"""Tracer: ciclo de vida da execução, hierarquia de spans e storage local.

Este módulo é o "motor" da observabilidade. Ele:

* cria uma execução identificável (``run_id``) e registra início/fim;
* gerencia a hierarquia de spans (run > fase > agente > tool > relatório) usando
  ``span_id``/``parent_span_id``, com uma pilha por-contexto (``contextvars``),
  o que o torna seguro mesmo com fases/agentes rodando em paralelo (async);
* grava cada evento num ``Exporter`` — hoje um arquivo JSONL local, amanhã um
  exportador OpenTelemetry/Cloud Trace, SEM mudar o resto do código.

Decisão de projeto (registrada no README): a instrumentação NUNCA pode derrubar
o pipeline. Por isso o ``Tracer`` é opcional e as falhas de exportação são
engolidas — observabilidade é aditiva, não um ponto de falha novo.
"""

from __future__ import annotations

import contextvars
import json
import threading
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .events import EventType, SpanKind, Status, TraceEvent, new_id

# Span "atual" do contexto de execução. Com contextvars, cada task async herda
# uma cópia — spans abertos em paralelo não se misturam.
_current_span: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "obs_current_span", default=None
)
# Tracer "atual" da execução — é assim que Grupo 1 e Grupo 2 acham a execução
# em curso para emitir eventos, sem precisar receber o tracer por parâmetro.
_current_tracer: contextvars.ContextVar["Tracer | None"] = contextvars.ContextVar(
    "obs_current_tracer", default=None
)


# ---------------------------------------------------------------------------
# Exporters (destino dos eventos) — trocáveis
# ---------------------------------------------------------------------------

class Exporter(ABC):
    """Destino dos eventos de trace. Implementações concretas gravam/enviam."""

    @abstractmethod
    def export(self, event: TraceEvent) -> None:  # pragma: no cover - interface
        ...

    def close(self) -> None:  # pragma: no cover - opcional
        """Fecha recursos (arquivos, conexões). Opcional."""


class JsonlExporter(Exporter):
    """Grava um evento por linha em um arquivo JSONL (``logs/traces/<run_id>.jsonl``).

    JSONL é simples de reconstruir e fácil de futuramente reenviar a um backend
    OpenTelemetry: cada linha é um evento independente.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._fh = self.path.open("a", encoding="utf-8")

    def export(self, event: TraceEvent) -> None:
        line = json.dumps(event.to_dict(), ensure_ascii=False)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            if not self._fh.closed:
                self._fh.close()


class MemoryExporter(Exporter):
    """Guarda os eventos em memória — útil para testes e inspeção."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def export(self, event: TraceEvent) -> None:
        self.events.append(event)


# ---------------------------------------------------------------------------
# Tracer
# ---------------------------------------------------------------------------

