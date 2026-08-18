"""Captura de usage_metadata dos eventos do ADK — Grupo 2 (tokens reais).

O Runner do ADK emite ``Event`` para cada resposta dos agentes; quando o modelo
informa consumo, o evento carrega ``usage_metadata`` (contrato do google-genai:
``prompt_token_count``, ``candidates_token_count``, ``thoughts_token_count``,
``cached_content_token_count``, ``total_token_count``). Este módulo lê esses
valores DIRETAMENTE dos eventos — nunca estima tokens por tamanho de texto — e
os registra no ExecutionCollector da execução como eventos ``tipo="chamada_llm"``,
a MESMA fonte de eventos que alimenta o resumo auditável (sem segunda história).

Regras que este módulo garante:
  - Ausência ≠ zero: campo sem valor no ADK vira ``None`` (exibido como "n/d"),
    nunca 0 e nunca uma estimativa.
  - Sem contagem duplicada: eventos parciais de streaming são ignorados (o
    usage de parciais é cumulativo; só a resposta final conta) e o registro no
    coletor usa ``chave_dedup`` — ``event.id`` quando existir, senão
    ``invocation_id + author``.
  - Contexto preservado: modelo/versão, agente (``author``), fase e
    ``invocation_id`` acompanham cada chamada.

Ponto de conexão (combinado com o Grupo 3 — mesmo padrão de ``trace_adk_event``,
sem tocar em ``adk_bridge.py``): os loops ``async for event in runner.run_async``
dos agentes chamam ``registrar_usage_adk(event, fase=...)``, que é no-op quando
nenhum coletor foi registrado (demos avulsas continuam funcionando).

Custo fica PREPARADO, não ativado: ``precos_de_ambiente()`` lê preço configurado
via variáveis de ambiente (nenhum preço fixo no código) e ``estimar_custo_usd()``
separa claramente consumo real (tokens) de preço configurado.

Tudo defensivo (``getattr``): mudança na forma do ``Event`` não quebra o fluxo.
Versão validada nos testes: ``google-adk==1.27.5`` (ver requirements.txt).
"""

from __future__ import annotations

import contextvars
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .coletor import ExecutionCollector
from .eventos import ExecutionEvent, StatusEvento

# Nome do campo no UsageChamada -> nome do campo no usage_metadata do ADK.
CAMPOS_USAGE: dict[str, str] = {
    "tokens_entrada": "prompt_token_count",
    "tokens_resposta": "candidates_token_count",
    "tokens_pensamento": "thoughts_token_count",
    "tokens_cache": "cached_content_token_count",
    "tokens_total": "total_token_count",
}

VAR_PRECO_ENTRADA = "GRUPO2_PRECO_USD_MILHAO_TOKENS_ENTRADA"
VAR_PRECO_SAIDA = "GRUPO2_PRECO_USD_MILHAO_TOKENS_SAIDA"


@dataclass(frozen=True)
class UsageChamada:
    """Consumo e contexto de UMA chamada LLM, extraídos de um Event do ADK.

    Todo campo de token é ``int | None`` — ``None`` significa "o ADK não
    informou" (indisponível), o que é diferente de 0 (informado como zero).
    """

    tokens_entrada: int | None = None
    tokens_resposta: int | None = None
    tokens_pensamento: int | None = None
    tokens_cache: int | None = None
    tokens_total: int | None = None
    modelo: str | None = None
    agente: str | None = None
    invocation_id: str | None = None
    event_id: str | None = None

    @property
    def usage_disponivel(self) -> bool:
        """True se ao menos um campo de token veio preenchido do ADK."""
        return any(getattr(self, campo) is not None for campo in CAMPOS_USAGE)

    def to_detalhes(self) -> dict[str, Any]:
        """Serializa para o dict ``detalhes`` do ExecutionEvent (pronto p/ JSON)."""
        return {
            "tokens_entrada": self.tokens_entrada,
            "tokens_resposta": self.tokens_resposta,
            "tokens_pensamento": self.tokens_pensamento,
            "tokens_cache": self.tokens_cache,
            "tokens_total": self.tokens_total,
            "modelo": self.modelo,
            "agente": self.agente,
            "invocation_id": self.invocation_id,
            "event_id": self.event_id,
        }


def _como_int(valor: Any) -> int | None:
    """Converte o valor vindo do ADK para int, preservando None (nunca 0)."""
    if valor is None:
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _tem_conteudo(event: Any) -> bool:
    """True se o evento carrega alguma resposta (content com parts)."""
    content = getattr(event, "content", None)
    return bool(getattr(content, "parts", None)) if content is not None else False


def extrair_usage(event: Any) -> UsageChamada | None:
    """Extrai o UsageChamada de um Event do ADK, ou None se não conta.

    Devolve ``None`` (evento NÃO representa uma chamada LLM contabilizável) quando:
      - ``partial=True`` — resposta parcial de streaming: o usage dos parciais é
        cumulativo, só o evento final da invocação deve contar;
      - ``author`` é ``"user"`` — mensagem do usuário, não resposta de modelo;
      - o evento não tem ``usage_metadata`` NEM conteúdo de resposta — eventos de
        controle (ex.: só state delta) não são chamadas LLM.

    Quando o evento é uma resposta de modelo mas ``usage_metadata`` está ausente,
    devolve um UsageChamada com todos os tokens ``None`` (indisponível ≠ zero) —
    a chamada ACONTECEU e precisa aparecer no resumo, mesmo sem números.
    """
    if bool(getattr(event, "partial", False)):
        return None
    author = getattr(event, "author", None)
    if author == "user":
        return None
    usage = getattr(event, "usage_metadata", None)
    if usage is None and not _tem_conteudo(event):
        return None

    tokens = {
        campo: _como_int(getattr(usage, campo_adk, None)) if usage is not None else None
        for campo, campo_adk in CAMPOS_USAGE.items()
    }
    # model_version fica no próprio Event (herda de LlmResponse no google-adk);
    # fallback para o usage_metadata cobre variações de versão do contrato.
    modelo = getattr(event, "model_version", None) or getattr(usage, "model_version", None)
    return UsageChamada(
        **tokens,
        modelo=str(modelo) if modelo is not None else None,
        agente=str(author) if author is not None else None,
        invocation_id=getattr(event, "invocation_id", None),
        event_id=getattr(event, "id", None),
    )


