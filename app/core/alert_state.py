"""State pattern for alert lifecycle management.

Each ``AlertState`` subclass encodes which transitions are valid from that
state and provides an ``on_enter`` hook for side-effects (logging,
notifications, metrics) that should fire when an alert enters that state.

Usage::

    state = AlertStateMachine.get_state(alert.status)
    state.validate_transition(alert_id, target_status)   # raises ValueError if invalid
    new_state = AlertStateMachine.get_state(target_status)
    new_state.on_enter(alert)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from app.models.alert import AlertStatus, FraudAlert

logger = logging.getLogger(__name__)


class AlertState(ABC):
    """Base class for alert states."""

    @abstractmethod
    def allowed_transitions(self) -> set[AlertStatus]:
        """Return the set of statuses this state can transition to."""
        ...

    def can_transition_to(self, target: AlertStatus) -> bool:
        """Check whether transitioning to *target* is permitted."""
        return target in self.allowed_transitions()

    def validate_transition(self, alert_id: str, target: AlertStatus) -> None:
        """Raise ``ValueError`` if the transition is not allowed."""
        allowed = self.allowed_transitions()
        if target not in allowed:
            allowed_str = (
                ", ".join(s.value for s in allowed) if allowed else "none (terminal state)"
            )
            raise ValueError(
                f"Cannot transition alert {alert_id} from "
                f"'{self.status_name}' to '{target.value}'. "
                f"Allowed transitions: {{{allowed_str}}}"
            )

    @abstractmethod
    def on_enter(self, alert: FraudAlert) -> None:
        """Side-effects to execute when an alert enters this state."""
        ...

    @property
    @abstractmethod
    def status_name(self) -> str:
        """The string name of this state (matches ``AlertStatus.value``)."""
        ...


class PendingState(AlertState):
    """Initial state — alert awaits analyst review."""

    @property
    def status_name(self) -> str:
        return AlertStatus.PENDING.value

    def allowed_transitions(self) -> set[AlertStatus]:
        return {AlertStatus.CONFIRMED, AlertStatus.DISMISSED, AlertStatus.ESCALATED}

    def on_enter(self, alert: FraudAlert) -> None:
        pass  # Default state — no side-effects


class EscalatedState(AlertState):
    """Alert escalated for senior review."""

    @property
    def status_name(self) -> str:
        return AlertStatus.ESCALATED.value

    def allowed_transitions(self) -> set[AlertStatus]:
        return {AlertStatus.CONFIRMED, AlertStatus.DISMISSED}

    def on_enter(self, alert: FraudAlert) -> None:
        logger.info(
            "Alert %s escalated (score=%d) — requires senior analyst review",
            alert.id,
            alert.risk_score,
        )


class ConfirmedState(AlertState):
    """Terminal state — alert confirmed as fraud."""

    @property
    def status_name(self) -> str:
        return AlertStatus.CONFIRMED.value

    def allowed_transitions(self) -> set[AlertStatus]:
        return set()  # Terminal

    def on_enter(self, alert: FraudAlert) -> None:
        logger.info("Alert %s confirmed as fraud by %s", alert.id, alert.reviewed_by)


class DismissedState(AlertState):
    """Terminal state — alert dismissed as false positive."""

    @property
    def status_name(self) -> str:
        return AlertStatus.DISMISSED.value

    def allowed_transitions(self) -> set[AlertStatus]:
        return set()  # Terminal

    def on_enter(self, alert: FraudAlert) -> None:
        logger.info("Alert %s dismissed by %s", alert.id, alert.reviewed_by)


class AlertStateMachine:
    """Registry that maps ``AlertStatus`` values to their ``AlertState``."""

    _states: dict[str, AlertState] = {
        AlertStatus.PENDING.value: PendingState(),
        AlertStatus.ESCALATED.value: EscalatedState(),
        AlertStatus.CONFIRMED.value: ConfirmedState(),
        AlertStatus.DISMISSED.value: DismissedState(),
    }

    @classmethod
    def get_state(cls, status: str | AlertStatus) -> AlertState:
        """Return the ``AlertState`` for the given status value.

        Raises:
            ValueError: If *status* is not a recognised alert status.
        """
        key = status.value if isinstance(status, AlertStatus) else status
        state = cls._states.get(key)
        if state is None:
            raise ValueError(f"Unknown alert status: '{key}'")
        return state