class Tracer:
    """Registra a linha do tempo de UMA execução do pipeline."""

    def __init__(self, exporter: Exporter, run_id: str | None = None):
        self.run_id = run_id or new_id("run_")
        self.root_span_id = f"root_{self.run_id}"
        self._exporter = exporter
        self._starts: dict[str, float] = {}  # span_id -> perf_counter inicial

    # -- emissão de baixo nível ------------------------------------------------

    def _emit(self, event: TraceEvent) -> None:
        """Exporta um evento sem nunca propagar falha para o pipeline."""
        try:
            self._exporter.export(event)
        except Exception:  # noqa: BLE001 - observabilidade não pode quebrar o fluxo
            pass

    def event(
        self,
        name: str,
        *,
        event_type: str = EventType.LOG.value,
        author: str | None = None,
        phase: str | None = None,
        status: str = Status.OK.value,
        kind: str | None = None,
        invocation_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """Registra um evento PONTUAL, ancorado no span atual (fase/agente/etc.)."""
        self._emit(
            TraceEvent(
                run_id=self.run_id,
                event_type=event_type,
                name=name,
                kind=kind,
                span_id=_current_span.get(),
                parent_span_id=None,
                phase=phase,
                author=author,
                status=status,
                invocation_id=invocation_id,
                attributes=dict(attributes or {}),
            )
        )

    # -- spans (com início/fim e duração) -------------------------------------

    @contextmanager
    def span(
        self,
        name: str,
        *,
        kind: str = SpanKind.CALL.value,
        phase: str | None = None,
        author: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Iterator[str]:
        """Abre um span filho do span atual, registrando início, fim e duração.

        Em caso de exceção, marca o span como ``erro`` (e emite um evento de erro)
        antes de re-levantar — assim a linha do tempo mostra onde falhou.
        """
        span_id = new_id("sp_")
        parent_id = _current_span.get()
        token = _current_span.set(span_id)
        self._starts[span_id] = time.perf_counter()

        self._emit(
            TraceEvent(
                run_id=self.run_id,
                event_type=EventType.SPAN_START.value,
                name=name,
                kind=kind,
                span_id=span_id,
                parent_span_id=parent_id,
                phase=phase,
                author=author,
                status=Status.RUNNING.value,
                attributes=dict(attributes or {}),
            )
        )

        status = Status.OK.value
        try:
            yield span_id
        except Exception as exc:  # noqa: BLE001 - re-levantada após registrar
            status = Status.ERROR.value
            self._emit(
                TraceEvent(
                    run_id=self.run_id,
                    event_type=EventType.ERROR.value,
                    name=f"{name}: {type(exc).__name__}",
                    kind=kind,
                    span_id=span_id,
                    parent_span_id=parent_id,
                    phase=phase,
                    author=author,
                    status=Status.ERROR.value,
                    attributes={"erro": str(exc)},
                )
            )
            raise
        finally:
            inicio = self._starts.pop(span_id, None)
            dur_s = (time.perf_counter() - inicio) if inicio is not None else None
            self._emit(
                TraceEvent(
                    run_id=self.run_id,
                    event_type=EventType.SPAN_END.value,
                    name=name,
                    kind=kind,
                    span_id=span_id,
                    parent_span_id=parent_id,
                    phase=phase,
                    author=author,
                    status=status,
                    duration_s=dur_s,
                )
            )
            _current_span.reset(token)

    # -- ciclo de vida da execução --------------------------------------------

    @contextmanager
    def run(
        self,
        name: str = "peer_review",
        *,
        attributes: dict[str, Any] | None = None,
    ) -> Iterator["Tracer"]:
        """Delimita a execução inteira: emite ``run_start`` e ``run_end``.

        Torna este tracer o "tracer atual" durante o bloco, para que qualquer
        parte do pipeline (ou dos outros grupos) o encontre via
        :func:`get_current_tracer` / :func:`emit_event`.
        """
        tracer_token = _current_tracer.set(self)
        span_token = _current_span.set(self.root_span_id)
        self._starts[self.root_span_id] = time.perf_counter()

        self._emit(
            TraceEvent(
                run_id=self.run_id,
                event_type=EventType.RUN_START.value,
                name=name,
                kind=SpanKind.RUN.value,
                span_id=self.root_span_id,
                status=Status.RUNNING.value,
                attributes=dict(attributes or {}),
            )
        )

        status = Status.OK.value
        try:
            yield self
        except Exception as exc:  # noqa: BLE001 - registra erro geral e re-levanta
            status = Status.ERROR.value
            self._emit(
                TraceEvent(
                    run_id=self.run_id,
                    event_type=EventType.ERROR.value,
                    name=f"execução falhou: {type(exc).__name__}",
                    kind=SpanKind.RUN.value,
                    span_id=self.root_span_id,
                    status=Status.ERROR.value,
                    attributes={"erro": str(exc)},
                )
            )
            raise
        finally:
            inicio = self._starts.pop(self.root_span_id, None)
            dur_s = (time.perf_counter() - inicio) if inicio is not None else None
            self._emit(
                TraceEvent(
                    run_id=self.run_id,
                    event_type=EventType.RUN_END.value,
                    name=name,
                    kind=SpanKind.RUN.value,
                    span_id=self.root_span_id,
                    status=status,
                    duration_s=dur_s,
                )
            )
            _current_span.reset(span_token)
            _current_tracer.reset(tracer_token)
            try:
                self._exporter.close()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Fábrica + API global (o que os outros grupos usam)
# ---------------------------------------------------------------------------

def create_tracer(
    trace_dir: str | Path = "logs/traces",
    run_id: str | None = None,
) -> Tracer:
    """Cria um ``Tracer`` que grava em ``<trace_dir>/<run_id>.jsonl``."""
    rid = run_id or new_id("run_")
    exporter = JsonlExporter(Path(trace_dir) / f"{rid}.jsonl")
    return Tracer(exporter=exporter, run_id=rid)


def get_current_tracer() -> Tracer | None:
    """Devolve o tracer da execução em curso (ou ``None`` fora de uma execução)."""
    return _current_tracer.get()


def emit_event(
    name: str,
    *,
    author: str | None = None,
    phase: str | None = None,
    status: str = Status.OK.value,
    event_type: str = EventType.LOG.value,
    kind: str | None = None,
    invocation_id: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> None:
    """Emite um evento na execução atual, se houver uma.

    Este é o ponto de entrada COMUM para os outros grupos: Grupo 1 (validação) e
    Grupo 2 (tools/métricas) chamam ``emit_event(...)`` e seus eventos entram na
    MESMA linha do tempo, ancorados na fase corrente. Fora de uma execução (sem
    tracer), a chamada é um no-op — nada quebra.
    """
    tracer = _current_tracer.get()
    if tracer is None:
        return
    tracer.event(
        name,
        event_type=event_type,
        author=author,
        phase=phase,
        status=status,
        kind=kind,
        invocation_id=invocation_id,
        attributes=attributes,
    )


@contextmanager
def trace_span(
    name: str,
    *,
    kind: str = SpanKind.CALL.value,
    phase: str | None = None,
    author: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> Iterator[str | None]:
    """Abre um sub-span na execução atual (no-op se não houver tracer).

    Açúcar sintático para instrumentar trechos sem carregar o tracer à mão::

        with trace_span("statistician", kind="agent", author="statistician"):
            ...
    """
    tracer = _current_tracer.get()
    if tracer is None:
        yield None
        return
    with tracer.span(name, kind=kind, phase=phase, author=author, attributes=attributes) as sid:
        yield sid
