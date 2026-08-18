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
  - tipo="chamada_llm"    -> uma chamada LLM real (registrada por
                             metrics/adk_usage.py a partir do usage_metadata do
                             ADK); detalhes carrega tokens_entrada/resposta/
                             pensamento/cache/total (int ou None = indisponível,
                             nunca 0 no lugar de ausente), modelo, agente,
                             invocation_id e event_id.
  - tipo="fallback_llm"   -> uma troca de modelo por falha temporária
                             (registrada por llm_fallback.py). detalhes traz
                             evento ("acionado"|"respondeu"|"esgotado"),
                             provedor_inicial, motivo_falha (só "acionado"),
                             opcao_fallback e modelo_que_respondeu (só
                             "respondeu"); o PAPEL vai no campo `nome` do
                             evento (ex.: "editor"), não em detalhes. Ver §6.
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

from .adk_usage import PrecosModelo, estimar_custo_usd
from .eventos import ExecutionEvent
from .precos_modelos import resolver_precos_por_modelo

# Categorias de token agregadas por execução — nomes do UsageChamada
# (adk_usage.py), na ordem de exibição.
CATEGORIAS_TOKENS = (
    "tokens_entrada",
    "tokens_resposta",
    "tokens_pensamento",
    "tokens_cache",
    "tokens_total",
)


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
    # Tokens reais (usage_metadata do ADK), agregados dos eventos tipo="chamada_llm".
    # None = nenhuma chamada LLM registrada (ex.: modo mock) ou dado indisponível
    # — nunca 0 no lugar de "não medido".
    tokens_totais: int | None = None
    custo_estimado: float | None = None
    """Soma do custo de cada chamada tipo="chamada_llm", precificada pelo MODELO
    que respondeu naquela chamada específica (não um preço único aplicado ao
    total agregado da execução — ver gerar_resumo). None sem chamadas LLM ou
    quando nenhuma chamada teve preço resolvido."""
    modelo_usado: str | None = None
    """Um modelo -> o nome dele; vários -> nomes únicos ordenados, separados por ', '."""
    chamadas_llm: int | None = None
    tokens_execucao: dict[str, int | None] | None = None
    """Totais da execução por categoria (CATEGORIAS_TOKENS); None sem chamadas LLM."""
    tokens_por_agente: dict[str, int | None] = field(default_factory=dict)
    """tokens_total somado por agente; valor None = agente chamou, consumo indisponível."""
    tokens_por_fase: dict[str, int | None] = field(default_factory=dict)
    """tokens_total somado por fase; valor None = fase teve chamadas, consumo indisponível."""
    # Fallback de LLM (llm_fallback.py): cada troca de modelo por falha
    # temporária vira um evento tipo="fallback_llm" (ver §1); agregados aqui
    # para o resumo mostrar, sem precisar reler o trace, quantas trocas
    # aconteceram e o desfecho de cada uma.
    quantidade_fallbacks_acionados: int = 0
    """Quantas vezes o modelo principal falhou (temporário) e a reserva entrou."""
    quantidade_fallbacks_respondidos: int = 0
    """Quantas trocas terminaram com a reserva respondendo com sucesso."""
    quantidade_fallbacks_esgotados: int = 0
    """Quantas trocas terminaram com principal E reserva falhando."""
    fallbacks_llm: list[dict[str, Any]] = field(default_factory=list)
    """Uma entrada por evento fallback_llm, na ordem em que ocorreram: fase,
    papel, o desfecho (evento), provedor_inicial, motivo_falha,
    opcao_fallback e modelo_que_respondeu (quando aplicável)."""

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
            "tokens_execucao": self.tokens_execucao,
            "tokens_por_agente": self.tokens_por_agente,
            "tokens_por_fase": self.tokens_por_fase,
            "quantidade_fallbacks_acionados": self.quantidade_fallbacks_acionados,
            "quantidade_fallbacks_respondidos": self.quantidade_fallbacks_respondidos,
            "quantidade_fallbacks_esgotados": self.quantidade_fallbacks_esgotados,
            "fallbacks_llm": self.fallbacks_llm,
        }


