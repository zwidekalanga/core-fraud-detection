"""Repository for transaction data access."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction
from app.schemas.transaction import TransactionEvaluateRequest


class TransactionRepository:
    """Data access layer for transactions."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_external_id(self, external_id: str) -> Transaction | None:
        """Get a transaction by external ID."""
        query = select(Transaction).where(Transaction.external_id == external_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_by_id(self, transaction_id: str) -> Transaction | None:
        """Get a transaction by ID."""
        query = select(Transaction).where(Transaction.id == transaction_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def create(self, txn_data: TransactionEvaluateRequest) -> Transaction:
        """Create a new transaction record."""
        transaction = Transaction(
            external_id=txn_data.external_id,
            customer_id=txn_data.customer_id,
            amount=txn_data.amount,
            currency=txn_data.currency,
            transaction_type=txn_data.transaction_type,
            channel=txn_data.channel,
            merchant_id=txn_data.merchant_id,
            merchant_name=txn_data.merchant_name,
            merchant_category=txn_data.merchant_category,
            location_country=txn_data.location_country,
            location_city=txn_data.location_city,
            device_fingerprint=txn_data.device_fingerprint,
            ip_address=txn_data.ip_address,
            transaction_time=txn_data.transaction_time or datetime.now(UTC),
            extra_data=txn_data.extra_data,
        )
        self.session.add(transaction)
        await self.session.flush()
        return transaction

    async def upsert(self, txn_data: TransactionEvaluateRequest) -> Transaction:
        """Insert or ignore a transaction (idempotent on external_id).

        Returns the existing or newly-created transaction. Raises
        ``RuntimeError`` if the row cannot be fetched after the upsert
        (should not happen under normal operation).
        """
        stmt = (
            insert(Transaction)
            .values(
                external_id=txn_data.external_id,
                customer_id=txn_data.customer_id,
                amount=txn_data.amount,
                currency=txn_data.currency,
                transaction_type=txn_data.transaction_type,
                channel=txn_data.channel,
                merchant_id=txn_data.merchant_id,
                merchant_name=txn_data.merchant_name,
                merchant_category=txn_data.merchant_category,
                location_country=txn_data.location_country,
                location_city=txn_data.location_city,
                device_fingerprint=txn_data.device_fingerprint,
                ip_address=txn_data.ip_address,
                transaction_time=txn_data.transaction_time or datetime.now(UTC),
                extra_data=txn_data.extra_data,
            )
            .on_conflict_do_nothing(index_elements=["external_id"])
        )

        await self.session.execute(stmt)
        await self.session.flush()

        # Fetch the record (either new or existing)
        txn = await self.get_by_external_id(txn_data.external_id)
        if txn is None:
            raise RuntimeError(
                f"Transaction upsert succeeded but fetch failed for "
                f"external_id={txn_data.external_id}"
            )
        return txn
