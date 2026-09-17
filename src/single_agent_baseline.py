"""Baseline EXPERIMENTAL de agente único — topologia alternativa ao peer review.

Existe para comparar, na mesma infraestrutura de execução/métricas do
pipeline, o que a arquitetura multiagente (3 revisores especializados +
leitura cruzada + editor-chefe, ``reviewer_agent.py`` / ``cross_review.py`` /
``editor_agent.py``) ganha ou custa frente a uma topologia mais simples: UM
único agente que lê o artigo inteiro e produz diretamente o veredito final,
substituindo as três fases de LLM por uma só chamada.

A saída usa o MESMO contrato oficial das outras fases (``EditorVerdictSchema``
de ``review_schema.py``), para que o agente único plugue sem alteração no
relatório final, nas métricas e nos scripts de benchmark já existentes — só a
forma de chegar até ele muda. Como não há revisores especializados,
``notas_por_revisor`` recebe uma única entrada sintética
(``AGENTE_UNICO_ID``), preservando a exigência do schema de que a decisão
esteja ancorada em ao menos uma nota, e ``criticas`` usa o mesmo identificador
como fonte.

Configuração via ambiente (.env) — mesma convenção de ``model_provider.py``,
com sobrescrita própria por papel (``LLM_PROVIDER_SINGLE_AGENT`` /
``LLM_MODEL_SINGLE_AGENT``), útil para rodar a baseline num modelo diferente
do usado pelos revisores/editor sem afetar a outra topologia:
    LLM_PROVIDER   — gemini | maritaca | openai (default: gemini)
    LLM_MODEL      — id do modelo (default: o padrão do provedor escolhido)
    <PROVEDOR>_API_KEY — chave do serviço selecionado (ex.: ``GOOGLE_API_KEY``)

Sem mocks nem fallbacks aqui: se a chave do provedor escolhido não estiver
configurada, a execução falha com mensagem clara (mesma política das demais
fases de LLM do pipeline).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from model_provider import build_model, resolver_config  # noqa: E402

#: Papel do agente único: habilita sobrescrita própria de provedor/modelo
#: (``LLM_PROVIDER_SINGLE_AGENT`` / ``LLM_MODEL_SINGLE_AGENT``).
PAPEL = "single_agent"

#: Identificador sintético usado em ``notas_por_revisor``/``criticas`` no lugar
#: de um id de revisor real — não há revisores especializados nesta topologia.
AGENTE_UNICO_ID = "agente_unico"

APP_NAME = "scoring_single_agent"
USER_ID = "demo_user"


# ---------------------------------------------------------------------------
# Prompt do agente único (produz EditorVerdictSchema diretamente do artigo)
# ---------------------------------------------------------------------------
# Convenção de chaves: {article_text} é injetado pelo ADK a partir do state;
# {{ }} são chaves literais do JSON.

SINGLE_AGENT_PROMPT = """Você é um único revisor responsável por TODO o peer review de um artigo
científico, sozinho — sem outros revisores nem uma etapa separada de leitura
cruzada. Avalie o artigo internamente nas mesmas quatro dimensões usadas em um
peer review completo (Solidez Técnica, Originalidade, Significância e Clareza),
mas responda diretamente com o VEREDITO FINAL, já sintetizado.

TEXTO DO ARTIGO:
{article_text}

INSTRUÇÕES:
1. Leia o artigo e identifique TODAS as críticas negativas relevantes:
   fraquezas (problemas menores) e problemas críticos (bloqueantes). NÃO omita
   nenhuma e NÃO agrupe críticas distintas em uma só.
2. Para cada crítica, registre o tipo: "fraqueza" para um problema menor,
   "critica" para um problema crítico/bloqueante. Use sempre
   "{agente_unico_id}" como fonte (`revisor`) de cada crítica — não há
   revisores especializados nesta avaliação.
3. Tome a DECISÃO editorial na escala 1-4 (a MESMA escala usada em um peer
   review completo):
   - 4 = Aceitar
   - 3 = Aceitar com ressalvas
   - 2 = Rejeitar com ressalvas
   - 1 = Rejeitar
   A decisão deve ser COERENTE com as críticas levantadas: se há problemas
   críticos não resolvidos, a decisão não pode ser "Aceitar".
4. Em `notas_por_revisor`, registre APENAS a chave "{agente_unico_id}" com a
   sua própria nota geral (1-4) para o artigo.
