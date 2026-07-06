"""Formato COMUM de evento de trace do pipeline (base de observabilidade — Grupo 3).

Este é o "contrato" da observabilidade: um único formato de evento que TODOS os
grupos usam para registrar o que aconteceu dentro de uma mesma execução. A ideia
é ficar próximo da forma como o Google ADK modela eventos (``Event`` com
``invocation_id``, ``author`` etc.), mas de um jeito simples e agnóstico, para:

* reconstruir a linha do tempo de uma execução ("por onde a execução passou?");
* deixar Grupo 1 (validação/retry) e Grupo 2 (tools/métricas) emitirem eventos na
  MESMA execução, sem acoplamento;
* evoluir depois para OpenTelemetry / Cloud Trace sem reescrever o núcleo (os
  campos ``span_id``/``parent_span_id``/``attributes`` mapeiam direto em spans OTel).

Cada evento deixa claro, conforme pedido:
    - a QUAL execução pertence  -> ``run_id`` (e ``invocation_id`` do ADK, quando houver);
    - em QUAL fase ocorreu       -> ``phase``;
    - QUEM gerou                 -> ``author`` (agente, grupo, sistema);
    - qual foi o STATUS          -> ``status`` (ok/erro/alerta/...).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class SpanKind(str, Enum):
    """Papel de um evento/span no trace.

    Hoje só ``run`` e ``phase`` viram spans reais (início/fim, duração,
    ``parent_span_id``); ``agent``/``call``/``tool``/``report`` entram como eventos
    pontuais tipados, ancorados no span da fase corrente — não como spans aninhados.
    """

    RUN = "run"          # a execução inteira
    PHASE = "phase"      # uma das 4 fases do pipeline
    AGENT = "agent"      # um agente (revisor, editor)
    CALL = "call"        # uma chamada importante (ex.: chamada ao modelo)
    TOOL = "tool"        # uma tool determinística (Grupo 2)
    REPORT = "report"    # o relatório final


class EventType(str, Enum):
    """Natureza do evento na linha do tempo."""

    RUN_START = "run_start"
    RUN_END = "run_end"
    SPAN_START = "span_start"
    SPAN_END = "span_end"
    LOG = "log"            # evento pontual informativo (ex.: validação passou)
    ERROR = "error"        # erro geral da execução ou de um span
    ADK_EVENT = "adk_event"  # evento capturado do Runner do ADK


class Status(str, Enum):
    """Status de um span ou evento."""

    OK = "ok"
    ERROR = "erro"
    WARNING = "alerta"
    RUNNING = "em_andamento"


def new_id(prefix: str = "") -> str:
    """Gera um identificador curto e único (com prefixo opcional)."""
    return f"{prefix}{uuid.uuid4().hex[:16]}"


def _iso(epoch: float) -> str:
    """Formata um epoch em ISO-8601 UTC (legível e ordenável)."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


@dataclass
class TraceEvent:
    """Um evento na linha do tempo de uma execução do pipeline.

    Campos obrigatórios respondem às perguntas do enunciado (a qual execução,
    em qual fase, quem gerou, qual status). Os demais dão a hierarquia (span) e
    espaço livre para cada grupo anexar seus dados (``attributes``).
    """

    run_id: str                       # a qual execução pertence
    event_type: str                   # EventType
    name: str = ""                    # rótulo legível (ex.: "fase_1_revisao_independente")
    kind: str | None = None           # SpanKind (para eventos de span)
    span_id: str | None = None        # id deste span
    parent_span_id: str | None = None # id do span pai (hierarquia)
    phase: str | None = None          # em qual fase ocorreu
    author: str | None = None         # quem gerou (agente/grupo/sistema)
    status: str = Status.OK.value     # qual o status
    invocation_id: str | None = None  # invocation_id do ADK (quando houver)
    timestamp: float = field(default_factory=time.time)
    duration_ms: float | None = None  # duração (preenchida em span_end/run_end)
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serializa para dict (com timestamp ISO adicional para leitura humana)."""
        data = asdict(self)
        data["timestamp_iso"] = _iso(self.timestamp)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TraceEvent":
        """Reconstrói um ``TraceEvent`` a partir de um dict (ignora campos extras)."""
        campos = {f for f in cls.__dataclass_fields__}  # noqa: SIM118
        return cls(**{k: v for k, v in data.items() if k in campos})
