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
            account_id=txn_data.account_id,
            account_number=txn_data.account_number,
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
        """Insert or return existing transaction (idempotent on external_id).

        Uses ``ON CONFLICT DO UPDATE ... RETURNING`` to fetch the row in a
        single round-trip instead of INSERT + SELECT.
        """
        values = dict(
            external_id=txn_data.external_id,
            customer_id=txn_data.customer_id,
            account_id=txn_data.account_id,
            account_number=txn_data.account_number,
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

        stmt = (
            insert(Transaction)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["external_id"],
                # No-op SET so RETURNING works on the existing row
                set_={"external_id": txn_data.external_id},
            )
            .returning(Transaction)
        )

        result = await self.session.execute(stmt)
        txn = result.scalar_one()
        await self.session.flush()
        return txn
