"""Seed default fraud rules into the database."""
import asyncio
import sys
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import get_settings
from app.models.rule import FraudRule


async def seed_rules(session: AsyncSession) -> None:
    """Load rules from YAML and insert into database."""
    rules_path = Path(__file__).parent.parent / "rules" / "default_rules.yaml"

    if not rules_path.exists():
        print(f"Rules file not found: {rules_path}")
        return

    with open(rules_path) as f:
        data = yaml.safe_load(f)

    rules_data = data.get("rules", [])

    inserted = 0
    skipped = 0

    for rule_data in rules_data:
        code = rule_data["code"]

        # Check if rule already exists
        result = await session.execute(
            select(FraudRule).where(FraudRule.code == code)
        )
        existing = result.scalar_one_or_none()

        if existing:
            print(f"  Skipping {code} (already exists)")
            skipped += 1
            continue

        # Create new rule
        rule = FraudRule(
            code=code,
            name=rule_data["name"],
            description=rule_data.get("description"),
            category=rule_data["category"],
            severity=rule_data["severity"],
            score=rule_data["score"],
            enabled=rule_data.get("enabled", True),
            conditions=rule_data["conditions"],
        )
        session.add(rule)
        print(f"  Inserted {code}: {rule_data['name']}")
        inserted += 1

    await session.commit()
    print(f"\nSummary: {inserted} inserted, {skipped} skipped")


async def main() -> None:
    """Main entry point."""
    print("Seeding fraud rules...")

    settings = get_settings()
    engine = create_async_engine(str(settings.database_url))
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        await seed_rules(session)

    await engine.dispose()
    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
