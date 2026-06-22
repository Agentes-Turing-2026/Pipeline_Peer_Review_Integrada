"""Fase 3 do peer review: VEREDITO DO EDITOR-CHEFE.

Depois da avaliação independente (Fase 1, ``reviewer_agent.py``) e da leitura
cruzada (Fase 2, ``cross_review.py``), o Editor-Chefe SINTETIZA os pareceres
revisados em uma decisão editorial única, no formato oficial
``EditorVerdictSchema`` (``review_schema.py``).

Princípios (alinhados às fases anteriores):

1. **Schema oficial como contrato.** O agente usa ``output_schema=EditorVerdictSchema``
   junto com ``output_key="final_verdict"``, de modo que o ADK force e valide a
   estrutura do veredito. A decisão usa a MESMA escala 1-4 da ``nota_geral`` —
   não há formato paralelo de nota.
2. **Preservar críticas.** O editor NÃO resume nem agrupa: cada fraqueza ou
   problema crítico levantado por um revisor entra individualmente em
   ``criticas`` (campo do schema), com a fonte e o tipo padronizados.
3. **Sem mocks.** Chama o Gemini real; sem ``GOOGLE_API_KEY`` a execução falha
   com mensagem clara (mesma política de ``reviewer_agent.py``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from review_schema import (  # noqa: E402
    CRITERIOS_REVISAVEIS,
    CrossReviewSchema,
    EditorVerdictSchema,
)
from reviewer_agent import MODEL, REVIEWERS  # noqa: E402

# Rótulos legíveis dos critérios (apenas para formatar os pareceres no prompt).
_LABELS = {
    "solidez_tecnica": "Solidez Técnica",
    "originalidade": "Originalidade",
    "significancia": "Significância",
    "clareza": "Clareza",
    "nota_geral": "Nota Geral / Recomendação",
    "confianca": "Confiança",
}

APP_NAME = "scoring_editor"
USER_ID = "demo_user"


# ---------------------------------------------------------------------------
# Formatação dos pareceres finais (pós leitura cruzada) para o prompt do editor
# ---------------------------------------------------------------------------

def format_final_parecer(reviewer_id: str, cross: CrossReviewSchema) -> str:
    """Formata o parecer FINAL de um revisor (após a leitura cruzada).

    Inclui notas + justificativas das quatro dimensões, nota geral, confiança e a
    resposta aos pares — material suficiente para o editor extrair as críticas e
    decidir, sem reparsear texto livre.
    """
    pr = cross.parecer_revisado
    linhas = [f"REVISOR '{reviewer_id}':"]
    for criterio in CRITERIOS_REVISAVEIS:
        bloco = getattr(pr, criterio)
        linhas.append(
            f"  - {_LABELS[criterio]}: nota {bloco.nota} — {bloco.justificativa}"
        )
    linhas.append(f"  Mudou de posição na leitura cruzada: {cross.mudou_posicao}")
    linhas.append(f"  Resposta aos pares: {cross.resposta_aos_pares}")
    return "\n".join(linhas)


def format_all_finais(cross_reviews: dict[str, CrossReviewSchema]) -> str:
    """Concatena os pareceres finais de todos os revisores para o prompt."""
    return "\n\n".join(
        format_final_parecer(rid, cross_reviews[rid])
        for rid in REVIEWERS
        if rid in cross_reviews
    )


# ---------------------------------------------------------------------------
# Prompt do Editor-Chefe (produz EditorVerdictSchema)
# ---------------------------------------------------------------------------
# Convenção de chaves: {article_text} e {pareceres_finais} são injetados pelo
# ADK a partir do state; {{ }} são chaves literais do JSON.

EDITOR_PROMPT = """Você é o EDITOR-CHEFE de um periódico científico. Você recebeu os
pareceres FINAIS de três revisores especializados (já após a leitura cruzada em
que cada um reagiu aos argumentos dos colegas). Seu trabalho é SINTETIZÁ-LOS em
uma decisão editorial única, rastreável e justificada.

TEXTO DO ARTIGO:
{article_text}

PARECERES FINAIS DOS REVISORES (notas, justificativas e resposta aos pares):
{pareceres_finais}