def chave_dedup_usage(usage: UsageChamada) -> str | None:
    """Chave de dedup da chamada: event_id; senão invocation_id+agente; senão None."""
    if usage.event_id:
        return f"adk_usage:{usage.event_id}"
    if usage.invocation_id and usage.agente:
        return f"adk_usage:{usage.invocation_id}:{usage.agente}"
    return None


# ---------------------------------------------------------------------------
# Consumidor plugável nos loops dos agentes (padrão do tracer do Grupo 3:
# contexto global + no-op quando não há execução instrumentada em curso).
# ---------------------------------------------------------------------------

_coletor_adk: contextvars.ContextVar[ExecutionCollector | None] = contextvars.ContextVar(
    "metrics_coletor_adk", default=None
)


def definir_coletor_adk(coletor: ExecutionCollector | None) -> contextvars.Token:
    """Registra o coletor que receberá as chamadas LLM desta execução.

    Chamado pelo pipeline ao criar o ExecutionCollector. Devolve o token do
    contextvar (útil em testes para restaurar com ``.reset()``); passar ``None``
    desliga o consumidor.
    """
    return _coletor_adk.set(coletor)


def obter_coletor_adk() -> ExecutionCollector | None:
    """Coletor registrado para a execução atual, ou None."""
    return _coletor_adk.get()


def registrar_usage_adk(event: Any, *, fase: str) -> ExecutionEvent | None:
    """Registra a chamada LLM do evento ADK no coletor da execução atual.

    No-op (devolve None) quando não há coletor registrado, quando o evento não
    representa uma chamada LLM contabilizável (parcial, usuário, controle) ou
    quando a chave de dedup já foi vista (evento repetido no streaming).

    Uso nos agentes, ao lado de ``trace_adk_event``::

        async for event in runner.run_async(...):
            registrar_usage_adk(event, fase="fase_1_revisao_independente")
    """
    coletor = obter_coletor_adk()
    if coletor is None:
        return None
    usage = extrair_usage(event)
    if usage is None:
        return None

    nome = usage.agente or "chamada_llm"
    detalhes = usage.to_detalhes()
    status: StatusEvento
    if usage.usage_disponivel:
        status = "sucesso"
    else:
        status = "aviso"
        detalhes["mensagem"] = (
            f"chamada LLM de '{nome}' sem usage_metadata do ADK — "
            "tokens registrados como indisponíveis (n/d), não 0"
        )
    return coletor.registrar(
        fase=fase,
        tipo="chamada_llm",
        nome=nome,
        status=status,
        chave_dedup=chave_dedup_usage(usage),
        detalhes=detalhes,
    )


# ---------------------------------------------------------------------------
# Custo PREPARADO (não ativado): preço vem de configuração (env), nunca do
# código; consumo real (tokens) e preço configurado ficam separados.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PrecosModelo:
    """Preço configurado, em USD por milhão de tokens (entrada e saída)."""

    usd_por_milhao_tokens_entrada: float
    usd_por_milhao_tokens_saida: float


def precos_de_ambiente(env: Mapping[str, str] | None = None) -> PrecosModelo | None:
    """Lê o preço configurado das variáveis de ambiente, ou None se não houver.

    Variáveis: ``GRUPO2_PRECO_USD_MILHAO_TOKENS_ENTRADA`` e
    ``GRUPO2_PRECO_USD_MILHAO_TOKENS_SAIDA``. As DUAS precisam estar presentes
    e numéricas — configuração parcial é tratada como ausente (None), para o
    custo nunca sair de um preço pela metade.
    """
    fonte = env if env is not None else os.environ
    bruto_entrada = fonte.get(VAR_PRECO_ENTRADA)
    bruto_saida = fonte.get(VAR_PRECO_SAIDA)
    if bruto_entrada is None or bruto_saida is None:
        return None
    try:
        return PrecosModelo(
            usd_por_milhao_tokens_entrada=float(bruto_entrada),
            usd_por_milhao_tokens_saida=float(bruto_saida),
        )
    except ValueError:
        return None


def estimar_custo_usd(
    tokens_entrada: int | None,
    tokens_resposta: int | None,
    precos: PrecosModelo | None,
) -> float | None:
    """Custo estimado em USD do consumo informado, ou None se não estimável.

    ``None`` quando não há preço configurado ou quando NENHUM dos dois lados de
    consumo está disponível (ausência ≠ zero: sem tokens medidos não existe
    custo "0", existe custo desconhecido). Com consumo parcialmente disponível,
    estima só a parte medida — o resumo deixa a limitação visível nos campos
    de tokens ao lado.
    """
    if precos is None:
        return None
    if tokens_entrada is None and tokens_resposta is None:
        return None
    custo = 0.0
    if tokens_entrada is not None:
        custo += tokens_entrada * precos.usd_por_milhao_tokens_entrada / 1_000_000
    if tokens_resposta is not None:
        custo += tokens_resposta * precos.usd_por_milhao_tokens_saida / 1_000_000
    return custo
