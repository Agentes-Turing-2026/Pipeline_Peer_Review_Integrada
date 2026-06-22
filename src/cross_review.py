"""Segunda fase do peer review: LEITURA CRUZADA entre os revisores.

Na fase 1 (``reviewer_agent.py``) cada revisor avalia o artigo de forma
totalmente independente e isolada — nenhum lê o parecer do outro antes do
Editor-Chefe. Este módulo adiciona a **fase 2**: depois da avaliação
independente, cada revisor lê os ARGUMENTOS (não as notas) dos colegas e
decide, critério a critério, se MANTÉM ou REVISA a sua posição, sempre com
justificativa obrigatória.

Princípios de projeto:

1. **Argumentos, não notas.** Cada revisor recebe apenas as *justificativas* dos
   colegas, nunca as notas. Isso evita ancoragem numérica e força a mudança a
   ser motivada por um argumento concreto, não por "seguir a média".
2. **Resistência controlada (Du et al., 2023).** O prompt instrui o revisor a
   não ceder por pressão social nem para fechar consenso: só revisa uma nota
   diante de um argumento que ele realmente não havia considerado. A divergência
   produtiva é desejável; o objetivo não é convergir, é aumentar a consistência.
3. **Rastreabilidade.** A saída (``CrossReviewSchema``) registra explicitamente
   se houve mudança (``mudou_posicao``), quais critérios mudaram e qual
   argumento foi decisivo em cada um.
4. **Estado de sessão (ADK ``output_key``).** Cada revisor grava o parecer
   atualizado no state da sessão sob ``<id>_cross_review``, do mesmo modo que a
   fase 1 grava ``<id>_review``. O Editor-Chefe passa a consumir o parecer
   revisado.

Não há mocks: a leitura cruzada chama o Gemini real. Sem ``GOOGLE_API_KEY`` a
demonstração falha com mensagem clara (mesma política de ``reviewer_agent.py``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from review_schema import (  # noqa: E402
    CRITERIOS_REVISAVEIS,
    CrossReviewSchema,
    validar_cross_review,
    validar_review,
)
from reviewer_agent import (  # noqa: E402
    MODEL,
    REVIEWERS,
    _require_api_key,
    _run_reviewers,
)

load_dotenv()


# ---------------------------------------------------------------------------
# Logging do estado da sessão antes/depois da segunda fase
# ---------------------------------------------------------------------------

LOG_DIR = HERE / "logs"
LOG_DIR.mkdir(exist_ok=True)

_handler = logging.FileHandler(LOG_DIR / "cross_review.log", encoding="utf-8")
_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
)
logger = logging.getLogger("cross_review")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(_handler)


# Rótulos legíveis dos critérios (apenas para formatar os argumentos).
_LABELS = {
    "solidez_tecnica": "Solidez Técnica",
    "originalidade": "Originalidade",
    "significancia": "Significância",
    "clareza": "Clareza",
    "nota_geral": "Nota Geral / Recomendação",
    "confianca": "Confiança",
}

# Mapeia output_key da fase 1 -> id do revisor (ex.: "statistician_review" -> "statistician").
_KEY_TO_ID = {cfg["output_key"]: rid for rid, cfg in REVIEWERS.items()}


# ---------------------------------------------------------------------------
# Formatação dos insumos da fase 2 (own review + argumentos dos pares)
# ---------------------------------------------------------------------------

def format_own_review(review: dict) -> str:
    """Formata o parecer original do próprio revisor (COM notas e justificativas)."""
    linhas = []
    for criterio in CRITERIOS_REVISAVEIS:
        bloco = review[criterio]
        linhas.append(
            f"- {_LABELS[criterio]}: nota {bloco['nota']}\n"
            f"  justificativa: {bloco['justificativa']}"
        )
    return "\n".join(linhas)


def format_peer_arguments(reviews: dict[str, dict], exclude_id: str) -> str:
    """Formata os argumentos dos OUTROS revisores, SEM revelar as notas deles.

    ``reviews`` é o dicionário ``{output_key: parecer}`` da fase 1. Para o revisor
    ``exclude_id`` montamos apenas as justificativas (argumentos) dos demais —
    nunca as notas — pois a leitura cruzada deve ser dirigida por argumento, não
    por ancoragem numérica.
    """
    blocos = []
    for output_key, review in reviews.items():
        rid = _KEY_TO_ID.get(output_key, output_key)
        if rid == exclude_id:
            continue
        argumentos = [
            f"  - {_LABELS[criterio]}: {review[criterio]['justificativa']}"
            for criterio in CRITERIOS_REVISAVEIS
        ]
        blocos.append(f"REVISOR '{rid}' argumenta:\n" + "\n".join(argumentos))
    return "\n\n".join(blocos)


def prepare_cross_review_state(reviews: dict[str, dict], article_text: str) -> dict:
    """Monta o state da sessão para a fase 2 a partir dos pareceres da fase 1.

    Para cada revisor cria duas chaves consumidas pelo prompt da fase 2:
      - ``<id>_own_review``      -> seu parecer original (com notas);
      - ``<id>_peer_arguments``  -> argumentos dos colegas (sem notas).
    """
    state: dict = {"article_text": article_text}
    for rid, cfg in REVIEWERS.items():
        own = reviews.get(cfg["output_key"])
        if own is None:
            raise RuntimeError(
                f"Parecer da fase 1 ausente para '{rid}' "
                f"(output_key='{cfg['output_key']}')."
            )
        state[f"{rid}_own_review"] = format_own_review(own)
        state[f"{rid}_peer_arguments"] = format_peer_arguments(reviews, rid)
    return state


# ---------------------------------------------------------------------------
# Prompt da leitura cruzada (resistência controlada — Du et al., 2023)
# ---------------------------------------------------------------------------
# Convenção de chaves (igual à fase 1):
#   - {article_text}      -> injetado pelo ADK a partir do state
#   - {{ ... }}           -> chaves literais do JSON (escapadas para o ADK)
# Marcadores substituídos na construção do agente (str.replace):
#   - __PERSONA__         -> persona do revisor
#   - __REVISOR_ID__      -> id do revisor
#   - __OWN_KEY__         -> {<id>_own_review}      (chave de state)
#   - __PEERS_KEY__       -> {<id>_peer_arguments}  (chave de state)

CROSS_REVIEW_PROMPT_TEMPLATE = """__PERSONA__

