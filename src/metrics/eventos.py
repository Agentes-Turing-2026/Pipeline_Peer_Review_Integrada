"""Evento estruturado de execução — Grupo 2 (métricas, tools, resumo auditável).

Cada ExecutionEvent representa um fato atômico ocorrido durante uma execução do
pipeline (início/fim de fase, chamada de tool, tentativa de validação, retry,
falha, decisão final, chamada LLM com usage do ADK). Dataclass puro da biblioteca padrão — sem dependência de
ADK/OpenTelemetry hoje, mas os nomes de campo foram escolhidos para mapear
diretamente para conceitos de span/trace no futuro (ver docs/metricas_reference.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

TipoEvento = Literal["fase", "tool", "validacao", "retry", "falha", "decisao_final", "chamada_llm"]
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
    duracao_s: float | None = None
    """Duração medida do que o evento representa, em segundos (float, precisão total)."""
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
            "duracao_s": self.duracao_s,
            "detalhes": self.detalhes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExecutionEvent":
        """Reconstrói um ExecutionEvent a partir de um dict (ex.: lido de JSON).

        Compatibilidade transitória: antes da migração do contrato de duração
        para segundos, eventos persistidos gravavam ``duracao_ms``
        (milissegundos). Para não quebrar a leitura desses payloads antigos,
        esta conversão é de MÃO ÚNICA — ``duracao_ms`` é convertido para
        ``duracao_s`` apenas na entrada; ``to_dict()`` nunca volta a emitir
        ``duracao_ms``. Não é compatibilidade bidirecional, é só uma rampa de
        leitura, e deve ser removida quando não houver mais payloads legados
        em circulação. Estratégia semelhante à de ``TraceEvent.from_dict``
        (Grupo 3, ``src/observability/events.py``) — mas diverge num ponto:
        lá, ``duration_s`` presente com valor ``None`` ainda cai para
        ``duration_ms``; aqui, "duracao_s" presente com valor ``None`` vence
        e NÃO cai para ``duracao_ms`` (ver precedência abaixo).

        Precedência (nunca soma as duas):
          1. Se "duracao_s" está presente no dict (mesmo com valor None), vence
             — é o contrato atual, prioridade absoluta.
          2. Senão, se "duracao_ms" está presente e não é None, converte:
             duracao_s = duracao_ms / 1000.0.
          3. Senão, duracao_s = None.

        Limitação conhecida: chaves desconhecidas no payload (fora dos campos
        do dataclass) ainda levantam TypeError via cls(**payload) — não há
        filtro de campos extras como o de TraceEvent.from_dict.
        """
        payload = dict(data)
        duracao_ms = payload.pop("duracao_ms", None)
        if "duracao_s" not in payload:
            payload["duracao_s"] = duracao_ms / 1000.0 if duracao_ms is not None else None
        return cls(**payload)
