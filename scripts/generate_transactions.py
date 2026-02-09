"""Generate test transactions and publish to Kafka."""
import argparse
import asyncio
import json
import logging
import random
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from aiokafka import AIOKafkaProducer

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Banking DB connection string — same Postgres host, different database.
# ---------------------------------------------------------------------------
BANKING_DATABASE_URL = (
    "postgresql+asyncpg://postgres:postgres@postgres:5432/core_banking"
)

# Fallback random customer IDs (used only when the banking DB is unreachable)
_FALLBACK_CUSTOMERS = [str(uuid.uuid4()) for _ in range(10)]

# Will be populated at runtime by `_load_customer_ids()`
CUSTOMERS: list[str] = []


async def _load_customer_ids() -> list[str]:
    """Fetch real customer IDs from the core_banking database.

    Falls back to random UUIDs when the banking DB is unavailable (e.g. tests).
    """
    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        engine = create_async_engine(BANKING_DATABASE_URL, pool_size=2)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with factory() as session:
            result = await session.execute(text("SELECT id FROM customers"))
            ids = [str(row[0]) for row in result.all()]

        await engine.dispose()

        if ids:
            logger.info("Loaded %d customer IDs from core_banking", len(ids))
            return ids
        else:
            logger.warning("No customers found in core_banking — using fallback UUIDs")
            return _FALLBACK_CUSTOMERS

    except Exception as exc:
        logger.warning("Cannot reach core_banking DB (%s) — using fallback UUIDs", exc)
        return _FALLBACK_CUSTOMERS

CHANNELS = ["card", "eft", "mobile", "online", "atm"]

TRANSACTION_TYPES = [
    "CARD_PRESENT",
    "CARD_NOT_PRESENT",
    "EFT",
    "INTERNAL_TRANSFER",
    "ATM_WITHDRAWAL",
]

MERCHANTS = [
    {"id": "M001", "name": "Woolworths", "category": "grocery"},
    {"id": "M002", "name": "Checkers", "category": "grocery"},
    {"id": "M003", "name": "Takealot", "category": "online_retail"},
    {"id": "M004", "name": "Game", "category": "electronics"},
    {"id": "M005", "name": "Uber", "category": "transport"},
    {"id": "M006", "name": "Netflix", "category": "subscription"},
    {"id": "M007", "name": "Shell", "category": "fuel"},
    {"id": "M008", "name": "Lucky Star Casino", "category": "gambling"},
    {"id": "M009", "name": "CryptoExchange", "category": "crypto"},
]

COUNTRIES = {
    "normal": ["ZA", "ZA", "ZA", "ZA", "ZA", "BW", "NA", "MZ"],  # Mostly SA
    "high_risk": ["NG", "GH", "KE", "RU"],
    "sanctioned": ["IR", "KP", "SY"],
}


def generate_transaction(high_risk: bool = False) -> dict:
    """Generate a random transaction."""
    customer_id = random.choice(CUSTOMERS)
    merchant = random.choice(MERCHANTS)

    # Determine amount
    if high_risk:
        # Higher amounts for high-risk transactions
        amount = random.choice([
            random.uniform(50000, 150000),  # Very high
            random.uniform(10000, 50000),  # High
        ])
    else:
        amount = random.choice([
            random.uniform(10, 500),  # Small purchase
            random.uniform(500, 2000),  # Medium purchase
            random.uniform(2000, 10000),  # Large purchase
        ])

    # Determine country
    if high_risk:
        countries = COUNTRIES["high_risk"] + COUNTRIES["sanctioned"]
        country = random.choice(countries)
    else:
        country = random.choice(COUNTRIES["normal"])

    # Determine channel
    channel = random.choice(CHANNELS)

    return {
        "external_id": f"TXN-{uuid.uuid4().hex[:8].upper()}",
        "customer_id": customer_id,
        "amount": round(amount, 2),
        "currency": "ZAR",
        "transaction_type": random.choice(TRANSACTION_TYPES),
        "channel": channel,
        "merchant_id": merchant["id"],
        "merchant_name": merchant["name"],
        "merchant_category": merchant["category"],
        "location_country": country,
        "location_city": "Cape Town" if country == "ZA" else None,
        "device_fingerprint": (
            f"fp_{uuid.uuid4().hex[:12]}" if random.random() > 0.1 else None
        ),
        "ip_address": (
            f"{random.randint(1, 255)}.{random.randint(1, 255)}."
            f"{random.randint(1, 255)}.{random.randint(1, 255)}"
        ),
        "transaction_time": datetime.now(timezone.utc).isoformat(),
        "extra_data": {},
    }


async def produce_transactions(count: int, high_risk_pct: float = 0.2) -> None:
    """Generate and publish transactions to Kafka."""
    global CUSTOMERS  # noqa: PLW0603
    CUSTOMERS = await _load_customer_ids()

    settings = get_settings()

    producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        value_serializer=lambda m: json.dumps(m).encode("utf-8"),
    )

    await producer.start()
    print(f"Connected to Kafka: {settings.kafka_bootstrap_servers}")
    print(f"Generating {count} transactions ({high_risk_pct * 100:.0f}% high-risk)...")

    try:
        for i in range(count):
            high_risk = random.random() < high_risk_pct
            txn = generate_transaction(high_risk=high_risk)

            await producer.send_and_wait("transactions.raw", txn)

            risk_indicator = "[HIGH-RISK]" if high_risk else "[NORMAL]"
            print(
                f"{risk_indicator} [{i + 1}/{count}] "
                f"{txn['external_id']}: R{txn['amount']:,.2f} "
                f"({txn['channel']}, {txn['location_country']})"
            )

            # Small delay between messages
            await asyncio.sleep(0.1)

        print(f"\nPublished {count} transactions to transactions.raw")

    finally:
        await producer.stop()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Generate test transactions")
    parser.add_argument(
        "--count",
        "-c",
        type=int,
        default=20,
        help="Number of transactions to generate",
    )
    parser.add_argument(
        "--high-risk",
        type=float,
        default=0.2,
        help="Percentage of high-risk transactions (0.0-1.0)",
    )

    args = parser.parse_args()
    await produce_transactions(args.count, args.high_risk)


if __name__ == "__main__":
    asyncio.run(main())