5. Escreva uma `sintese` curta (2-3 frases) do artigo e do seu parecer, e uma
   `justificativa` explicando como você chegou à decisão a partir das críticas
   levantadas.
6. Liste `recomendacoes_aos_autores` acionáveis (o que mudar para melhorar o
   trabalho).

REGRAS OBRIGATÓRIAS:
- NÃO invente críticas que não tenham base no texto do artigo.
- Responda EXCLUSIVAMENTE com um JSON válido, sem texto antes ou depois, neste
  formato EXATO:

{{
  "decisao": <1-4>,
  "justificativa": "<como a decisão foi derivada das críticas levantadas>",
  "sintese": "<resumo do artigo e do parecer, 2-3 frases>",
  "notas_por_revisor": {{ "{agente_unico_id}": <1-4> }},
  "criticas": [
    {{ "revisor": "{agente_unico_id}", "tipo": "<fraqueza|critica>", "texto": "<crítica específica>" }}
  ],
  "recomendacoes_aos_autores": [
    "<recomendação acionável 1>",
    "<recomendação acionável 2>"
  ]
}}
""".replace("{agente_unico_id}", AGENTE_UNICO_ID)


def build_single_agent_agent():
    """Cria o ``LlmAgent`` do agente único, em JSON mode.

    Mesma justificativa de ``editor_agent.build_editor_agent``: sem
    ``output_schema`` porque ``notas_por_revisor: dict[str, int]`` (um *map*)
    gera ``additionalProperties`` no schema, que o Gemini rejeita em
    ``response_schema``. Em vez disso, forçamos JSON puro com
    ``response_mime_type="application/json"`` (o prompt já descreve o formato
    exato) e deixamos a validação estrutural para ``validar_editor_verdict`` +
    retry, no mesmo padrão das demais fases de LLM do pipeline.
    """
    from google.adk.agents import LlmAgent
    from google.genai import types

    return LlmAgent(
        name="single_agent_reviewer",
        model=build_model(papel=PAPEL),
        output_key="single_agent_verdict",   # veredito no state da sessão
        generate_content_config=types.GenerateContentConfig(
            response_mime_type="application/json",
        ),
        description="Agente único que sozinho lê o artigo e produz o veredito final.",
        instruction=SINGLE_AGENT_PROMPT,
    )


# ---------------------------------------------------------------------------
# Execução da fase única (contra a API real do provedor configurado)
# ---------------------------------------------------------------------------

async def _run_single_agent(article_text: str) -> dict:
    """Roda o agente único sobre o artigo inteiro e devolve o veredito (dict).

    Recebe só o texto do artigo (nenhum parecer prévio de outros agentes) e
    devolve o veredito bruto do state; a validação contra
    ``EditorVerdictSchema`` é feita pela fase do pipeline, no mesmo padrão de
    ``editor_agent._run_editor``.
    """
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    agent = build_single_agent_agent()
    runner = InMemoryRunner(agent=agent, app_name=APP_NAME)
    initial_state = {"article_text": article_text}
    session = await runner.session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID, state=initial_state
    )

    trigger = types.Content(
        role="user",
        parts=[types.Part.from_text(text="Revise o artigo sozinho e produza o veredito final.")],
    )
    try:
        from observability import trace_adk_event
    except Exception:  # noqa: BLE001
        trace_adk_event = None  # type: ignore[assignment]
    try:
        from metrics.adk_usage import registrar_usage_adk
    except Exception:  # noqa: BLE001
        registrar_usage_adk = None  # type: ignore[assignment]

    provedor = {"provedor": resolver_config(papel=PAPEL).provider.value}
    fase = "fase_unica_agente_unico"
    async for event in runner.run_async(
        user_id=USER_ID, session_id=session.id, new_message=trigger
    ):
        if trace_adk_event is not None:
            trace_adk_event(event, phase=fase, extra=provedor)
        if registrar_usage_adk is not None:
            registrar_usage_adk(event, fase=fase)

    updated = await runner.session_service.get_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=session.id
    )
    state = dict(updated.state) if updated and updated.state else {}
    raw = state.get("single_agent_verdict")
    if raw is None:
        raise RuntimeError("O agente único não produziu um veredito (single_agent_verdict ausente).")
    return raw if isinstance(raw, dict) else json.loads(raw)
