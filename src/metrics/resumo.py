"""Resumo auditável de uma execução — Grupo 2.

gerar_resumo() agrega uma lista de ExecutionEvent (eventos.py) — tipicamente
coletor.eventos de um ExecutionCollector já preenchido — em um ResumoExecucao:
um snapshot numérico, serializável em JSON, sem depender de parsing de texto.

Convenção de agregação (precisa ser respeitada por quem EMITE eventos em
pipeline.py):
  - tipo="fase"          -> duracao_ms vira a duração daquela fase.
  - tipo="tool"           -> cada evento é uma chamada de tool; nome identifica
                             qual tool (ex.: "validar_completude").
  - tipo="validacao"      -> uma verificação de schema/validação foi realizada
                             (independente de ter passado de primeira ou não).
  - tipo="retry"          -> uma tentativa de retry ocorreu após falha
                             recuperável.
  - tipo="falha"          -> todas as tentativas se esgotaram e a saída foi
                             bloqueada (falha definitiva, não recuperável).
  - tipo="decisao_final"  -> deve trazer detalhes={"decisao": <valor>}.
  - detalhes={"requer_revisao_humana": True} em QUALQUER evento -> liga a
    flag agregada de revisão humana no resumo.
  - status="aviso" em qualquer evento -> vira um item em `alertas`
    (opcionalmente lendo detalhes["mensagem"] para o texto).

duracao_total_ms é a SOMA das durações dos eventos tipo="fase" — assume fases
sequenciais (verdadeiro no pipeline atual). Se fases passarem a rodar em
paralelo, esse cálculo precisa ser revisto para usar timestamp de início/fim
em vez de soma.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .eventos import ExecutionEvent


@dataclass
class ResumoExecucao:
    """Snapshot agregado e auditável de uma execução do pipeline."""

    run_id: str
    duracao_total_ms: float | None
    duracao_por_fase_ms: dict[str, float]
    quantidade_validacoes: int
    quantidade_retries: int
    quantidade_falhas: int
    quantidade_tools_chamadas: dict[str, int]
    requer_revisao_humana: bool
    decisao_final: Any | None
    status_final: str
    alertas: list[str] = field(default_factory=list)
    # Placeholders para evolução futura (ADK/OpenTelemetry) — não preenchidos hoje.
    tokens_totais: int | None = None
    custo_estimado: float | None = None
    modelo_usado: str | None = None
    chamadas_llm: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serializa o resumo em um dict simples, pronto para JSON."""
        return {
            "run_id": self.run_id,
            "duracao_total_ms": self.duracao_total_ms,
            "duracao_por_fase_ms": self.duracao_por_fase_ms,
            "quantidade_validacoes": self.quantidade_validacoes,
            "quantidade_retries": self.quantidade_retries,
            "quantidade_falhas": self.quantidade_falhas,
            "quantidade_tools_chamadas": self.quantidade_tools_chamadas,
            "requer_revisao_humana": self.requer_revisao_humana,
            "decisao_final": self.decisao_final,
            "status_final": self.status_final,
            "alertas": self.alertas,
            "tokens_totais": self.tokens_totais,
            "custo_estimado": self.custo_estimado,
            "modelo_usado": self.modelo_usado,
            "chamadas_llm": self.chamadas_llm,
        }


def gerar_resumo(eventos: list[ExecutionEvent], *, run_id: str | None = None) -> ResumoExecucao:
    """Agrega uma lista de ExecutionEvent em um ResumoExecucao.

    run_id é inferido de eventos[0].run_id quando não informado explicitamente.
    Se a lista estiver vazia, run_id precisa ser passado (não há como inferir).
    """
    if not eventos and run_id is None:
        raise ValueError(
            "run_id não pode ser inferido de uma lista vazia de eventos; "
            "informe run_id explicitamente."
        )
    resolved_run_id = run_id or eventos[0].run_id

    duracao_por_fase_ms: dict[str, float] = {}
    quantidade_tools_chamadas: dict[str, int] = {}
    quantidade_validacoes = 0
    quantidade_retries = 0
    quantidade_falhas = 0
    requer_revisao_humana = False
    decisao_final: Any | None = None
    alertas: list[str] = []
    houve_falha = False
    houve_aviso = False

    for evento in eventos:
        if evento.tipo == "fase" and evento.duracao_ms is not None:
            duracao_por_fase_ms[evento.fase] = evento.duracao_ms
        if evento.tipo == "tool":
            quantidade_tools_chamadas[evento.nome] = quantidade_tools_chamadas.get(evento.nome, 0) + 1
        if evento.tipo == "validacao":
            quantidade_validacoes += 1
        if evento.tipo == "retry":
            quantidade_retries += 1
        if evento.tipo == "falha":
            quantidade_falhas += 1
        if evento.detalhes.get("requer_revisao_humana") is True:
            requer_revisao_humana = True
        if evento.tipo == "decisao_final" and "decisao" in evento.detalhes:
            decisao_final = evento.detalhes["decisao"]
        if evento.status == "falha":
            houve_falha = True
        if evento.status == "aviso":
            houve_aviso = True
            mensagem = evento.detalhes.get("mensagem", f"{evento.tipo} '{evento.nome}' gerou aviso")
            alertas.append(f"[{evento.fase}] {mensagem}")

    duracao_total_ms = sum(duracao_por_fase_ms.values()) if duracao_por_fase_ms else None

    if houve_falha:
        status_final = "falha"
    elif houve_aviso:
        status_final = "sucesso_com_alertas"
    else:
        status_final = "sucesso"

    return ResumoExecucao(
        run_id=resolved_run_id,
        duracao_total_ms=duracao_total_ms,
        duracao_por_fase_ms=duracao_por_fase_ms,
        quantidade_validacoes=quantidade_validacoes,
        quantidade_retries=quantidade_retries,
        quantidade_falhas=quantidade_falhas,
        quantidade_tools_chamadas=quantidade_tools_chamadas,
        requer_revisao_humana=requer_revisao_humana,
        decisao_final=decisao_final,
        status_final=status_final,
        alertas=alertas,
    )
