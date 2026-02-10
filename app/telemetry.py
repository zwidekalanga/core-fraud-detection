"""OpenTelemetry instrumentation stubs (M38).

Provides lightweight tracing hooks for gRPC and Kafka.  When the
``opentelemetry-api`` package is installed the helpers create real spans;
otherwise they silently degrade to no-ops so the rest of the application
runs without an OTel dependency.

To enable tracing, install the SDK packages::

    pip install opentelemetry-api opentelemetry-sdk opentelemetry-instrumentation-grpc

Then configure an exporter (Jaeger, OTLP, etc.) in your entrypoint before
calling ``init_telemetry()``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

import grpc.aio as grpc_aio

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Try to import OTel; fall back to no-ops
# ---------------------------------------------------------------------------
try:
    from opentelemetry import trace

    _HAS_OTEL = True
except ImportError:  # pragma: no cover
    _HAS_OTEL = False
    trace = None  # type: ignore[assignment]

_SERVICE_NAME = "core-fraud-detection"
_tracer: Any = None


def init_telemetry(service_name: str = _SERVICE_NAME) -> None:
    """Initialise the global tracer.

    Call once at application startup (e.g. in ``main.py``).  If the OTel SDK
    is not installed this is a silent no-op.
    """
    global _tracer
    if not _HAS_OTEL or trace is None:
        logger.debug("opentelemetry not installed — tracing disabled")
        return
    _tracer = trace.get_tracer(service_name)
    logger.info("OpenTelemetry tracer initialised for %s", service_name)


# ---------------------------------------------------------------------------
# Span helpers
# ---------------------------------------------------------------------------


@contextmanager
def span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[Any]:
    """Synchronous context-manager that wraps code in a trace span."""
    if _tracer is not None:
        with _tracer.start_as_current_span(name, attributes=attributes or {}) as s:
            yield s
    else:
        yield None


@asynccontextmanager
async def aspan(name: str, attributes: dict[str, Any] | None = None) -> AsyncIterator[Any]:
    """Async context-manager that wraps code in a trace span."""
    if _tracer is not None:
        with _tracer.start_as_current_span(name, attributes=attributes or {}) as s:
            yield s
    else:
        yield None


# ---------------------------------------------------------------------------
# gRPC server interceptor stub
# ---------------------------------------------------------------------------


class TracingInterceptor(grpc_aio.ServerInterceptor):
    """Unary-unary gRPC server interceptor that creates a span per RPC.

    If OTel is not installed the interceptor still works but records no spans.
    """

    async def intercept_service(self, continuation, handler_call_details):
        method = handler_call_details.method
        start = time.perf_counter()
        if _tracer is not None:
            with _tracer.start_as_current_span(
                f"grpc {method}",
                attributes={"rpc.method": method, "rpc.system": "grpc"},
            ):
                response = await continuation(handler_call_details)
        else:
            response = await continuation(handler_call_details)

        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.debug("grpc.%s completed in %.1fms", method, elapsed_ms)
        return response


# ---------------------------------------------------------------------------
# Kafka consumer tracing helper
# ---------------------------------------------------------------------------


@asynccontextmanager
async def kafka_span(
    topic: str,
    partition: int,
    offset: int,
) -> AsyncIterator[Any]:
    """Wrap Kafka message processing in a trace span."""
    attrs = {
        "messaging.system": "kafka",
        "messaging.destination": topic,
        "messaging.kafka.partition": partition,
        "messaging.kafka.offset": offset,
    }
    async with aspan(f"kafka.process {topic}", attributes=attrs) as s:
        yield s