SEGUNDA FASE DO PEER REVIEW — LEITURA CRUZADA.

Você já emitiu um parecer independente sobre este artigo. Agora você vai LER OS
ARGUMENTOS dos outros revisores (sem ver as notas deles) e decidir, critério a
critério, se MANTÉM ou REVISA a sua posição.

TEXTO DO ARTIGO:
{article_text}

O SEU PARECER ORIGINAL (suas notas e justificativas, da fase independente):
__OWN_KEY__

ARGUMENTOS DOS OUTROS REVISORES (apenas as justificativas — as notas deles foram
DELIBERADAMENTE omitidas):
__PEERS_KEY__

COMO PROCEDER (RESISTÊNCIA CONTROLADA):
- Considere cada argumento dos colegas com seriedade, mas NÃO ceda facilmente.
  Só revise uma nota se um colega trouxer um argumento concreto, ancorado no
  artigo, que você realmente NÃO havia considerado e que muda a avaliação.
- NÃO mude de posição por pressão social, por ser minoria, nem para "fechar
  consenso". Discordância bem fundamentada é desejável e deve ser mantida.
  (Esta postura de resistir a convergir cedo demais segue Du et al., 2023, sobre
  debate multiagente: a divergência produtiva aumenta a qualidade da avaliação.)
- Se um argumento expõe algo que você deixou passar (um dado, uma limitação, um
  mérito do artigo), ATUALIZE a nota correspondente e identifique exatamente
  qual argumento de qual colega foi decisivo.
- Mantenha a sua persona e o seu foco de especialista do início ao fim.

O QUE PRODUZIR:
- `parecer_revisado`: o seu parecer FINAL nas quatro dimensões + nota geral +
  confiança, no MESMO formato da fase 1. Nos critérios em que você MANTÉM a
  posição, repita exatamente a nota original; nos que você revisou, use a nota
  nova. As justificativas devem refletir a sua posição final.
