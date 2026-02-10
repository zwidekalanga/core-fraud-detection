"""Service layer for fraud rule management."""

from math import ceil

from app.filters.rule import RuleFilter
from app.repositories.rule_repository import DuplicateRuleError, RuleRepository
from app.schemas.rule import (
    RuleCreate,
    RuleListResponse,
    RuleResponse,
    RuleUpdate,
)


class RuleService:
    """Version-agnostic business logic for fraud rules."""

    def __init__(self, repo: RuleRepository):
        self._repo = repo

    async def list_rules(
        self,
        filters: RuleFilter,
        page: int = 1,
        size: int = 50,
    ) -> RuleListResponse:
        rules, total = await self._repo.get_all(filters, page=page, size=size)
        return RuleListResponse(
            items=[RuleResponse.model_validate(r) for r in rules],
            total=total,
            page=page,
            size=size,
            pages=ceil(total / size) if size > 0 else 0,
        )

    async def get_rule(self, code: str) -> RuleResponse | None:
        rule = await self._repo.get_by_code(code)
        if rule is None:
            return None
        return RuleResponse.model_validate(rule)

    async def create_rule(self, rule_data: RuleCreate) -> RuleResponse:
        """Create a new fraud rule.

        Raises:
            DuplicateRuleError: If a rule with the same code already exists.
        """
        rule = await self._repo.create(rule_data)
        return RuleResponse.model_validate(rule)

    async def update_rule(self, code: str, rule_data: RuleUpdate) -> RuleResponse | None:
        rule = await self._repo.update(code, rule_data)
        if rule is None:
            return None
        return RuleResponse.model_validate(rule)

    async def delete_rule(self, code: str) -> bool:
        return await self._repo.delete(code)

    async def toggle_rule(self, code: str) -> RuleResponse | None:
        rule = await self._repo.toggle(code)
        if rule is None:
            return None
        return RuleResponse.model_validate(rule)