INSTRUÇÕES:
1. Leia cada parecer final e identifique TODAS as críticas negativas: fraquezas
   (problemas menores) e problemas críticos (bloqueantes). NÃO omita nenhuma e
   NÃO agrupe críticas distintas em uma só.
2. Para cada crítica, registre a fonte (o `revisor`) e o tipo: "fraqueza" para um
   problema menor, "critica" para um problema crítico/bloqueante.
3. Tome a DECISÃO editorial na escala 1-4 (a MESMA escala da nota geral dos
   revisores):
   - 4 = Aceitar
   - 3 = Aceitar com ressalvas
   - 2 = Rejeitar com ressalvas
   - 1 = Rejeitar
   A decisão deve ser COERENTE com as notas gerais dos revisores: se há problemas
   críticos não resolvidos, a decisão não pode ser "Aceitar".
4. Em `notas_por_revisor`, registre a nota geral (1-4) de CADA revisor exatamente
   como consta no parecer final dele.
5. Escreva uma `sintese` curta (2-3 frases) do artigo e do parecer agregado, e
   uma `justificativa` explicando como você chegou à decisão a partir das notas e
   das críticas.
6. Liste `recomendacoes_aos_autores` acionáveis (o que mudar para melhorar o
   trabalho).

REGRAS OBRIGATÓRIAS:
- NÃO invente críticas que não estejam nos pareceres.
- Responda EXCLUSIVAMENTE com um JSON válido, sem texto antes ou depois, neste
  formato EXATO:

{{
  "decisao": <1-4>,
  "justificativa": "<como a decisão foi derivada das notas e críticas>",
  "sintese": "<resumo do artigo e do parecer agregado, 2-3 frases>",
  "notas_por_revisor": {{ "statistician": <1-4>, "domain_expert": <1-4>, "copyeditor": <1-4> }},
  "criticas": [
    {{ "revisor": "<id do revisor>", "tipo": "<fraqueza|critica>", "texto": "<crítica específica>" }}
  ],
  "recomendacoes_aos_autores": [
    "<recomendação acionável 1>",
    "<recomendação acionável 2>"
  ]
}}
"""


def build_editor_agent():
    """Cria o ``LlmAgent`` do Editor-Chefe com saída forçada/validada pelo schema.

    Grava o veredito no state sob ``final_verdict`` (output_key), seguindo o mesmo
    padrão das fases anteriores. A importação do ADK é lazy para permitir
    inspecionar o prompt sem o pacote instalado.
    """
    from google.adk.agents import LlmAgent

    return LlmAgent(
        name="editor_in_chief",
        model=MODEL,
        output_key="final_verdict",          # veredito no state da sessão
        output_schema=EditorVerdictSchema,    # força + valida a estrutura
        description="Editor-chefe que sintetiza os pareceres em um veredito final.",
        instruction=EDITOR_PROMPT,
    )


# ---------------------------------------------------------------------------
# Execução da Fase 3 (contra a API real do Gemini)
# ---------------------------------------------------------------------------

async def _run_editor(
    cross_reviews: dict[str, CrossReviewSchema], article_text: str
) -> dict:
    """Roda o Editor-Chefe sobre os pareceres finais e devolve o veredito (dict).

    Recebe os pareceres da Fase 2 (``cross_reviews``), monta o state da sessão
    com os pareceres formatados e devolve o veredito bruto do state (a validação
    contra ``EditorVerdictSchema`` é feita pela fase do pipeline).
    """
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    agent = build_editor_agent()
    runner = InMemoryRunner(agent=agent, app_name=APP_NAME)
    initial_state = {
        "article_text": article_text,
        "pareceres_finais": format_all_finais(cross_reviews),
    }
    session = await runner.session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID, state=initial_state
    )

    trigger = types.Content(
        role="user",
        parts=[types.Part.from_text(text="Sintetize os pareceres em um veredito final.")],
    )
    async for _ in runner.run_async(
        user_id=USER_ID, session_id=session.id, new_message=trigger
    ):
        pass

    updated = await runner.session_service.get_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=session.id
    )
    state = dict(updated.state) if updated and updated.state else {}
    raw = state.get("final_verdict")
    if raw is None:
        raise RuntimeError("O Editor-Chefe não produziu um veredito (final_verdict ausente).")
    return raw if isinstance(raw, dict) else json.loads(raw)
