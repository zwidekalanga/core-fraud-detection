"""Repository for fraud rule data access."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rule import FraudRule, RuleCategory
from app.schemas.rule import RuleCreate, RuleUpdate


class RuleRepository:
    """Data access layer for fraud rules."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all(
        self,
        *,
        enabled_only: bool = False,
        category: RuleCategory | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[FraudRule], int]:
        """Get all rules with optional filtering and pagination."""
        query = select(FraudRule)
        count_query = select(func.count()).select_from(FraudRule)

        # Apply filters
        if enabled_only:
            now = datetime.now(UTC)
            query = query.where(
                FraudRule.enabled == True,  # noqa: E712
                (FraudRule.effective_from.is_(None)) | (FraudRule.effective_from <= now),
                (FraudRule.effective_to.is_(None)) | (FraudRule.effective_to >= now),
            )
            count_query = count_query.where(FraudRule.enabled == True)  # noqa: E712

        if category:
            query = query.where(FraudRule.category == category.value)
            count_query = count_query.where(FraudRule.category == category.value)

        # Get total count
        total = await self.session.scalar(count_query) or 0

        # Apply pagination
        query = query.order_by(FraudRule.category, FraudRule.code)
        query = query.offset((page - 1) * size).limit(size)

        result = await self.session.execute(query)
        rules = list(result.scalars().all())

        return rules, total

    async def get_by_code(self, code: str) -> FraudRule | None:
        """Get a rule by its code."""
        query = select(FraudRule).where(FraudRule.code == code)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_enabled_rules(self) -> list[FraudRule]:
        """Get all currently enabled and effective rules."""
        now = datetime.now(UTC)
        query = select(FraudRule).where(
            FraudRule.enabled == True,  # noqa: E712
            (FraudRule.effective_from.is_(None)) | (FraudRule.effective_from <= now),
            (FraudRule.effective_to.is_(None)) | (FraudRule.effective_to >= now),
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def create(self, rule_data: RuleCreate) -> FraudRule:
        """Create a new rule."""
        rule = FraudRule(
            code=rule_data.code,
            name=rule_data.name,
            description=rule_data.description,
            category=rule_data.category.value,
            severity=rule_data.severity.value,
            score=rule_data.score,
            enabled=rule_data.enabled,
            conditions=rule_data.conditions,
            effective_from=rule_data.effective_from,
            effective_to=rule_data.effective_to,
        )
        self.session.add(rule)
        await self.session.commit()
        await self.session.refresh(rule)
        return rule

    async def update(self, code: str, rule_data: RuleUpdate) -> FraudRule | None:
        """Update an existing rule."""
        rule = await self.get_by_code(code)
        if not rule:
            return None

        # Update only provided fields
        update_data = rule_data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            if field in ("category", "severity") and value is not None:
                value = value.value if hasattr(value, "value") else value
            setattr(rule, field, value)

        await self.session.commit()
        await self.session.refresh(rule)
        return rule

    async def delete(self, code: str) -> bool:
        """Soft delete a rule by disabling it."""
        rule = await self.get_by_code(code)
        if not rule:
            return False

        rule.enabled = False
        await self.session.commit()
        return True

    async def toggle(self, code: str) -> FraudRule | None:
        """Toggle a rule's enabled state."""
        rule = await self.get_by_code(code)
        if not rule:
            return None

        rule.enabled = not rule.enabled
        await self.session.commit()
        await self.session.refresh(rule)
        return rule
