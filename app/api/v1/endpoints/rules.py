"""Rules API endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Security, status
from fastapi_filter import FilterDepends
from fastapi_pagination import Page
from fastapi_pagination.ext.sqlalchemy import paginate as sqlalchemy_paginate

from app.constants import RULE_CODE_PATTERN
from app.dependencies import get_current_user
from app.filters.rule import RuleFilter
from app.providers import RuleSvc
from app.repositories.rule_repository import DuplicateRuleError
from app.schemas.rule import (
    RuleCreate,
    RuleResponse,
    RuleUpdate,
)
from app.utils.audit import audit_logged

router = APIRouter()

# Reusable Path() for rule code parameters — mirrors RuleCreate.code pattern.
_RULE_CODE = Path(pattern=RULE_CODE_PATTERN, description="Rule code (e.g. AMT_001)")


@router.get(
    "",
    response_model=Page[RuleResponse],
    dependencies=[Security(get_current_user, scopes=["admin", "analyst", "viewer"])],
)
async def list_rules(
    service: RuleSvc,
    filters: Annotated[RuleFilter, FilterDepends(RuleFilter)],
) -> Page[RuleResponse]:
    """
    List all fraud rules with optional filtering.

    - **enabled**: Filter by enabled status (also enforces temporal bounds)
    - **category**: Filter by rule category
    - **order_by**: Sort fields (e.g. ``category``, ``-code``)
    """
    query = service.get_list_query(filters)
    return await sqlalchemy_paginate(service.session, query)  # type: ignore[no-any-return]


@router.get(
    "/{code}",
    response_model=RuleResponse,
    dependencies=[Security(get_current_user, scopes=["admin", "analyst", "viewer"])],
)
async def get_rule(
    service: RuleSvc,
    code: Annotated[str, _RULE_CODE],
) -> RuleResponse:
    """Get a specific rule by code."""
    result = await service.get_rule(code)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rule with code '{code}' not found",
        )
    return result


@router.post(
    "",
    response_model=RuleResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Security(get_current_user, scopes=["admin"]),
        Depends(audit_logged("create_rule")),
    ],
)
async def create_rule(
    rule_data: RuleCreate,
    service: RuleSvc,
) -> RuleResponse:
    """Create a new fraud rule."""
    try:
        return await service.create_rule(rule_data)
    except DuplicateRuleError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Rule with code '{rule_data.code}' already exists",
        ) from e


@router.put(
    "/{code}",
    response_model=RuleResponse,
    dependencies=[
        Security(get_current_user, scopes=["admin"]),
        Depends(audit_logged("update_rule")),
    ],
)
async def update_rule(
    service: RuleSvc,
    code: Annotated[str, _RULE_CODE],
    *,
    rule_data: RuleUpdate,
) -> RuleResponse:
    """Update an existing rule."""
    result = await service.update_rule(code, rule_data)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rule with code '{code}' not found",
        )
    return result


@router.delete(
    "/{code}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[
        Security(get_current_user, scopes=["admin"]),
        Depends(audit_logged("delete_rule")),
    ],
)
async def delete_rule(
    service: RuleSvc,
    code: Annotated[str, _RULE_CODE],
) -> None:
    """Soft delete a rule (disable it)."""
    success = await service.delete_rule(code)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rule with code '{code}' not found",
        )


@router.post(
    "/{code}/toggle",
    response_model=RuleResponse,
    dependencies=[
        Security(get_current_user, scopes=["admin"]),
        Depends(audit_logged("toggle_rule")),
    ],
)
async def toggle_rule(
    service: RuleSvc,
    code: Annotated[str, _RULE_CODE],
) -> RuleResponse:
    """Toggle a rule's enabled state."""
    result = await service.toggle_rule(code)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rule with code '{code}' not found",
        )
    return result
