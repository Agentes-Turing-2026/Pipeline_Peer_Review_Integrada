"""Base de observabilidade e traces do pipeline (Grupo 3).

Ponto único de importação para toda a instrumentação. Os outros grupos precisam
apenas de :func:`emit_event` (evento pontual) e/ou :func:`trace_span` (sub-span),
que se ancoram automaticamente na execução em curso.

Exemplo (Grupo 1 ou Grupo 2)::

    from observability import emit_event
    emit_event("validacao_ok", author="grupo1", attributes={"tentativas": 1})
"""

from __future__ import annotations

from .adk_bridge import trace_adk_event
from .events import EventType, SpanKind, Status, TraceEvent, new_id
from .timeline import (
    build_timeline,
    load_events,
    print_timeline,
    render_timeline,
    resumir_tokens,
)
from .tracer import (
    Exporter,
    JsonlExporter,
    MemoryExporter,
    Tracer,
    create_tracer,
    emit_event,
    get_current_tracer,
    trace_span,
)

__all__ = [
    # formato comum
    "TraceEvent",
    "SpanKind",
    "EventType",
    "Status",
    "new_id",
    # motor
    "Tracer",
    "Exporter",
    "JsonlExporter",
    "MemoryExporter",
    "create_tracer",
    # API para os outros grupos / instrumentação
    "get_current_tracer",
    "emit_event",
    "trace_span",
    "trace_adk_event",
    # reconstrução da linha do tempo
    "load_events",
    "build_timeline",
    "render_timeline",
    "print_timeline",
    "resumir_tokens",
]
