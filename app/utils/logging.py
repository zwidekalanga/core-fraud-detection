"""Structured logging configuration using structlog."""

import logging
import sys
from typing import Any, Literal

import structlog


def setup_logging(
    log_level: str = "INFO",
    log_format: Literal["json", "console"] = "json",
) -> None:
    """Configure structured logging with structlog.

    Args:
        log_level: Standard Python log level name (INFO, DEBUG, etc.).
        log_format: Output format — ``"json"`` for machine-readable output
            (staging/production) or ``"console"`` for coloured human-readable
            output (local development).
    """
    renderer: structlog.types.Processor = (
        structlog.dev.ConsoleRenderer()
        if log_format == "console"
        else structlog.processors.JSONRenderer()
    )

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Also configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper(), logging.INFO),
    )

    # Reduce noise from third-party libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiokafka").setLevel(logging.WARNING)


def get_logger(name: str) -> Any:
    """Get a structlog logger."""
    return structlog.get_logger(name)
