"""Generate test transactions and publish to Kafka."""
import argparse
import asyncio
import json
import random
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from aiokafka import AIOKafkaProducer

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import get_settings


# Sample data for generating realistic transactions
CUSTOMERS = [str(uuid.uuid4()) for _ in range(10)]

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
