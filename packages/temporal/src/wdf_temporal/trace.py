"""The W3C `traceparent` born at submit and carried on every event."""

import secrets
from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.trace import Span, Tracer
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from temporalio.contrib.opentelemetry import TracingInterceptor

_PROPAGATOR = TraceContextTextMapPropagator()


def new_traceparent() -> str:
    """A fresh sampled `traceparent`, created once at submit."""
    return f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"


def trace_id_of(traceparent: str) -> str:
    return traceparent.split("-")[1]


def current_traceparent() -> str:
    """The `traceparent` of the span that is current, for the event a stage emits."""
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    return carrier["traceparent"]


@contextmanager
def stage_span(tracer: Tracer, name: str, traceparent: str) -> Iterator[Span]:
    """A span for one stage, parented on the span that `traceparent` names."""
    context = _PROPAGATOR.extract({"traceparent": traceparent})
    with tracer.start_as_current_span(name, context=context) as span:
        yield span


def tracing_interceptor(tracer: Tracer | None = None) -> TracingInterceptor:
    """The Temporal interceptor that carries the trace context in workflow and activity headers."""
    return TracingInterceptor(tracer or trace.get_tracer("wdf"))
