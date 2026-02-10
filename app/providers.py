"""FastAPI dependency providers for repositories and services.

Separated from ``dependencies.py`` to break the circular-import risk
(A01) caused by late ``# noqa: E402`` imports at the bottom of that
module.  Route modules should import type aliases from here.
"""

from typing import Annotated

from fastapi import Depends

from app.dependencies import DBSession
from app.repositories.alert_repository import AlertRepository
from app.repositories.config_repository import ConfigRepository
from app.repositories.rule_repository import RuleRepository
from app.services.alert_service import AlertService
from app.services.config_service import ConfigService
from app.services.rule_service import RuleService

# ---------------------------------------------------------------------------
# Repository providers
# ---------------------------------------------------------------------------


def get_rule_repository(db: DBSession) -> RuleRepository:
    return RuleRepository(db)


def get_alert_repository(db: DBSession) -> AlertRepository:
    return AlertRepository(db)


def get_config_repository(db: DBSession) -> ConfigRepository:
    return ConfigRepository(db)


RuleRepo = Annotated[RuleRepository, Depends(get_rule_repository)]
AlertRepo = Annotated[AlertRepository, Depends(get_alert_repository)]
ConfigRepo = Annotated[ConfigRepository, Depends(get_config_repository)]

# ---------------------------------------------------------------------------
# Service providers
# ---------------------------------------------------------------------------


def get_rule_service(repo: RuleRepo) -> RuleService:
    return RuleService(repo)


def get_alert_service(repo: AlertRepo) -> AlertService:
    return AlertService(repo)


def get_config_service(repo: ConfigRepo) -> ConfigService:
    return ConfigService(repo)


RuleSvc = Annotated[RuleService, Depends(get_rule_service)]
AlertSvc = Annotated[AlertService, Depends(get_alert_service)]
ConfigSvc = Annotated[ConfigService, Depends(get_config_service)]
