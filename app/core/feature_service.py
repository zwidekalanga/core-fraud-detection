"""Feature service for enriching transactions with computed features."""

import json
import logging
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


@dataclass
class CustomerFeatures:
    """Computed features for a customer."""

    customer_id: str
    txn_count_5m: int = 0
    txn_count_10m: int = 0
    txn_count_1h: int = 0
    txn_count_24h: int = 0
    total_amount_24h: float = 0.0
    unique_merchants_24h: int = 0
    avg_amount: float = 0.0


class FeatureService:
    """
    Service for retrieving and computing transaction features.

    Features are cached in Redis for fast access during evaluation.
    If features are missing, they're computed on-demand.
    """

    def __init__(self, redis: Redis):
        self.redis = redis

    async def get_customer_features(self, customer_id: str) -> CustomerFeatures:
        """
        Get cached features for a customer.

        Returns default features if not cached.
        """
        cache_key = f"features:customer:{customer_id}"

        try:
            cached = await self.redis.get(cache_key)

            if cached:
                data = json.loads(cached)
                return CustomerFeatures(
                    customer_id=customer_id,
                    txn_count_5m=data.get("txn_count_5m", 0),
                    txn_count_10m=data.get("txn_count_10m", 0),
                    txn_count_1h=data.get("txn_count_1h", 0),
                    txn_count_24h=data.get("txn_count_24h", 0),
                    total_amount_24h=data.get("total_amount_24h", 0.0),
                    unique_merchants_24h=data.get("unique_merchants_24h", 0),
                    avg_amount=data.get("avg_amount", 0.0),
                )

            # Return default features if not cached
            logger.debug("No cached features for customer: %s", customer_id)
            return CustomerFeatures(customer_id=customer_id)

        except Exception as e:
            logger.warning(
                "Redis unavailable for customer features (customer=%s): %s", customer_id, e
            )
            return CustomerFeatures(customer_id=customer_id)

    async def get_merchant_risk(self, merchant_id: str) -> int:
        """
        Get cached risk score for a merchant.

        Returns 0 if not cached.
        """
        cache_key = f"merchant:risk:{merchant_id}"

        try:
            cached = await self.redis.get(cache_key)

            if cached:
                data = json.loads(cached)
                return data.get("risk_score", 0)

            return 0

        except Exception as e:
            logger.warning("Redis unavailable for merchant risk (merchant=%s): %s", merchant_id, e)
            return 0

    async def enrich_transaction(
        self,
        transaction: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Enrich transaction with computed features.

        Adds velocity features, merchant risk, and computed ratios.
        """
        customer_id = transaction.get("customer_id")
        merchant_id = transaction.get("merchant_id")
        amount = float(transaction.get("amount", 0))

        # Get customer features
        features = await self.get_customer_features(str(customer_id or ""))

        # Add velocity features
        transaction["txn_count_5m"] = features.txn_count_5m
        transaction["txn_count_10m"] = features.txn_count_10m
        transaction["txn_count_1h"] = features.txn_count_1h
        transaction["txn_count_24h"] = features.txn_count_24h
        transaction["total_amount_24h"] = features.total_amount_24h
        transaction["unique_merchants_24h"] = features.unique_merchants_24h

        # Add amount ratios
        if features.avg_amount > 0:
            transaction["amount_vs_avg_ratio"] = amount / features.avg_amount
        else:
            transaction["amount_vs_avg_ratio"] = 1.0

        # Get merchant risk if available
        if merchant_id:
            transaction["merchant_risk_score"] = await self.get_merchant_risk(merchant_id)

        return transaction
