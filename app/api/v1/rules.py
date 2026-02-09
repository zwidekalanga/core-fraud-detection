"""Rules API endpoints."""

from math import ceil

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import require_role
from app.dependencies import DBSession
from app.models.rule import RuleCategory
from app.repositories.rule_repository import RuleRepository
from app.schemas.rule import (
    RuleCreate,
    RuleListResponse,
    RuleResponse,
    RuleUpdate,
)

router = APIRouter()


@router.get(
    "",
    response_model=RuleListResponse,
    dependencies=[Depends(require_role("admin", "analyst", "viewer"))],
)
async def list_rules(
    db: DBSession,
    enabled: bool | None = None,
    category: RuleCategory | None = None,
    page: int = 1,
    size: int = 50,
) -> RuleListResponse:
    """
    List all fraud rules with optional filtering.

    - **enabled**: Filter by enabled status
    - **category**: Filter by rule category
    - **page**: Page number (1-indexed)
    - **size**: Items per page (max 100)
    """
    size = min(size, 100)  # Cap at 100

    repo = RuleRepository(db)
    rules, total = await repo.get_all(
        enabled_only=enabled if enabled else False,
        category=category,
        page=page,
        size=size,
    )

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
    code: str,
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
    dependencies=[Depends(require_role("admin"))],
)
async def create_rule(
    rule_data: RuleCreate,
    db: DBSession,
) -> RuleResponse:
    """Create a new fraud rule."""
    repo = RuleRepository(db)

    # Check if rule already exists
    existing = await repo.get_by_code(rule_data.code)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Rule with code '{rule_data.code}' already exists",
        )

    rule = await repo.create(rule_data)
    return RuleResponse.model_validate(rule)


@router.put(
    "/{code}",
    response_model=RuleResponse,
    dependencies=[Depends(require_role("admin"))],
)
async def update_rule(
    code: str,
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
    dependencies=[Depends(require_role("admin"))],
)
async def delete_rule(
    code: str,
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
    dependencies=[Depends(require_role("admin"))],
)
async def toggle_rule(
    code: str,
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
