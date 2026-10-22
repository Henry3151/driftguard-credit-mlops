"""OpenTelemetry opcional: exporta spans se DRIFTGUARD_OTEL_ENABLED=true e a lib existir.

Sem OTel, o trace_id/span_id continuam presentes nos logs JSON (correlação preservada).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from typing import Any

_tracer = None


def _get_tracer():  # pragma: no cover - depende de lib opcional
    global _tracer
    if _tracer is not None:
        return _tracer
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    provider = TracerProvider(resource=Resource.create({"service.name": "driftguard-credit-mlops"}))
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    except ImportError:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("driftguard")
    return _tracer


@contextmanager
def span(name: str, trace_attrs: dict[str, Any] | None = None) -> Iterator[None]:
    from driftguard.adapters import available, note_skipped
    from driftguard.settings import get_settings

    if not get_settings().otel_enabled:
        with nullcontext():
            yield
        return
    if not available("opentelemetry.sdk"):
        note_skipped("opentelemetry", "DRIFTGUARD_OTEL_ENABLED=true mas opentelemetry-sdk não instalado")
        yield
        return
    tracer = _get_tracer()  # pragma: no cover
    with tracer.start_as_current_span(name) as sp:  # pragma: no cover
        for k, v in (trace_attrs or {}).items():
            sp.set_attribute(k, str(v))
        yield
