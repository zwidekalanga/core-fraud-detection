"""Rules API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi_filter import FilterDepends

from app.auth.dependencies import require_role
from app.constants import RULE_CODE_PATTERN
from app.filters.rule import RuleFilter
from app.providers import RuleSvc
from app.repositories.rule_repository import DuplicateRuleError
from app.schemas.rule import (
    RuleCreate,
    RuleListResponse,
    RuleResponse,
    RuleUpdate,
)
from app.utils.audit import audit_logged

router = APIRouter()

# Reusable Path() for rule code parameters — mirrors RuleCreate.code pattern.
_RULE_CODE = Path(pattern=RULE_CODE_PATTERN, description="Rule code (e.g. AMT_001)")


@router.get(
    "",
    response_model=RuleListResponse,
    dependencies=[Depends(require_role("admin", "analyst", "viewer"))],
)
async def list_rules(
    service: RuleSvc,
    filters: RuleFilter = FilterDepends(RuleFilter),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
) -> RuleListResponse:
    """
    List all fraud rules with optional filtering.

    - **enabled**: Filter by enabled status (also enforces temporal bounds)
    - **category**: Filter by rule category
    - **order_by**: Sort fields (e.g. ``category``, ``-code``)
    """
    return await service.list_rules(filters, page=page, size=size)


@router.get(
    "/{code}",
    response_model=RuleResponse,
    dependencies=[Depends(require_role("admin", "analyst", "viewer"))],
)
async def get_rule(
    service: RuleSvc,
    code: str = _RULE_CODE,
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
    dependencies=[Depends(require_role("admin")), Depends(audit_logged("create_rule"))],
)
async def create_rule(
    rule_data: RuleCreate,
    service: RuleSvc,
) -> RuleResponse:
    """Create a new fraud rule."""
    try:
        return await service.create_rule(rule_data)
    except DuplicateRuleError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Rule with code '{rule_data.code}' already exists",
        )


@router.put(
    "/{code}",
    response_model=RuleResponse,
    dependencies=[Depends(require_role("admin")), Depends(audit_logged("update_rule"))],
)
async def update_rule(
    service: RuleSvc,
    code: str = _RULE_CODE,
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
    dependencies=[Depends(require_role("admin")), Depends(audit_logged("delete_rule"))],
)
async def delete_rule(
    service: RuleSvc,
    code: str = _RULE_CODE,
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
    dependencies=[Depends(require_role("admin")), Depends(audit_logged("toggle_rule"))],
)
async def toggle_rule(
    service: RuleSvc,
    code: str = _RULE_CODE,
) -> RuleResponse:
    """Toggle a rule's enabled state."""
    result = await service.toggle_rule(code)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rule with code '{code}' not found",
        )
    return result
