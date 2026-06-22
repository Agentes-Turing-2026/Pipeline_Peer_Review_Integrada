"""Agentes revisores adaptados ao schema estruturado de avaliação.

Baseado nos revisores da atividade anterior (statistician, domain_expert e
copyeditor em ``Atividade_2/.../src/agents/``), porém com duas mudanças centrais:

1. Os prompts foram reescritos para avaliar o artigo nas QUATRO dimensões do
   novo schema (Solidez Técnica, Originalidade, Significância e Clareza), cada
   uma com nota (1-4) + justificativa, além de nota geral (1-4) e confiança
   (1-3), também com justificativa.
2. Cada ``LlmAgent`` usa ``output_schema=ReviewSchema`` (de ``review_schema.py``)
   junto com o ``output_key`` já usado no projeto, de modo que o ADK force e
   valide a estrutura de saída.

Configuração via ambiente (.env):
    GOOGLE_API_KEY           — chave da API Gemini (obrigatória para rodar)
    GOOGLE_GENAI_USE_VERTEXAI — FALSE para usar a API pública do Gemini
    GEMINI_MODEL             — id do modelo (default: gemini-2.0-flash)

Não há mocks nem fallbacks: se a ``GOOGLE_API_KEY`` não estiver configurada, a
demonstração falha com uma mensagem clara em vez de fingir que funcionou.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Garante que ``review_schema`` (mesma pasta) seja importável ao rodar o script
# diretamente de qualquer diretório.
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from review_schema import ReviewSchema, validar_review  # noqa: E402

# Carrega variáveis de ambiente a partir do .env da raiz do projeto.
load_dotenv()

# Modelo configurável por ambiente (padrão do projeto: gemini-2.0-flash).
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")


# ---------------------------------------------------------------------------
# Prompt compartilhado: avaliação nas quatro dimensões + nota geral + confiança
# ---------------------------------------------------------------------------
# Convenção de chaves (igual à da atividade anterior):
#   - {article_text}  -> injetado pelo ADK a partir do state da sessão
#   - {{ ... }}       -> chaves literais do JSON (escapadas para o ADK)
# Os marcadores __PERSONA__ e __REVISOR_ID__ são substituídos em tempo de
# construção do agente (via str.replace), não pelo ADK.

REVIEWER_PROMPT_TEMPLATE = """__PERSONA__

Você está realizando o peer review de um artigo científico. Mantenha o seu papel
e a sua perspectiva de especialista ao longo de TODA a avaliação.

TEXTO DO ARTIGO:
{article_text}

Avalie o artigo nas QUATRO dimensões a seguir, EXATAMENTE NESTA ORDEM:

1. SOLIDEZ TÉCNICA — validade dos métodos, rigor científico, suporte empírico
   ou teórico das afirmações.
2. ORIGINALIDADE — novidade da contribuição, diferenciação em relação ao estado
   da arte.
3. SIGNIFICÂNCIA — impacto potencial, relevância para a comunidade, avanço real
   do campo.
4. CLAREZA — qualidade da escrita, organização e reprodutibilidade.

Para CADA uma das quatro dimensões, atribua:
- uma NOTA inteira de 1 a 4, onde 1=Fraco, 2=Regular, 3=Bom, 4=Excelente;
- uma JUSTIFICATIVA com NO MÍNIMO 2 frases, específica e ancorada no conteúdo
  REAL do artigo (cite métodos, números, seções ou trechos concretos). NÃO use
  frases genéricas que serviriam para qualquer artigo.

Depois das quatro dimensões, atribua a NOTA GERAL:
- inteiro de 1 a 4, onde 1=Rejeitar, 2=Rejeitar com ressalvas,
  3=Aceitar com ressalvas, 4=Aceitar;
- a justificativa deve explicar COMO você chegou à decisão a partir das quatro
  notas acima. A nota geral precisa ser COERENTE com os critérios — por exemplo,
  quatro critérios fracos não podem resultar em "Aceitar", e quatro critérios
  excelentes não podem resultar em "Rejeitar".

