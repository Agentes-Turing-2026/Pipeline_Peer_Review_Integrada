"""Ponte ADK: transforma os ``Event`` do Runner em eventos do trace comum.

Hoje os agentes consomem o stream do ADK e DESCARTAM os eventos::

    async for _ in runner.run_async(...):
        pass   # <- invocation_id, author, tool calls, tudo se perde aqui

Este módulo oferece :func:`trace_adk_event`, que recebe cada ``Event`` do ADK e
o registra na execução atual (fase corrente), preservando o ``invocation_id`` do
ADK e o ``author`` (nome do agente). Assim conseguimos responder "quais agentes
participaram e o que cada um fez" — sem reimplementar nada dos outros grupos.

Uso mínimo nos agentes::

    async for event in runner.run_async(...):
        trace_adk_event(event, phase="fase_1_revisao_independente")

Tudo é defensivo (``getattr``): se a forma do ``Event`` mudar, não quebra o fluxo.
"""

from __future__ import annotations

from typing import Any

from .events import EventType, SpanKind, Status
from .tracer import get_current_tracer


def _safe_function_calls(event: Any) -> list[str]:
    """Extrai nomes de function/tool calls do evento, se existirem."""
    try:
        calls = event.get_function_calls()
    except Exception:  # noqa: BLE001 - método pode não existir/variar
        return []
    nomes = []
    for c in calls or []:
        nome = getattr(c, "name", None)
        if nome:
            nomes.append(nome)
    return nomes


def _safe_text(event: Any) -> str | None:
    """Tenta obter um resumo textual curto do conteúdo do evento."""
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None) if content is not None else None
    if not parts:
        return None
    trechos = []
    for p in parts:
        txt = getattr(p, "text", None)
        if txt:
            trechos.append(txt)
    if not trechos:
        return None
    resumo = " ".join(trechos).strip()
    return (resumo[:180] + "…") if len(resumo) > 180 else resumo


#: Contadores de token do ADK (``usage_metadata``) -> nome no nosso trace.
#: São os campos do ``GenerateContentResponseUsageMetadata``, preenchidos tanto
#: pela rota nativa do Gemini quanto pelo adaptador LiteLlm (Maritaca/OpenAI).
_CAMPOS_TOKENS = {
    "prompt_token_count": "tokens_entrada",
    "candidates_token_count": "tokens_saida",
    "total_token_count": "tokens_total",
    "cached_content_token_count": "tokens_cache",
    "thoughts_token_count": "tokens_raciocinio",
}


def _safe_usage(event: Any) -> dict[str, int]:
    """Extrai o consumo de tokens do evento, QUANDO o provedor o reporta.

    Nem todo serviço devolve ``usage_metadata`` (e alguns preenchem só parte dos
    contadores). Ausência não é erro: significa apenas que aquele evento não
    trouxe medição — por isso só entram no trace os campos realmente presentes,
    em vez de zeros que fingiriam uma medição que não houve.
    """
    usage = getattr(event, "usage_metadata", None)
    if usage is None:
        return {}
    tokens: dict[str, int] = {}
    for campo, rotulo in _CAMPOS_TOKENS.items():
        valor = getattr(usage, campo, None)
        if isinstance(valor, int):
            tokens[rotulo] = valor
    return tokens


def trace_adk_event(
    event: Any, *, phase: str | None = None, extra: dict[str, Any] | None = None
) -> None:
    """Registra um ``Event`` do ADK na linha do tempo da execução atual.

    Captura o ``author`` (quem gerou), o ``invocation_id`` (a qual invocação do
    ADK pertence — nosso vínculo com o ``run_id``), eventuais chamadas de tool,
    o modelo que de fato respondeu (``model_version``) e o consumo de tokens,
    quando o provedor o reporta.

    ``extra`` permite ao chamador anexar contexto que o ``Event`` não carrega —
    é por onde o pipeline informa o PROVEDOR (a ponte segue agnóstica: ela não
    conhece Gemini, Maritaca nem OpenAI).

    No-op se não houver execução em curso.
    """
    tracer = get_current_tracer()
    if tracer is None:
        return

    author = getattr(event, "author", None)
    invocation_id = getattr(event, "invocation_id", None)
    is_final = bool(getattr(event, "is_final_response", lambda: False)()) if callable(
        getattr(event, "is_final_response", None)
    ) else False

    attributes: dict[str, Any] = {}
    if is_final:
        attributes["final_response"] = True
    tool_calls = _safe_function_calls(event)
    if tool_calls:
        attributes["tool_calls"] = tool_calls
    texto = _safe_text(event)
    if texto:
        attributes["preview"] = texto
    partial = getattr(event, "partial", None)
    if partial is not None:
        attributes["partial"] = bool(partial)

    # Qual modelo respondeu de fato (o ADK preenche tanto no Gemini quanto via
    # LiteLlm) e quanto custou — ambos só entram se o provedor os reportar.
    modelo = getattr(event, "model_version", None)
    if modelo:
        attributes["modelo"] = modelo
    attributes.update(_safe_usage(event))
    if extra:
        attributes.update(extra)

    nome = f"adk:{author}" if author else "adk:event"
    tracer.event(
        nome,
        event_type=EventType.ADK_EVENT.value,
        author=author,
        phase=phase,
        status=Status.OK.value,
        kind=SpanKind.AGENT.value,
        invocation_id=invocation_id,
        attributes=attributes,
    )
