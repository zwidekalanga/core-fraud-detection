"""Celery tasks for feature computation."""

import json
import logging
from datetime import UTC, datetime, timedelta

from redis import Redis
from sqlalchemy import func, select

from app.config import get_settings
from app.models.transaction import Transaction
from app.tasks.celery_app import celery_app
from app.tasks.notifications import get_sync_session

logger = logging.getLogger(__name__)


def get_redis_client() -> Redis:
    """Get synchronous Redis client."""
    settings = get_settings()
    # Use sync Redis for Celery
    redis_url = str(settings.redis_url)
    return Redis.from_url(redis_url, decode_responses=True)


@celery_app.task
def compute_customer_features(customer_id: str) -> dict[str, object]:
    """
    Compute and cache velocity features for a customer.

    Features computed:
    - Transaction count in last 5m, 10m, 1h, 24h
    - Total amount in last 24h
    - Unique merchants in last 24h
    - Average transaction amount

    Results are cached in Redis with 5 minute TTL.
    """
    logger.info(f"Computing features for customer: {customer_id}")

    session = get_sync_session()
    redis = get_redis_client()

    try:
        now = datetime.now(UTC)

        # Time windows
        windows = {
            "5m": timedelta(minutes=5),
            "10m": timedelta(minutes=10),
            "1h": timedelta(hours=1),
            "24h": timedelta(hours=24),
        }

        features: dict[str, object] = {"customer_id": customer_id, "computed_at": now.isoformat()}

        # Transaction counts by window
        for window_name, delta in windows.items():
            cutoff = now - delta
            count = (
                session.scalar(
                    select(func.count())
                    .select_from(Transaction)
                    .where(
                        Transaction.customer_id == customer_id,
                        Transaction.transaction_time >= cutoff,
                    )
                )
                or 0
            )
            features[f"txn_count_{window_name}"] = count

        # Total amount in 24h
        total_24h = (
            session.scalar(
                select(func.sum(Transaction.amount)).where(
                    Transaction.customer_id == customer_id,
                    Transaction.transaction_time >= now - timedelta(hours=24),
                )
            )
            or 0
        )
        features["total_amount_24h"] = float(total_24h)

        # Unique merchants in 24h
        unique_merchants = (
            session.scalar(
                select(func.count(func.distinct(Transaction.merchant_id))).where(
                    Transaction.customer_id == customer_id,
                    Transaction.transaction_time >= now - timedelta(hours=24),
                    Transaction.merchant_id.isnot(None),
                )
            )
            or 0
        )
        features["unique_merchants_24h"] = unique_merchants

        # Average amount (all time)
        avg_amount = (
            session.scalar(
                select(func.avg(Transaction.amount)).where(
                    Transaction.customer_id == customer_id,
                )
            )
            or 0
        )
        features["avg_amount"] = float(avg_amount)

        # Cache in Redis
        cache_key = f"features:customer:{customer_id}"
        redis.setex(cache_key, 300, json.dumps(features))  # 5 minute TTL

        logger.info(f"Cached features for customer {customer_id}")
        return features

    finally:
        session.close()
        redis.close()


@celery_app.task
def refresh_expired_features() -> dict[str, int]:
    """
    Background task to refresh features for active customers.

    Finds customers with recent transactions and pre-computes their features.
    """
    logger.info("Refreshing customer features...")

    session = get_sync_session()

    try:
        # Find customers with transactions in the last hour
        cutoff = datetime.now(UTC) - timedelta(hours=1)

        active_customers = (
            session.execute(
                select(Transaction.customer_id)
                .distinct()
                .where(Transaction.transaction_time >= cutoff)
            )
            .scalars()
            .all()
        )

        refreshed = 0
        for customer_id in active_customers:
            compute_customer_features.delay(customer_id)  # pyright: ignore[reportFunctionMemberAccess]
            refreshed += 1

        logger.info(f"Queued feature refresh for {refreshed} customers")
        return {"queued": refreshed}

    finally:
        session.close()


@celery_app.task
def compute_merchant_risk_score(merchant_id: str) -> dict[str, object]:
    """
    Compute fraud risk score for a merchant.

    Based on:
    - Historical fraud rate
    - Alert frequency
    - Average alert score
    """
    logger.info(f"Computing risk for merchant: {merchant_id}")

    session = get_sync_session()
    redis = get_redis_client()

    try:
        from app.models.alert import FraudAlert

        # Count total transactions for merchant
        total_txns = (
            session.scalar(
                select(func.count())
                .select_from(Transaction)
                .where(
                    Transaction.merchant_id == merchant_id,
                )
            )
            or 0
        )

        if total_txns == 0:
            return {
                "merchant_id": merchant_id,
                "risk_score": 0,
                "total_transactions": 0,
            }

        # Count transactions that resulted in alerts
        alert_count = (
            session.scalar(
                select(func.count())
                .select_from(FraudAlert)
                .join(Transaction, FraudAlert.transaction_id == Transaction.id)
                .where(Transaction.merchant_id == merchant_id)
            )
            or 0
        )

        # Calculate fraud rate
        fraud_rate = alert_count / total_txns if total_txns > 0 else 0

        # Compute risk score (0-100)
        # Scale up since fraud rates are typically small percentages
        risk_score = min(int(fraud_rate * 1000), 100)

        result = {
            "merchant_id": merchant_id,
            "total_transactions": total_txns,
            "alert_count": alert_count,
            "fraud_rate": round(fraud_rate, 4),
            "risk_score": risk_score,
        }

        # Cache result
        cache_key = f"merchant:risk:{merchant_id}"
        redis.setex(cache_key, 3600, json.dumps(result))  # 1 hour TTL

        logger.info(f"Computed merchant risk for {merchant_id}: {risk_score}")
        return result

    finally:
        session.close()
        redis.close()


@celery_app.task
def compute_all_merchant_risks() -> dict[str, int]:
    """
    Compute risk scores for all merchants with transactions.
    """
    logger.info("Computing risk scores for all merchants...")

    session = get_sync_session()

    try:
        # Get all unique merchants
        merchants = (
            session.execute(
                select(Transaction.merchant_id)
                .distinct()
                .where(Transaction.merchant_id.isnot(None))
            )
            .scalars()
            .all()
        )

        computed = 0
        for merchant_id in merchants:
            compute_merchant_risk_score.delay(merchant_id)  # pyright: ignore[reportFunctionMemberAccess]
            computed += 1

        logger.info(f"Queued risk computation for {computed} merchants")
        return {"queued": computed}

    finally:
        session.close()