Por fim, atribua a CONFIANÇA:
- inteiro de 1 a 3, onde 1=Pouco confiante, 2=Moderadamente confiante,
  3=Confiante;
- justifique o nível com base na sua familiaridade com a área do artigo, na
  clareza do texto e na facilidade ou dificuldade de verificar as afirmações.
  Quando uma dimensão estiver fora do seu núcleo de especialidade, avalie mesmo
  assim, mas reflita isso de forma honesta na sua confiança.

REGRAS OBRIGATÓRIAS:
- NÃO invente informações que não estejam no artigo. Se algo não foi reportado
  (por exemplo, tamanho de amostra, intervalos de confiança ou validação
  externa), trate a AUSÊNCIA como limitação — nunca suponha valores.
- Mantenha a sua persona e o seu foco de especialista do início ao fim.
- Responda EXCLUSIVAMENTE com um JSON válido, sem nenhum texto antes ou depois,
  seguindo EXATAMENTE este formato:

{{
  "revisor": "__REVISOR_ID__",
  "solidez_tecnica": {{ "nota": <1-4>, "justificativa": "<mínimo 2 frases>" }},
  "originalidade":   {{ "nota": <1-4>, "justificativa": "<mínimo 2 frases>" }},
  "significancia":   {{ "nota": <1-4>, "justificativa": "<mínimo 2 frases>" }},
  "clareza":         {{ "nota": <1-4>, "justificativa": "<mínimo 2 frases>" }},
  "nota_geral":      {{ "nota": <1-4>, "justificativa": "<como chegou à decisão>" }},
  "confianca":       {{ "nota": <1-3>, "justificativa": "<por que este nível>" }}
}}
"""


# ---------------------------------------------------------------------------
# Personas dos revisores (preservadas da atividade anterior)
# ---------------------------------------------------------------------------

REVIEWERS: dict[str, dict[str, str]] = {
    "statistician": {
        "name": "statistician_reviewer",
        "output_key": "statistician_review",
        "description": "Revisor estatístico e metodológico de artigos científicos.",
        "persona": (
            "Você é um BIOESTATÍSTICO SÊNIOR atuando como revisor por pares. O seu "
            "olhar prioriza o desenho experimental, a amostragem, os controles, a "
            "adequação dos testes estatísticos (p-valores, intervalos de confiança, "
            "tamanho de efeito), a reprodutibilidade e a integridade dos dados "
            "(tamanhos amostrais, dados faltantes, risco de p-hacking)."
        ),
    },
    "domain_expert": {
        "name": "domain_expert_reviewer",
        "output_key": "domain_expert_review",
        "description": "Revisor especialista avaliando novidade e fundamentação teórica.",
        "persona": (
            "Você é um ESPECIALISTA DE DOMÍNIO SÊNIOR atuando como revisor por pares. "
            "O seu olhar prioriza a clareza do problema de pesquisa, a completude da "
            "revisão de literatura, a identificação da lacuna de pesquisa, a novidade "
            "da contribuição e a qualidade da discussão dos resultados frente ao "
            "estado da arte."
        ),
    },
    "copyeditor": {
        "name": "copyeditor_reviewer",
        "output_key": "copyeditor_review",
        "description": "Revisor de gramática, coesão e tom acadêmico.",
        "persona": (
            "Você é um COPYEDITOR ACADÊMICO PROFISSIONAL atuando como revisor por "
            "pares. O seu olhar prioriza a gramática e a ortografia, a coesão e o "
            "fluxo lógico entre parágrafos, o tom acadêmico (formalidade, "
            "objetividade, hedging adequado), a consistência de citações e a "
            "organização e formatação das seções."
        ),
    },
}


def build_reviewer_prompt(reviewer_id: str) -> str:
    """Monta o prompt completo de um revisor injetando persona e identificador.

    Mantém ``{article_text}`` (injetado pelo ADK) e as chaves ``{{ }}`` literais
    do JSON intactas — apenas os marcadores de persona/id são substituídos aqui.
    """
    cfg = REVIEWERS[reviewer_id]
    return (
        REVIEWER_PROMPT_TEMPLATE
        .replace("__PERSONA__", cfg["persona"])
        .replace("__REVISOR_ID__", reviewer_id)
    )


def build_reviewer_agent(reviewer_id: str):
    """Cria um ``LlmAgent`` revisor com saída forçada/validada pelo schema.

    A importação do ADK é feita aqui dentro para que carregar este módulo não
    exija o pacote instalado nem a API — útil para inspecionar os prompts.
    """
    from google.adk.agents import LlmAgent

    cfg = REVIEWERS[reviewer_id]
    return LlmAgent(
        name=cfg["name"],
        model=MODEL,
        output_key=cfg["output_key"],   # grava o parecer no state da sessão
        output_schema=ReviewSchema,      # força + valida a estrutura de notas
        description=cfg["description"],
        instruction=build_reviewer_prompt(reviewer_id),
    )


def build_all_reviewers() -> list:
    """Retorna os três agentes revisores prontos para orquestração."""
    return [build_reviewer_agent(rid) for rid in REVIEWERS]


# ---------------------------------------------------------------------------
# Demonstração (executa contra a API real do Gemini)
# ---------------------------------------------------------------------------

APP_NAME = "scoring_reviewers"
USER_ID = "demo_user"


def _require_api_key() -> None:
    """Falha de forma explícita se a API key não estiver configurada."""
    if not os.getenv("GOOGLE_API_KEY"):
        raise RuntimeError(
            "GOOGLE_API_KEY não configurada. Copie '.env.example' para '.env' na "
            "raiz do projeto e preencha a sua chave do Gemini antes de rodar a "
            "demonstração. O sistema NÃO usa mocks nem respostas simuladas."
        )


async def _run_reviewers(article_text: str) -> dict:
    """Roda os três revisores em paralelo e devolve o state final da sessão."""
    from google.adk.agents import ParallelAgent
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    parallel_reviewers = ParallelAgent(
        name="scoring_reviewers",
        sub_agents=build_all_reviewers(),
        description="Executa os três revisores em paralelo sobre o mesmo artigo.",
    )

    runner = InMemoryRunner(agent=parallel_reviewers, app_name=APP_NAME)
    session = await runner.session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        state={"article_text": article_text},  # alimenta {article_text} dos prompts
    )

    trigger = types.Content(
        role="user",
        parts=[types.Part.from_text(text="Avalie o artigo fornecido.")],
    )

    async for _ in runner.run_async(
        user_id=USER_ID, session_id=session.id, new_message=trigger
    ):
        pass

    updated = await runner.session_service.get_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=session.id
    )
    return dict(updated.state) if updated and updated.state else {}


def run_demo() -> dict:
    """Roda a demonstração ponta a ponta e salva o output validado em disco."""
    _require_api_key()

    article_path = HERE / "examples" / "example_article.txt"
    article_text = article_path.read_text(encoding="utf-8")

    state = asyncio.run(_run_reviewers(article_text))

    reviews: dict[str, dict] = {}
    for reviewer_id, cfg in REVIEWERS.items():
        raw = state.get(cfg["output_key"])
        if raw is None:
            raise RuntimeError(f"Revisor '{reviewer_id}' não produziu saída.")
        data = raw if isinstance(raw, dict) else json.loads(raw)
        # Confirma que o output passa na validação do schema.
        reviews[cfg["output_key"]] = validar_review(data).model_dump()

    output = {
        "article_file": "examples/example_article.txt",
        "model": MODEL,
        "reviews": reviews,
    }

    out_dir = HERE / "outputs"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "sample_run_output.json"
    out_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"OK: {len(reviews)} pareceres validados pelo schema.")
    print(f"Output salvo em: {out_path}")
    return output


if __name__ == "__main__":
    run_demo()
