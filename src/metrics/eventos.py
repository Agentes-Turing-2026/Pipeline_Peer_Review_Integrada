"""Evento estruturado de execução — Grupo 2 (métricas, tools, resumo auditável).

Cada ExecutionEvent representa um fato atômico ocorrido durante uma execução do
pipeline (início/fim de fase, chamada de tool, tentativa de validação, retry,
falha, decisão final). Dataclass puro da biblioteca padrão — sem dependência de
ADK/OpenTelemetry hoje, mas os nomes de campo foram escolhidos para mapear
diretamente para conceitos de span/trace no futuro (ver docs/metricas_reference.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

TipoEvento = Literal["fase", "tool", "validacao", "retry", "falha", "decisao_final"]
StatusEvento = Literal["sucesso", "falha", "aviso"]


@dataclass
class ExecutionEvent:
    """Um evento atômico registrado durante uma execução do pipeline."""

    run_id: str
    fase: str
    tipo: TipoEvento
    nome: str
    status: StatusEvento
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    duracao_ms: float | None = None
    detalhes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serializa o evento em um dict simples, pronto para JSON."""
        return {
            "run_id": self.run_id,
            "fase": self.fase,
            "tipo": self.tipo,
            "nome": self.nome,
            "status": self.status,
            "timestamp": self.timestamp,
            "duracao_ms": self.duracao_ms,
            "detalhes": self.detalhes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExecutionEvent":
        """Reconstrói um ExecutionEvent a partir de um dict (ex.: lido de JSON)."""
        return cls(**data)
