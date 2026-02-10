"""Rules API endpoints."""

from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi_filter import FilterDepends

from app.auth.dependencies import require_role
from app.dependencies import DBSession
from app.filters.rule import RuleFilter
from app.repositories.rule_repository import DuplicateRuleError, RuleRepository
from app.schemas.rule import (
    RuleCreate,
    RuleListResponse,
    RuleResponse,
    RuleUpdate,
)
from app.utils.audit import audit_logged

router = APIRouter()

# Reusable Path() for rule code parameters — mirrors RuleCreate.code pattern.
_RULE_CODE = Path(pattern=r"^[A-Z]{2,4}_\d{3}$", description="Rule code (e.g. AMT_001)")


@router.get(
    "",
    response_model=RuleListResponse,
    dependencies=[Depends(require_role("admin", "analyst", "viewer"))],
)
async def list_rules(
    db: DBSession,
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
    repo = RuleRepository(db)
    rules, total = await repo.get_all(filters, page=page, size=size)

    return RuleListResponse(
        items=[RuleResponse.model_validate(r) for r in rules],
        total=total,
        page=page,
        size=size,
        pages=ceil(total / size) if size > 0 else 0,
    )


@router.get(
    "/{code}",
    response_model=RuleResponse,
    dependencies=[Depends(require_role("admin", "analyst", "viewer"))],
)
async def get_rule(
    code: str = _RULE_CODE,
    *,
    db: DBSession,
) -> RuleResponse:
    """Get a specific rule by code."""
    repo = RuleRepository(db)
    rule = await repo.get_by_code(code)

    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rule with code '{code}' not found",
        )

    return RuleResponse.model_validate(rule)


@router.post(
    "",
    response_model=RuleResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin")), Depends(audit_logged("create_rule"))],
)
async def create_rule(
    rule_data: RuleCreate,
    db: DBSession,
) -> RuleResponse:
    """Create a new fraud rule."""
    repo = RuleRepository(db)

    try:
        rule = await repo.create(rule_data)
    except DuplicateRuleError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Rule with code '{rule_data.code}' already exists",
        )

    return RuleResponse.model_validate(rule)


@router.put(
    "/{code}",
    response_model=RuleResponse,
    dependencies=[Depends(require_role("admin")), Depends(audit_logged("update_rule"))],
)
async def update_rule(
    code: str = _RULE_CODE,
    *,
    rule_data: RuleUpdate,
    db: DBSession,
) -> RuleResponse:
    """Update an existing rule."""
    repo = RuleRepository(db)
    rule = await repo.update(code, rule_data)

    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rule with code '{code}' not found",
        )

    return RuleResponse.model_validate(rule)


@router.delete(
    "/{code}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role("admin")), Depends(audit_logged("delete_rule"))],
)
async def delete_rule(
    code: str = _RULE_CODE,
    *,
    db: DBSession,
) -> None:
    """Soft delete a rule (disable it)."""
    repo = RuleRepository(db)
    success = await repo.delete(code)

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
    code: str = _RULE_CODE,
    *,
    db: DBSession,
) -> RuleResponse:
    """Toggle a rule's enabled state."""
    repo = RuleRepository(db)
    rule = await repo.toggle(code)

    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rule with code '{code}' not found",
        )

    return RuleResponse.model_validate(rule)
