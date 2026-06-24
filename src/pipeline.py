"""Pipeline de PEER REVIEW em 4 fases, sobre a orquestração genérica.

Esta é a camada de DOMÍNIO: ela implementa as quatro fases concretas do peer
review reaproveitando o esqueleto agnóstico de ``pipeline_base.py``. A saída de
cada fase alimenta a próxima usando ESTRITAMENTE os schemas oficiais
(``ReviewSchema``, ``CrossReviewSchema``, ``EditorVerdictSchema``):

    Fase 1  Revisão Independente   ->  IndependentReviews  (dict[id, ReviewSchema])
    Fase 2  Leitura Cruzada        ->  CrossReviews        (dict[id, CrossReviewSchema])
    Fase 3  Editor-Chefe           ->  EditorVerdictSchema
    Fase 4  Relatório Final        ->  FinalReport         (markdown + dados)

A mecânica de encadear/propagar/registrar fases vive em ``pipeline_base.py`` e
não conhece peer review. Para aplicar a mesma arquitetura a OUTRO domínio
multiagente, basta escrever novas ``PipelinePhase`` (com seus próprios schemas e
agentes) e montá-las em um ``Pipeline`` — sem tocar na orquestração.

Sem mocks: as fases 1-3 chamam o Gemini real. A fase 4 é pura formatação (sem
LLM). Sem ``GOOGLE_API_KEY``, a demo falha com mensagem clara.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from pipeline_base import Pipeline, PipelineContext, PipelinePhase  # noqa: E402
from review_schema import (  # noqa: E402
    CRITERIOS_REVISAVEIS,
    ESCALA_VEREDITO,
    CrossReviewSchema,
    EditorVerdictSchema,
    ReviewSchema,
    validar_cross_review,
    validar_editor_verdict,
    validar_review,
)
from validacao_retry import PipelineValidationError, validar_com_tentativas  # noqa: E402
from reviewer_agent import MODEL, REVIEWERS, _require_api_key, _run_reviewers  # noqa: E402
from cross_review import _run_cross_review  # noqa: E402
from editor_agent import _run_editor  # noqa: E402


# ---------------------------------------------------------------------------
# Logging do pipeline
# ---------------------------------------------------------------------------

LOG_DIR = HERE / "logs"
LOG_DIR.mkdir(exist_ok=True)

_handler = logging.FileHandler(LOG_DIR / "pipeline.log", encoding="utf-8")
_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
)
logger = logging.getLogger("pipeline")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(_handler)


# ---------------------------------------------------------------------------
# Modo de execução: API (Gemini real) x MOCK (JSONs locais, offline)
# ---------------------------------------------------------------------------
# A alternância permite rodar o pipeline de ponta a ponta SEM internet nem
# chave de API: no modo MOCK, cada fase lê respostas pré-salvas de um JSON local
# e as valida pelos MESMOS schemas oficiais (o contrato continua sendo exercido).
#
# Precedência da resolução do modo:
#   1. config["mode"] passado ao pipeline (flag explícita);
#   2. variável de ambiente PIPELINE_MODE;
#   3. default "api".

DEFAULT_MOCK_FILE = HERE / "mocks" / "peer_review_mock.json"


class RunMode(str, Enum):
    """Modos de execução do pipeline."""

    API = "api"     # chamadas reais ao Gemini, usando os prompts das fases
    MOCK = "mock"   # lê respostas pré-salvas em JSON local (offline, sem chave)


def resolve_mode(config: dict) -> RunMode:
    """Resolve o modo de execução a partir de flag/env/default.

    Aceita sinônimos amigáveis (ex.: 'local'/'offline' -> MOCK, 'real' -> API) e
    levanta ``ValueError`` para valores desconhecidos, em vez de silenciar.
    """
    raw = config.get("mode") or os.getenv("PIPELINE_MODE") or RunMode.API.value
    chave = str(raw).strip().lower()
    if chave in ("api", "gemini", "real", "online"):
        return RunMode.API
    if chave in ("mock", "local", "offline", "json"):
        return RunMode.MOCK
    raise ValueError(
        f"Modo de execução desconhecido: {raw!r}. Use 'api' ou 'mock' "
        f"(via config['mode'] ou a variável de ambiente PIPELINE_MODE)."
    )


def _load_mock(context: PipelineContext) -> dict:
    """Lê (e cacheia) o JSON de mocks da execução atual.

    O arquivo pode ser configurado via ``config['mock_file']``; por padrão usa
    ``mocks/peer_review_mock.json``. O resultado é cacheado no ``context`` para
    que as quatro fases compartilhem a mesma leitura.
    """
    cache = context.config.get("_mock_cache")
    if cache is not None:
        return cache

    mock_path = Path(context.config.get("mock_file") or DEFAULT_MOCK_FILE)
    if not mock_path.exists():
        raise FileNotFoundError(
            f"Modo MOCK ativo, mas o arquivo de mocks não foi encontrado: {mock_path}. "
            f"Crie o JSON ou ajuste config['mock_file']."
        )
    data = json.loads(mock_path.read_text(encoding="utf-8"))
    context.config["_mock_cache"] = data
    return data


# ---------------------------------------------------------------------------
# Containers tipados de saída das fases (contratos entre as fases)
# ---------------------------------------------------------------------------

@dataclass
class IndependentReviews:
    """Saída da Fase 1: pareceres independentes, por revisor."""

    reviews: dict[str, ReviewSchema] = field(default_factory=dict)


@dataclass
class CrossReviews:
    """Saída da Fase 2: pareceres após a leitura cruzada, por revisor."""

    cross_reviews: dict[str, CrossReviewSchema] = field(default_factory=dict)


@dataclass
class FinalReport:
    """Saída da Fase 4: relatório final (markdown legível + dados estruturados)."""

    markdown: str
    data: dict


# ---------------------------------------------------------------------------
# Fase 1 — Revisão Independente
# ---------------------------------------------------------------------------

class IndependentReviewPhase(PipelinePhase[str, IndependentReviews]):
    """Roda os três revisores em paralelo e valida cada parecer (ReviewSchema)."""

    name = "fase_1_revisao_independente"

    def run(self, data: str, context: PipelineContext) -> IndependentReviews:
        mode = resolve_mode(context.config)
        reviews: dict[str, ReviewSchema] = {}

        if mode is RunMode.MOCK:
            payloads = _load_mock(context).get("phase1_reviews", {})
            for rid in REVIEWERS:
                if rid not in payloads:
                    raise RuntimeError(f"Mock sem parecer de Fase 1 para '{rid}'.")
                resultado = validar_com_tentativas(payloads[rid], validar_review, mode, rid)
                reviews[rid] = resultado.dados
                logger.info("Validação Fase 1 '%s': tentativas=%d", rid, resultado.tentativas_usadas)
        else:
            article_text = data
            state = asyncio.run(_run_reviewers(article_text))
            for rid, cfg in REVIEWERS.items():
                raw = state.get(cfg["output_key"])
                if raw is None:
                    raise RuntimeError(f"Revisor '{rid}' não produziu parecer na Fase 1.")
                payload = raw if isinstance(raw, dict) else json.loads(raw)
                resultado = validar_com_tentativas(payload, validar_review, mode, rid)
                reviews[rid] = resultado.dados
                logger.info("Validação Fase 1 '%s': tentativas=%d", rid, resultado.tentativas_usadas)

        logger.info("Fase 1 (%s) concluída: %s pareceres validados.", mode.value, len(reviews))
        return IndependentReviews(reviews=reviews)


# ---------------------------------------------------------------------------
# Fase 2 — Leitura Cruzada
# ---------------------------------------------------------------------------

class CrossReviewPhase(PipelinePhase[IndependentReviews, CrossReviews]):
    """Cada revisor lê os argumentos dos colegas e valida o parecer revisado."""

    name = "fase_2_leitura_cruzada"

    def run(self, data: IndependentReviews, context: PipelineContext) -> CrossReviews:
        mode = resolve_mode(context.config)
        cross: dict[str, CrossReviewSchema] = {}

        if mode is RunMode.MOCK:
            payloads = _load_mock(context).get("phase2_cross_reviews", {})
            for rid in REVIEWERS:
                if rid not in payloads:
                    raise RuntimeError(f"Mock sem parecer de Fase 2 para '{rid}'.")
                resultado = validar_com_tentativas(payloads[rid], validar_cross_review, mode, rid)
                cross[rid] = resultado.dados
                logger.info("Validação Fase 2 '%s': tentativas=%d", rid, resultado.tentativas_usadas)
        else:
            article_text: str = context.initial_input
            # _run_cross_review espera os pareceres chaveados por output_key.
            phase1_by_key = {
                REVIEWERS[rid]["output_key"]: review.model_dump()
                for rid, review in data.reviews.items()
            }
            state = asyncio.run(_run_cross_review(phase1_by_key, article_text))
            for rid in REVIEWERS:
                raw = state.get(f"{rid}_cross_review")
                if raw is None:
                    raise RuntimeError(f"Revisor '{rid}' não produziu parecer na Fase 2.")
                payload = raw if isinstance(raw, dict) else json.loads(raw)
                resultado = validar_com_tentativas(payload, validar_cross_review, mode, rid)
                cross[rid] = resultado.dados
                logger.info("Validação Fase 2 '%s': tentativas=%d", rid, resultado.tentativas_usadas)

        mudaram = [rid for rid, cr in cross.items() if cr.mudou_posicao]
        logger.info(
            "Fase 2 (%s) concluída. Revisores que mudaram de posição: %s.",
            mode.value,
            mudaram or "nenhum",
        )
        return CrossReviews(cross_reviews=cross)


# ---------------------------------------------------------------------------
# Fase 3 — Editor-Chefe
# ---------------------------------------------------------------------------

class EditorVerdictPhase(PipelinePhase[CrossReviews, EditorVerdictSchema]):
    """O Editor-Chefe sintetiza os pareceres finais em um veredito (EditorVerdictSchema)."""

    name = "fase_3_editor_chefe"

    def run(self, data: CrossReviews, context: PipelineContext) -> EditorVerdictSchema:
        mode = resolve_mode(context.config)

        if mode is RunMode.MOCK:
            payload = _load_mock(context).get("phase3_verdict")
            if payload is None:
                raise RuntimeError("Mock sem veredito de Fase 3 ('phase3_verdict').")
            resultado = validar_com_tentativas(payload, validar_editor_verdict, mode, "editor")
            verdict = resultado.dados
            logger.info("Validação Fase 3 'editor': tentativas=%d", resultado.tentativas_usadas)
        else:
            article_text: str = context.initial_input
            verdict_payload = asyncio.run(_run_editor(data.cross_reviews, article_text))
            resultado = validar_com_tentativas(verdict_payload, validar_editor_verdict, mode, "editor")
            verdict = resultado.dados
            logger.info("Validação Fase 3 'editor': tentativas=%d", resultado.tentativas_usadas)

        logger.info(
            "Fase 3 (%s) concluída. Decisão: %s (%s).",
            mode.value,
            verdict.decisao,
            ESCALA_VEREDITO[verdict.decisao],
        )
        return verdict


# ---------------------------------------------------------------------------
# Fase 4 — Relatório Final (pura formatação, sem LLM)
# ---------------------------------------------------------------------------

def _render_report_md(
    article_ref: str,
    reviews: dict[str, ReviewSchema],
    cross: dict[str, CrossReviewSchema],
    verdict: EditorVerdictSchema,
) -> str:
    """Monta o relatório final em Markdown a partir das saídas das três fases."""
    linhas: list[str] = []
    linhas.append("# Relatório Final do Peer Review")
    linhas.append("")
    linhas.append(f"- **Artigo:** {article_ref}")
    linhas.append(f"- **Modelo:** {MODEL}")
    linhas.append(
        f"- **Decisão editorial:** {verdict.decisao} — {ESCALA_VEREDITO[verdict.decisao]}"
    )
    linhas.append("")

    linhas.append("## Síntese")
    linhas.append(verdict.sintese)
    linhas.append("")
    linhas.append("## Justificativa da decisão")
    linhas.append(verdict.justificativa)
    linhas.append("")

    linhas.append("## Notas por revisor (nota geral 1-4)")
    linhas.append("")
    linhas.append("| Revisor | Nota geral | Mudou na leitura cruzada |")
    linhas.append("|---|---|---|")
    for rid in REVIEWERS:
        if rid not in reviews:
            continue
        nota_final = verdict.notas_por_revisor.get(rid, "—")
        mudou = cross[rid].mudou_posicao if rid in cross else False
        linhas.append(f"| {rid} | {nota_final} | {'sim' if mudou else 'não'} |")
    linhas.append("")

    linhas.append("## Críticas levantadas")
    if verdict.criticas:
        for crit in verdict.criticas:
            linhas.append(f"- **[{crit.tipo}]** ({crit.revisor}) {crit.texto}")
    else:
        linhas.append("- Nenhuma crítica registrada.")
    linhas.append("")

    linhas.append("## Recomendações aos autores")
    if verdict.recomendacoes_aos_autores:
        for rec in verdict.recomendacoes_aos_autores:
            linhas.append(f"- {rec}")
    else:
        linhas.append("- Nenhuma recomendação registrada.")
    linhas.append("")

    return "\n".join(linhas)


class FinalReportPhase(PipelinePhase[EditorVerdictSchema, FinalReport]):
    """Consolida tudo num relatório final legível + um payload estruturado.

    Esta fase é pura formatação (sem LLM). Ela lê as saídas das fases anteriores
    pelo ``context`` para montar um relatório completo, mantendo o encadeamento.
    """

    name = "fase_4_relatorio_final"

    def run(self, data: EditorVerdictSchema, context: PipelineContext) -> FinalReport:
        verdict = data
        phase1: IndependentReviews = context.get("fase_1_revisao_independente")
        phase2: CrossReviews = context.get("fase_2_leitura_cruzada")
        article_ref: str = context.config.get("article_ref", "entrada")

        markdown = _render_report_md(
            article_ref=article_ref,
            reviews=phase1.reviews,
            cross=phase2.cross_reviews,
            verdict=verdict,
        )
        structured = {
            "article_ref": article_ref,
            "model": MODEL,
            "decisao": verdict.decisao,
            "decisao_rotulo": ESCALA_VEREDITO[verdict.decisao],
            "phase1_reviews": {rid: r.model_dump() for rid, r in phase1.reviews.items()},
            "phase2_cross_reviews": {
                rid: c.model_dump() for rid, c in phase2.cross_reviews.items()
            },
            "phase3_verdict": verdict.model_dump(),
        }
        logger.info("Fase 4 concluída: relatório final gerado.")
        return FinalReport(markdown=markdown, data=structured)


# ---------------------------------------------------------------------------
# Builder do pipeline de peer review (as 4 fases, na ordem estrita)
# ---------------------------------------------------------------------------

def build_peer_review_pipeline() -> Pipeline:
    """Monta o pipeline de peer review com as quatro fases, na ordem oficial."""
    return Pipeline(
        phases=[
            IndependentReviewPhase(),
            CrossReviewPhase(),
            EditorVerdictPhase(),
            FinalReportPhase(),
        ],
        name="peer_review",
        logger=logger,
    )


# ---------------------------------------------------------------------------
# Demonstração ponta a ponta
# ---------------------------------------------------------------------------

def run_demo(mode: str | None = None) -> FinalReport:
    """Roda o pipeline completo sobre o artigo de exemplo e salva os resultados.

    Parameters
    ----------
    mode:
        Modo de execução: ``"api"`` (Gemini real) ou ``"mock"`` (JSONs locais,
        offline). Se ``None``, cai para a variável de ambiente ``PIPELINE_MODE``
        e, na ausência dela, para ``"api"``.
    """
    config: dict = {"article_ref": "examples/example_article.txt"}
    if mode is not None:
        config["mode"] = mode

    resolved = resolve_mode(config)
    # A chave de API só é exigida no modo que de fato chama o Gemini.
    if resolved is RunMode.API:
        _require_api_key()

    article_path = HERE / "examples" / "example_article.txt"
    article_text = article_path.read_text(encoding="utf-8")

    pipeline = build_peer_review_pipeline()
    print(f"Pipeline '{pipeline.name}' [modo={resolved.value}] — fases: {pipeline.phase_names}")

    result = pipeline.run(
        initial_input=article_text,
        config=config,
        verbose=True,
    )
    report: FinalReport = result.final

    out_dir = HERE / "outputs"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "final_report.md").write_text(report.markdown, encoding="utf-8")
    (out_dir / "final_report.json").write_text(
        json.dumps(report.data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("OK: pipeline concluído.")
    print(f"Relatório (markdown): {out_dir / 'final_report.md'}")
    print(f"Relatório (json):     {out_dir / 'final_report.json'}")
    print(f"Log do pipeline:      {LOG_DIR / 'pipeline.log'}")
    return report


if __name__ == "__main__":
    # Permite escolher o modo pela linha de comando: `python pipeline.py mock`.
    cli_mode = sys.argv[1] if len(sys.argv) > 1 else None
    run_demo(mode=cli_mode)