def _acumular_tokens(atual: int | None, valor: int | None) -> int | None:
    """Acumula tokens_total preservando a semântica "indisponível ≠ zero".

    None + None = None (houve chamada, consumo desconhecido); disponível soma
    normalmente; indisponível não zera nem invalida o que já foi somado.
    """
    if valor is None:
        return atual
    return valor if atual is None else atual + valor


def _somar_categoria(chamadas: list[ExecutionEvent], categoria: str) -> int | None:
    """Soma uma categoria de token nas chamadas onde ela veio informada.

    None quando NENHUMA chamada informou a categoria. Soma parcial (algumas
    chamadas sem o dado) é permitida e fica auditável pelos alertas dos
    próprios eventos status="aviso".
    """
    # ``detalhes`` é ``dict[str, Any]``, então o ``.get`` devolve ``Any | None``
    # e o filtro por ``is not None`` não estreita o tipo para o mypy. O walrus
    # com anotação explícita resolve sem mudar o comportamento (e sem chamar
    # ``.get`` duas vezes por chamada).
    valores: list[Any] = [
        valor
        for chamada in chamadas
        if (valor := chamada.detalhes.get(categoria)) is not None
    ]
    return sum(valores) if valores else None


def _custo_estimado_por_chamada(
    chamadas: list[ExecutionEvent], precos_override: PrecosModelo | None
) -> tuple[float | None, bool]:
    """Soma o custo de CADA chamada, precificada pelo modelo que respondeu nela.

    ``precos_override``, quando informado, é um preço FIXO aplicado a TODAS
    as chamadas (uso explícito do chamador — ex.: testes, ou um contrato de
    preço que nenhuma fonte automática conhece). Sem ele, cada chamada é
    precificada individualmente por ``resolver_precos_por_modelo()``, a
    partir do ``modelo`` real do próprio evento — nunca um único preço
    "representativo" aplicado ao total agregado de tokens da execução, que
    erraria sempre que mais de um modelo respondeu (o editor configurado com
    um modelo diferente do resto, ou um fallback assumindo a resposta no
    meio da execução).

    Devolve ``(custo_total_ou_None, parcial)``: ``parcial=True`` quando ao
    menos uma chamada com tokens medidos não teve preço resolvido para o seu
    modelo — mesma regra de "ausência ≠ zero" dos tokens: a soma cobre só o
    que foi possível precificar, e a lacuna fica auditável (via alerta em
    ``gerar_resumo``), nunca escondida como custo 0.
    """
    total: float | None = None
    parcial = False
    for chamada in chamadas:
        tokens_entrada = chamada.detalhes.get("tokens_entrada")
        tokens_resposta = chamada.detalhes.get("tokens_resposta")
        precos_chamada = precos_override
        if precos_chamada is None:
            precos_chamada = resolver_precos_por_modelo(chamada.detalhes.get("modelo"))
        if precos_chamada is None:
            if tokens_entrada is not None or tokens_resposta is not None:
                parcial = True
            continue
        custo_chamada = estimar_custo_usd(tokens_entrada, tokens_resposta, precos_chamada)
        if custo_chamada is not None:
            total = custo_chamada if total is None else total + custo_chamada
    return total, parcial


