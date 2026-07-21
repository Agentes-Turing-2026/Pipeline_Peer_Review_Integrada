"""Resumo auditável de uma execução — Grupo 2.

gerar_resumo() agrega uma lista de ExecutionEvent (eventos.py) — tipicamente
coletor.eventos de um ExecutionCollector já preenchido — em um ResumoExecucao:
um snapshot numérico, serializável em JSON, sem depender de parsing de texto.

Convenção de agregação (precisa ser respeitada por quem EMITE eventos em
pipeline.py):
  - tipo="fase"          -> duracao_s vira a duração daquela fase.
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

Dois números de duração, nenhum substitui o outro:
  - duracao_total_s: tempo de parede da execução inteira (início ao fim),
    tipicamente vindo de ExecutionCollector.duracao_execucao_s. Cobre TUDO
    que acontece na execução, inclusive o que nenhuma fase mede (extração,
    montagem de relatório, gravação em disco).
  - duracao_soma_fases_s: SOMA das durações dos eventos tipo="fase" — assume
    fases sequenciais (verdadeiro no pipeline atual). Se fases passarem a
    rodar em paralelo, esse cálculo precisa ser revisto para usar timestamp
    de início/fim em vez de soma.

A diferença entre os dois é o tempo NÃO atribuído a nenhuma fase — isso é
informação de auditoria útil, não ruído, e é por isso que os dois campos
convivem em vez de um substituir o outro. Quando gerar_resumo() não recebe
o tempo de parede real, duracao_total_s cai para duracao_soma_fases_s como
aproximação, e um alerta registra essa aproximação em `alertas`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .eventos import ExecutionEvent


@dataclass
class ResumoExecucao:
    """Snapshot agregado e auditável de uma execução do pipeline."""

    run_id: str
    duracao_total_s: float | None
    duracao_soma_fases_s: float | None
    duracao_por_fase_s: dict[str, float]
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
            "duracao_total_s": self.duracao_total_s,
            "duracao_soma_fases_s": self.duracao_soma_fases_s,
            "duracao_por_fase_s": self.duracao_por_fase_s,
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


def gerar_resumo(
    eventos: list[ExecutionEvent],
    *,
    run_id: str | None = None,
    duracao_total_s: float | None = None,
) -> ResumoExecucao:
    """Agrega uma lista de ExecutionEvent em um ResumoExecucao.

    run_id é inferido de eventos[0].run_id quando não informado explicitamente.
    Se a lista estiver vazia, run_id precisa ser passado (não há como inferir).

    duracao_total_s é o tempo de parede real da execução (ex.:
    ExecutionCollector.duracao_execucao_s), passado pelo chamador. Se omitido,
    o resumo usa duracao_soma_fases_s como aproximação e registra esse fato em
    `alertas`. duracao_soma_fases_s é SEMPRE calculado a partir dos eventos,
    independente deste parâmetro.
    """
    if not eventos and run_id is None:
        raise ValueError(
            "run_id não pode ser inferido de uma lista vazia de eventos; "
            "informe run_id explicitamente."
        )
    resolved_run_id = run_id or eventos[0].run_id

    duracao_por_fase_s: dict[str, float] = {}
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
        if evento.tipo == "fase" and evento.duracao_s is not None:
            duracao_por_fase_s[evento.fase] = evento.duracao_s
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

    duracao_soma_fases_s = sum(duracao_por_fase_s.values()) if duracao_por_fase_s else None

    if duracao_total_s is None:
        duracao_total_s = duracao_soma_fases_s
        if duracao_soma_fases_s is not None:
            alertas.append(
                "Duração total aproximada pela soma das durações das fases "
                "(nenhum tempo de parede real foi informado a gerar_resumo())."
            )

    if houve_falha:
        status_final = "falha"
    elif houve_aviso:
        status_final = "sucesso_com_alertas"
    else:
        status_final = "sucesso"

    return ResumoExecucao(
        run_id=resolved_run_id,
        duracao_total_s=duracao_total_s,
        duracao_soma_fases_s=duracao_soma_fases_s,
        duracao_por_fase_s=duracao_por_fase_s,
        quantidade_validacoes=quantidade_validacoes,
        quantidade_retries=quantidade_retries,
        quantidade_falhas=quantidade_falhas,
        quantidade_tools_chamadas=quantidade_tools_chamadas,
        requer_revisao_humana=requer_revisao_humana,
        decisao_final=decisao_final,
        status_final=status_final,
        alertas=alertas,
    )
