"""Service layer for fraud rule management."""

from typing import Any

from sqlalchemy import Select

from app.filters.rule import RuleFilter
from app.repositories.rule_repository import RuleRepository
from app.schemas.rule import (
    RuleCreate,
    RuleResponse,
    RuleUpdate,
)


class RuleService:
    """Version-agnostic business logic for fraud rules."""

    def __init__(self, repo: RuleRepository):
        self._repo = repo

    @property
    def session(self) -> Any:
        """Expose the repo's session for sqlalchemy_paginate."""
        return self._repo.session

    def get_list_query(self, filters: RuleFilter) -> Select[Any]:
        """Return a filtered query — pagination handled by the library."""
        return self._repo.get_list_query(filters)

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