- `mudou_posicao`: true se você revisou ao menos uma nota; false caso contrário.
- `mudancas`: uma entrada para CADA critério cuja nota mudou, com `nota_anterior`,
  `nota_nova`, `argumento_decisivo` (qual argumento de qual colega te convenceu)
  e `justificativa`. Se não mudou nada, deixe a lista VAZIA ([]).
  ATENÇÃO: a `nota_nova` de cada mudança DEVE ser idêntica à nota correspondente
  em `parecer_revisado`, e `nota_anterior` DEVE ser a do seu parecer original.
- `resposta_aos_pares`: um texto curto respondendo aos colegas — o que você
  acatou e o que rejeitou, e por quê.

REGRAS OBRIGATÓRIAS:
- NÃO invente dados que não estejam no artigo.
- Coerência: se você muda a nota geral, isso deve ser consistente com as notas
  dos critérios.
- Responda EXCLUSIVAMENTE com um JSON válido, sem texto antes ou depois, neste
  formato EXATO:

{{
  "revisor": "__REVISOR_ID__",
  "parecer_revisado": {{
    "revisor": "__REVISOR_ID__",
    "solidez_tecnica": {{ "nota": <1-4>, "justificativa": "<mín. 2 frases>" }},
    "originalidade":   {{ "nota": <1-4>, "justificativa": "<mín. 2 frases>" }},
    "significancia":   {{ "nota": <1-4>, "justificativa": "<mín. 2 frases>" }},
    "clareza":         {{ "nota": <1-4>, "justificativa": "<mín. 2 frases>" }},
    "nota_geral":      {{ "nota": <1-4>, "justificativa": "<como chegou à decisão>" }},
    "confianca":       {{ "nota": <1-3>, "justificativa": "<por que este nível>" }}
  }},
  "mudou_posicao": <true|false>,
  "mudancas": [
    {{
      "criterio": "<solidez_tecnica|originalidade|significancia|clareza|nota_geral|confianca>",
      "nota_anterior": <int>,
      "nota_nova": <int>,
      "argumento_decisivo": "<qual argumento de qual colega convenceu>",
      "justificativa": "<por que esse argumento mudou a sua posição>"
    }}
  ],
  "resposta_aos_pares": "<o que você acatou e o que rejeitou, e por quê>"
}}
"""


def build_cross_reviewer_prompt(reviewer_id: str) -> str:
    """Monta o prompt da fase 2 de um revisor, injetando persona, id e chaves."""
    cfg = REVIEWERS[reviewer_id]
    return (
        CROSS_REVIEW_PROMPT_TEMPLATE
        .replace("__PERSONA__", cfg["persona"])
        .replace("__OWN_KEY__", "{" + f"{reviewer_id}_own_review" + "}")
        .replace("__PEERS_KEY__", "{" + f"{reviewer_id}_peer_arguments" + "}")
        .replace("__REVISOR_ID__", reviewer_id)
    )


def build_cross_reviewer_agent(reviewer_id: str):
    """Cria o ``LlmAgent`` da fase 2 com saída forçada/validada por ``CrossReviewSchema``.

    Grava o parecer atualizado no state sob ``<id>_cross_review`` (output_key),
    paralelo ao ``<id>_review`` da fase 1.
    """
    from google.adk.agents import LlmAgent

    cfg = REVIEWERS[reviewer_id]
    return LlmAgent(
        name=f"{cfg['name']}_cross",
        model=MODEL,
        output_key=f"{reviewer_id}_cross_review",   # parecer revisado no state
        output_schema=CrossReviewSchema,            # força + valida a estrutura
        description=f"Leitura cruzada do revisor {reviewer_id}.",
        instruction=build_cross_reviewer_prompt(reviewer_id),
    )


def build_all_cross_reviewers() -> list:
    """Retorna os três agentes da fase de leitura cruzada."""
    return [build_cross_reviewer_agent(rid) for rid in REVIEWERS]


# ---------------------------------------------------------------------------
# Execução da fase 2 (contra a API real do Gemini)
# ---------------------------------------------------------------------------

APP_NAME = "scoring_cross_review"
USER_ID = "demo_user"


async def _run_cross_review(reviews: dict[str, dict], article_text: str) -> dict:
    """Roda os três revisores em paralelo na fase de leitura cruzada.

    Recebe os pareceres da fase 1 (``reviews``), prepara o state com os
    argumentos dos pares (sem notas) e devolve o state final da sessão.
    """
    from google.adk.agents import ParallelAgent
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    cross_agents = ParallelAgent(
        name="scoring_cross_review",
        sub_agents=build_all_cross_reviewers(),
        description="Leitura cruzada: cada revisor reage aos argumentos dos colegas.",
    )

    runner = InMemoryRunner(agent=cross_agents, app_name=APP_NAME)
    initial_state = prepare_cross_review_state(reviews, article_text)
    session = await runner.session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID, state=initial_state
    )

    trigger = types.Content(
        role="user",
        parts=[types.Part.from_text(text="Faça a leitura cruzada dos pareceres.")],
    )
    async for _ in runner.run_async(
        user_id=USER_ID, session_id=session.id, new_message=trigger
    ):
        pass

    updated = await runner.session_service.get_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=session.id
    )
    return dict(updated.state) if updated and updated.state else {}


def _log_state(label: str, reviews_by_id: dict[str, dict]) -> None:
    """Loga, de forma legível, o estado dos pareceres (notas) em um dado momento."""
    logger.info("=" * 70)
    logger.info(f"ESTADO DA SESSÃO — {label}")
    for rid, review in reviews_by_id.items():
        notas = {c: review[c]["nota"] for c in CRITERIOS_REVISAVEIS}
        logger.info(f"  {rid}: {notas}")


def run_demo() -> dict:
    """Pipeline completo: fase 1 (independente) -> fase 2 (leitura cruzada).

    Loga o estado da sessão ANTES e DEPOIS da segunda fase e salva o resultado
    validado em ``outputs/sample_cross_review_output.json``.
    """
    _require_api_key()

    article_path = HERE / "examples" / "example_article.txt"
    article_text = article_path.read_text(encoding="utf-8")

    # --- Fase 1: avaliação independente (reaproveita reviewer_agent) ---
    print("[Fase 1] Avaliação independente dos três revisores...")
    phase1_state = asyncio.run(_run_reviewers(article_text))

    phase1: dict[str, dict] = {}
    phase1_by_id: dict[str, dict] = {}
    for rid, cfg in REVIEWERS.items():
        raw = phase1_state.get(cfg["output_key"])
        if raw is None:
            raise RuntimeError(f"Revisor '{rid}' não produziu parecer na fase 1.")
        data = raw if isinstance(raw, dict) else json.loads(raw)
        validado = validar_review(data).model_dump()
        phase1[cfg["output_key"]] = validado
        phase1_by_id[rid] = validado

    _log_state("ANTES da leitura cruzada (fase 1)", phase1_by_id)
    print("[Fase 2] Leitura cruzada — cada revisor lê os argumentos dos colegas...")

    # --- Fase 2: leitura cruzada ---
    phase2_state = asyncio.run(_run_cross_review(phase1, article_text))

    cross_reviews: dict[str, dict] = {}
    phase2_by_id: dict[str, dict] = {}
    for rid in REVIEWERS:
        raw = phase2_state.get(f"{rid}_cross_review")
        if raw is None:
            raise RuntimeError(f"Revisor '{rid}' não produziu parecer na fase 2.")
        data = raw if isinstance(raw, dict) else json.loads(raw)
        validado = validar_cross_review(data).model_dump()
        cross_reviews[f"{rid}_cross_review"] = validado
        phase2_by_id[rid] = validado["parecer_revisado"]

    _log_state("DEPOIS da leitura cruzada (fase 2)", phase2_by_id)
    for rid in REVIEWERS:
        cr = cross_reviews[f"{rid}_cross_review"]
        logger.info(
            f"  {rid}: mudou_posicao={cr['mudou_posicao']} "
            f"| mudancas={[m['criterio'] for m in cr['mudancas']]}"
        )

    output = {
        "article_file": "examples/example_article.txt",
        "model": MODEL,
        "phase1_reviews": phase1,
        "phase2_cross_reviews": cross_reviews,
    }

    out_dir = HERE / "outputs"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "sample_cross_review_output.json"
    out_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    mudaram = [rid for rid in REVIEWERS
               if cross_reviews[f"{rid}_cross_review"]["mudou_posicao"]]
    print(f"OK: leitura cruzada concluída. Revisores que mudaram de posição: {mudaram or 'nenhum'}.")
    print(f"Output salvo em: {out_path}")
    print(f"Log do estado antes/depois em: {LOG_DIR / 'cross_review.log'}")
    return output


if __name__ == "__main__":
    run_demo()