def gerar_resumo(
    eventos: list[ExecutionEvent],
    *,
    run_id: str | None = None,
    duracao_total_s: float | None = None,
    precos: PrecosModelo | None = None,
) -> ResumoExecucao:
    """Agrega uma lista de ExecutionEvent em um ResumoExecucao.

    run_id é inferido de eventos[0].run_id quando não informado explicitamente.
    Se a lista estiver vazia, run_id precisa ser passado (não há como inferir).

    duracao_total_s é o tempo de parede real da execução (ex.:
    ExecutionCollector.duracao_execucao_s), passado pelo chamador. Se omitido,
    o resumo usa duracao_soma_fases_s como aproximação e registra esse fato em
    `alertas`. duracao_soma_fases_s é SEMPRE calculado a partir dos eventos,
    independente deste parâmetro.

    precos (opcional) é um preço FIXO aplicado a TODAS as chamadas da execução
    — útil para forçar um valor manual (testes, ou um contrato de preço que
    nenhuma fonte automática conhece). Sem ele — o padrão — cada chamada
    tipo="chamada_llm" é precificada INDIVIDUALMENTE pelo modelo que de fato
    respondeu (resolver_precos_por_modelo(), em precos_modelos.py — cobre
    ambiente > litellm > tabela oficial), e os custos por chamada são somados
    em custo_estimado. Isso importa porque nem toda chamada da mesma execução
    usa o mesmo modelo: o editor pode estar configurado para um modelo
    diferente do resto do pipeline, ou um fallback (llm_fallback.py) pode ter
    assumido a resposta no meio da execução — aplicar um preço único ao total
    agregado de tokens erraria esses dois casos. Sem preço resolvível para
    NENHUMA chamada, custo_estimado permanece None; consumo real e preço
    nunca se misturam.
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
    quantidade_fallbacks_acionados = 0
    quantidade_fallbacks_respondidos = 0
    quantidade_fallbacks_esgotados = 0
    fallbacks_llm: list[dict[str, Any]] = []

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
        if evento.tipo == "fallback_llm":
            desfecho = evento.detalhes.get("evento")
            if desfecho == "acionado":
                quantidade_fallbacks_acionados += 1
            elif desfecho == "respondeu":
                quantidade_fallbacks_respondidos += 1
            elif desfecho == "esgotado":
                quantidade_fallbacks_esgotados += 1
            fallbacks_llm.append(
                {
                    "fase": evento.fase,
                    "papel": evento.nome,
                    "evento": desfecho,
                    "status": evento.status,
                    "timestamp": evento.timestamp,
                    "provedor_inicial": evento.detalhes.get("provedor_inicial"),
                    "motivo_falha": evento.detalhes.get("motivo_falha"),
                    "opcao_fallback": evento.detalhes.get("opcao_fallback"),
                    "modelo_que_respondeu": evento.detalhes.get("modelo_que_respondeu"),
                }
            )
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

    # Tokens reais (ADK): agrega os eventos tipo="chamada_llm" já deduplicados
    # pelo coletor. Sem chamadas (ex.: modo mock), tudo permanece None/{} —
    # nenhum campo vira 0 por ausência de medição.
    chamadas = [evento for evento in eventos if evento.tipo == "chamada_llm"]
    chamadas_llm: int | None = None
    tokens_execucao: dict[str, int | None] | None = None
    tokens_totais: int | None = None
    tokens_por_agente: dict[str, int | None] = {}
    tokens_por_fase: dict[str, int | None] = {}
    modelo_usado: str | None = None
    custo_estimado: float | None = None
    if chamadas:
        chamadas_llm = len(chamadas)
        tokens_execucao = {
            categoria: _somar_categoria(chamadas, categoria)
            for categoria in CATEGORIAS_TOKENS
        }
        tokens_totais = tokens_execucao["tokens_total"]
        for chamada in chamadas:
            agente = chamada.detalhes.get("agente") or chamada.nome
            total = chamada.detalhes.get("tokens_total")
            tokens_por_agente[agente] = _acumular_tokens(tokens_por_agente.get(agente), total)
            tokens_por_fase[chamada.fase] = _acumular_tokens(tokens_por_fase.get(chamada.fase), total)
        modelos = sorted({
            chamada.detalhes["modelo"]
            for chamada in chamadas
            if chamada.detalhes.get("modelo")
        })
        modelo_usado = ", ".join(modelos) if modelos else None
        custo_estimado, custo_parcial = _custo_estimado_por_chamada(chamadas, precos)
        if custo_parcial and custo_estimado is not None:
            alertas.append(
                "Custo estimado é parcial: não foi possível resolver o preço de "
                "ao menos um modelo que respondeu durante a execução — os "
                "tokens dessa(s) chamada(s) ficaram fora da soma."
            )

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
        tokens_totais=tokens_totais,
        custo_estimado=custo_estimado,
        modelo_usado=modelo_usado,
        chamadas_llm=chamadas_llm,
        tokens_execucao=tokens_execucao,
        tokens_por_agente=tokens_por_agente,
        tokens_por_fase=tokens_por_fase,
        quantidade_fallbacks_acionados=quantidade_fallbacks_acionados,
        quantidade_fallbacks_respondidos=quantidade_fallbacks_respondidos,
        quantidade_fallbacks_esgotados=quantidade_fallbacks_esgotados,
        fallbacks_llm=fallbacks_llm,
    )
