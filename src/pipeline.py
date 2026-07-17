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
from contextlib import nullcontext
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
from extraction import ExtractedDocument, PdfExtractor, get_default_extractor  # noqa: E402

# Tools determinísticas do Grupo 2 (importação guardada: pipeline funciona sem elas).
try:
    from tools.validar_completude import validar_completude as _tool_completude
except Exception:
    _tool_completude = None  # type: ignore[assignment]
try:
    from tools.auditar_decisao_final import auditar_decisao_final as _tool_auditoria
except Exception:
    _tool_auditoria = None  # type: ignore[assignment]

# Observabilidade / traces (Grupo 3). Instrumentação ADITIVA: importação guardada
# para o pipeline continuar rodando mesmo se o pacote não estiver presente.
try:
    from observability import (  # noqa: E402
        create_tracer,
        emit_event,
        get_current_tracer,
    )
except Exception:  # noqa: BLE001
    create_tracer = None  # type: ignore[assignment]

    def emit_event(*_a, **_k):  # type: ignore[misc]
        return None

    def get_current_tracer():  # type: ignore[misc]
        return None


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


import time
from contextlib import nullcontext

# Métricas de execução do Grupo 2 (importação guardada: pipeline funciona sem elas).
try:
    from metrics.coletor import ExecutionCollector
    from metrics.resumo import gerar_resumo
    from metrics.exportar import imprimir_resumo, salvar_resumo_json
except ImportError:
    ExecutionCollector = None
    gerar_resumo = None
    imprimir_resumo = None
    salvar_resumo_json = None


def _fase_medida(coletor: ExecutionCollector | None, nome: str):
    """Context manager: mede a fase via coletor.fase(nome); no-op se coletor for None."""
    return coletor.fase(nome) if coletor is not None else nullcontext()


def _registrar_validacao(coletor: ExecutionCollector | None, *, fase: str, agente: str, resultado) -> None:
    """Traduz um ResultadoValidacao (validacao_retry.py) em eventos de métricas.

    Registra um evento tipo="validacao" (sempre), um evento tipo="retry" para
    cada tentativa além da primeira, e um evento tipo="falha" se todas as
    tentativas se esgotaram sem sucesso. Não faz nada se coletor for None
    (execução sem métricas configuradas — não deve travar o pipeline).
    """
    if coletor is None:
        return

    coletor.registrar(
        fase=fase, tipo="validacao", nome=agente,
        status="sucesso" if resultado.sucesso else "falha",
        agente=agente, tentativas_usadas=resultado.tentativas_usadas,
    )
    for _ in range(resultado.tentativas_usadas - 1):
        coletor.registrar(fase=fase, tipo="retry", nome=agente, status="sucesso", agente=agente)
    if not resultado.sucesso:
        coletor.registrar(
            fase=fase, tipo="falha", nome=agente, status="falha",
            agente=agente, erro_final=resultado.erro_final,
        )


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
        coletor: ExecutionCollector | None = context.config.get("_metrics_collector")
        with _fase_medida(coletor, self.name):
            mode = resolve_mode(context.config)
            reviews: dict[str, ReviewSchema] = {}

            if mode is RunMode.MOCK:
                payloads = _load_mock(context).get("phase1_reviews", {})
                for rid in REVIEWERS:
                    if rid not in payloads:
                        raise RuntimeError(f"Mock sem parecer de Fase 1 para '{rid}'.")
                    resultado = validar_com_tentativas(
                        payloads[rid], validar_review, mode, rid,
                        run_id=context.run_id, fase=self.name,
                    )
                    reviews[rid] = resultado.dados
                    logger.info("Validação Fase 1 '%s': tentativas=%d", rid, resultado.tentativas_usadas)
                    _registrar_validacao(coletor, fase=self.name, agente=rid, resultado=resultado)
                    emit_event(
                        "parecer_validado", author=rid, phase=self.name, kind="agent",
                        attributes={"tentativas": resultado.tentativas_usadas, "validado_por": "grupo1"},
                    )
            else:
                article_text = data
                state = asyncio.run(_run_reviewers(article_text))
                for rid, cfg in REVIEWERS.items():
                    raw = state.get(cfg["output_key"])
                    if raw is None:
                        raise RuntimeError(f"Revisor '{rid}' não produziu parecer na Fase 1.")
                    payload = raw if isinstance(raw, dict) else json.loads(raw)
                    resultado = validar_com_tentativas(
                        payload, validar_review, mode, rid,
                        run_id=context.run_id, fase=self.name,
                    )
                    reviews[rid] = resultado.dados
                    logger.info("Validação Fase 1 '%s': tentativas=%d", rid, resultado.tentativas_usadas)
                    _registrar_validacao(coletor, fase=self.name, agente=rid, resultado=resultado)
                    emit_event(
                        "parecer_validado", author=rid, phase=self.name, kind="agent",
                        attributes={"tentativas": resultado.tentativas_usadas, "validado_por": "grupo1"},
                    )

            if _tool_completude is not None:
                for rid, review in reviews.items():
                    inicio = time.perf_counter()
                    audit = _tool_completude(review.model_dump())
                    duracao_ms = (time.perf_counter() - inicio) * 1000
                    logger.info(
                        "[completude] Fase 1 '%s': score=%.4f completo=%s",
                        rid, audit["score_completude"], audit["completo"],
                    )
                    if coletor is not None:
                        coletor.registrar(
                            fase=self.name, tipo="tool", nome="validar_completude",
                            status="sucesso", duracao_ms=duracao_ms,
                            agente=rid, score_completude=audit["score_completude"],
                            completo=audit["completo"],
                        )
                    emit_event(
                        "completude", author="grupo2", phase=self.name, kind="tool",
                        status="ok" if audit["completo"] else "alerta",
                        attributes={"revisor": rid, "score": audit["score_completude"], "completo": audit["completo"]},
                    )

            logger.info("Fase 1 (%s) concluída: %s pareceres validados.", mode.value, len(reviews))
            emit_event(
                "revisao_independente_concluida", author="sistema", phase=self.name,
                attributes={"pareceres": len(reviews), "revisores": list(reviews)},
            )
            return IndependentReviews(reviews=reviews)


# ---------------------------------------------------------------------------
# Fase 2 — Leitura Cruzada
# ---------------------------------------------------------------------------

class CrossReviewPhase(PipelinePhase[IndependentReviews, CrossReviews]):
    """Cada revisor lê os argumentos dos colegas e valida o parecer revisado."""

    name = "fase_2_leitura_cruzada"

    def run(self, data: IndependentReviews, context: PipelineContext) -> CrossReviews:
        coletor: ExecutionCollector | None = context.config.get("_metrics_collector")
        with _fase_medida(coletor, self.name):
            mode = resolve_mode(context.config)
            cross: dict[str, CrossReviewSchema] = {}

            if mode is RunMode.MOCK:
                payloads = _load_mock(context).get("phase2_cross_reviews", {})
                for rid in REVIEWERS:
                    if rid not in payloads:
                        raise RuntimeError(f"Mock sem parecer de Fase 2 para '{rid}'.")
                    resultado = validar_com_tentativas(
                        payloads[rid], validar_cross_review, mode, rid,
                        run_id=context.run_id, fase=self.name,
                    )
                    cross[rid] = resultado.dados
                    logger.info("Validação Fase 2 '%s': tentativas=%d", rid, resultado.tentativas_usadas)
                    _registrar_validacao(coletor, fase=self.name, agente=rid, resultado=resultado)
                    emit_event(
                        "cross_review_validado", author=rid, phase=self.name, kind="agent",
                        attributes={"tentativas": resultado.tentativas_usadas, "validado_por": "grupo1"},
                    )
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
                    resultado = validar_com_tentativas(
                        payload, validar_cross_review, mode, rid,
                        run_id=context.run_id, fase=self.name,
                    )
                    cross[rid] = resultado.dados
                    logger.info("Validação Fase 2 '%s': tentativas=%d", rid, resultado.tentativas_usadas)
                    _registrar_validacao(coletor, fase=self.name, agente=rid, resultado=resultado)
                    emit_event(
                        "cross_review_validado", author=rid, phase=self.name, kind="agent",
                        attributes={"tentativas": resultado.tentativas_usadas, "validado_por": "grupo1"},
                    )

            mudaram = [rid for rid, cr in cross.items() if cr.mudou_posicao]
            logger.info(
                "Fase 2 (%s) concluída. Revisores que mudaram de posição: %s.",
                mode.value,
                mudaram or "nenhum",
            )
            emit_event(
                "leitura_cruzada_concluida", author="sistema", phase=self.name,
                attributes={"mudaram_de_posicao": mudaram or []},
            )
            return CrossReviews(cross_reviews=cross)


# ---------------------------------------------------------------------------
# Fase 3 — Editor-Chefe
# ---------------------------------------------------------------------------

class EditorVerdictPhase(PipelinePhase[CrossReviews, EditorVerdictSchema]):
    """O Editor-Chefe sintetiza os pareceres finais em um veredito (EditorVerdictSchema)."""

    name = "fase_3_editor_chefe"

    def run(self, data: CrossReviews, context: PipelineContext) -> EditorVerdictSchema:
        coletor: ExecutionCollector | None = context.config.get("_metrics_collector")
        with _fase_medida(coletor, self.name):
            mode = resolve_mode(context.config)

            if mode is RunMode.MOCK:
                payload = _load_mock(context).get("phase3_verdict")
                if payload is None:
                    raise RuntimeError("Mock sem veredito de Fase 3 ('phase3_verdict').")
                resultado = validar_com_tentativas(
                    payload, validar_editor_verdict, mode, "editor",
                    run_id=context.run_id, fase=self.name,
                )
                verdict = resultado.dados
                logger.info("Validação Fase 3 'editor': tentativas=%d", resultado.tentativas_usadas)
                _registrar_validacao(coletor, fase=self.name, agente="editor", resultado=resultado)
                emit_event(
                    "veredito_validado", author="editor", phase=self.name, kind="agent",
                    attributes={"tentativas": resultado.tentativas_usadas, "validado_por": "grupo1"},
                )
            else:
                article_text: str = context.initial_input
                verdict_payload = asyncio.run(_run_editor(data.cross_reviews, article_text))
                resultado = validar_com_tentativas(
                    verdict_payload, validar_editor_verdict, mode, "editor",
                    run_id=context.run_id, fase=self.name,
                )
                verdict = resultado.dados
                logger.info("Validação Fase 3 'editor': tentativas=%d", resultado.tentativas_usadas)
                _registrar_validacao(coletor, fase=self.name, agente="editor", resultado=resultado)
                emit_event(
                    "veredito_validado", author="editor", phase=self.name, kind="agent",
                    attributes={"tentativas": resultado.tentativas_usadas, "validado_por": "grupo1"},
                )

            logger.info(
                "Fase 3 (%s) concluída. Decisão: %s (%s).",
                mode.value,
                verdict.decisao,
                ESCALA_VEREDITO[verdict.decisao],
            )

            if _tool_auditoria is not None:
                inicio = time.perf_counter()
                auditoria = _tool_auditoria(verdict.model_dump())
                duracao_ms = (time.perf_counter() - inicio) * 1000
                logger.info("[auditoria] %s", auditoria["resumo_auditoria"])
                if auditoria["requer_revisao_humana"]:
                    logger.warning("[auditoria] Veredito requer revisão humana.")
                context.config["_auditoria_veredito"] = auditoria
                if coletor is not None:
                    coletor.registrar(
                        fase=self.name, tipo="tool", nome="auditar_decisao_final",
                        status="sucesso", duracao_ms=duracao_ms,
                        requer_revisao_humana=auditoria["requer_revisao_humana"],
                    )
                emit_event(
                    "auditoria_veredito", author="grupo2", phase=self.name, kind="tool",
                    status="alerta" if auditoria["requer_revisao_humana"] else "ok",
                    attributes={
                        "requer_revisao_humana": auditoria["requer_revisao_humana"],
                        "resumo": auditoria["resumo_auditoria"],
                    },
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
    run_id: str | None = None,
    document_meta: dict | None = None,
) -> str:
    """Monta o relatório final em Markdown a partir das saídas das três fases.

    ``run_id`` e ``document_meta`` (contrato do documento extraído, SEM o texto
    completo) fazem o relatório apontar para a execução e o documento que o
    geraram — rastreabilidade pedida pela atividade do Grupo 3.
    """
    linhas: list[str] = []
    linhas.append("# Relatório Final do Peer Review")
    linhas.append("")
    linhas.append(f"- **Artigo:** {article_ref}")
    if run_id:
        linhas.append(f"- **Execução (run_id):** {run_id}")
    if document_meta:
        linhas.append(
            f"- **Documento:** {document_meta.get('document_id')} "
            f"({document_meta.get('filename')}, {document_meta.get('num_pages')} página(s), "
            f"extraído por {document_meta.get('extractor')} "
            f"{document_meta.get('extractor_version')} em "
            f"{document_meta.get('extraction_duration_s', 0):.2f} s)"
        )
        if document_meta.get("warnings"):
            linhas.append(f"- **Avisos da extração:** {'; '.join(document_meta['warnings'])}")
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
        coletor: ExecutionCollector | None = context.config.get("_metrics_collector")
        with _fase_medida(coletor, self.name):
            verdict = data
            phase1: IndependentReviews = context.get("fase_1_revisao_independente")
            phase2: CrossReviews = context.get("fase_2_leitura_cruzada")
            article_ref: str = context.config.get("article_ref", "entrada")
            document_meta: dict | None = context.config.get("document_meta")

            _tracer_atual = get_current_tracer()
            run_id = _tracer_atual.run_id if _tracer_atual is not None else context.run_id
            markdown = _render_report_md(
                article_ref=article_ref,
                reviews=phase1.reviews,
                cross=phase2.cross_reviews,
                verdict=verdict,
                run_id=run_id,
                document_meta=document_meta,
            )
            auditoria_veredito = context.config.get("_auditoria_veredito")
            structured = {
                "run_id": run_id,
                "article_ref": article_ref,
                "document": document_meta,
                "model": MODEL,
                "decisao": verdict.decisao,
                "decisao_rotulo": ESCALA_VEREDITO[verdict.decisao],
                "phase1_reviews": {rid: r.model_dump() for rid, r in phase1.reviews.items()},
                "phase2_cross_reviews": {
                    rid: c.model_dump() for rid, c in phase2.cross_reviews.items()
                },
                "phase3_verdict": verdict.model_dump(),
                "auditoria_veredito": auditoria_veredito,
            }

            if coletor is not None:
                coletor.registrar(
                    fase=self.name, tipo="decisao_final", nome="veredito_final",
                    status="sucesso", decisao=verdict.decisao,
                    requer_revisao_humana=bool(
                        auditoria_veredito and auditoria_veredito.get("requer_revisao_humana")
                    ),
                )

            logger.info("Fase 4 concluída: relatório final gerado.")
            emit_event(
                "relatorio_final_gerado", author="sistema", phase=self.name, kind="report",
                attributes={"decisao": verdict.decisao, "rotulo": ESCALA_VEREDITO[verdict.decisao]},
            )
            return FinalReport(markdown=markdown, data=structured)


# ---------------------------------------------------------------------------
# Builder do pipeline de peer review (as 4 fases, na ordem estrita)
# ---------------------------------------------------------------------------

def build_peer_review_pipeline(tracer=None) -> Pipeline:
    """Monta o pipeline de peer review com as quatro fases, na ordem oficial.

    ``tracer`` (opcional) liga a observabilidade: cada fase roda dentro de um
    span. Sem tracer, o comportamento é idêntico ao anterior.
    """
    return Pipeline(
        phases=[
            IndependentReviewPhase(),
            CrossReviewPhase(),
            EditorVerdictPhase(),
            FinalReportPhase(),
        ],
        name="peer_review",
        logger=logger,
        tracer=tracer,
    )


# ---------------------------------------------------------------------------
# Extração de PDF (fase 0, determinística) — Grupo 3
# ---------------------------------------------------------------------------

EXTRACTION_PHASE = "fase_0_extracao_pdf"


def extract_pdf_input(
    pdf_path: str | Path,
    extractor: PdfExtractor | None = None,
    tracer=None,
) -> ExtractedDocument:
    """Extrai um PDF real como etapa DETERMINÍSTICA do pipeline (fase 0).

    Não depende de decisão de LLM: é sempre chamada quando a entrada é um PDF.
    A extração entra na MESMA linha do tempo das demais fases (span de fase,
    com início/fim, status, extrator, páginas e duração em segundos). Avisos de
    qualidade viram eventos de alerta — a camada de validação (Grupo 1) decide
    se bloqueia ou encaminha; a ausência total de texto é erro claro aqui.
    """
    extractor = extractor or get_default_extractor()
    span_cm = (
        tracer.span(
            EXTRACTION_PHASE,
            kind="phase",
            phase=EXTRACTION_PHASE,
            author="grupo3",
            attributes={"pdf": str(pdf_path), "extrator": extractor.name},
        )
        if tracer is not None
        else nullcontext()
    )
    with span_cm:
        doc = extractor.extract(pdf_path)
        logger.info(
            "Fase 0 concluída: '%s' extraído por %s %s (%d página(s), %.2f s, %d aviso(s)).",
            doc.filename, doc.extractor, doc.extractor_version,
            doc.num_pages, doc.extraction_duration_s, len(doc.warnings),
        )
        emit_event(
            "documento_extraido", author="grupo3", phase=EXTRACTION_PHASE, kind="tool",
            status="alerta" if doc.warnings else "ok",
            attributes=doc.to_metadata(),
        )
        for aviso in doc.warnings:
            logger.warning("[extracao] %s", aviso)
            emit_event(
                "aviso_extracao", author="grupo3", phase=EXTRACTION_PHASE, kind="tool",
                status="alerta", attributes={"aviso": aviso, "document_id": doc.document_id},
            )
        if not doc.has_text:
            raise RuntimeError(
                f"A extração de '{doc.filename}' não produziu texto utilizável "
                f"({'; '.join(doc.warnings) or 'sem detalhes'}). Não é possível "
                "alimentar os agentes — forneça um PDF com texto digital ou "
                "habilite OCR no extrator."
            )
    return doc


# ---------------------------------------------------------------------------
# Demonstração ponta a ponta
# ---------------------------------------------------------------------------

def run_demo(
    mode: str | None = None,
    pdf_path: str | Path | None = None,
    extractor: PdfExtractor | None = None,
) -> FinalReport:
    """Roda o pipeline completo (PDF real ou artigo de exemplo) e salva os resultados.

    Parameters
    ----------
    mode:
        Modo de execução: ``"api"`` (Gemini real) ou ``"mock"`` (JSONs locais,
        offline). Se ``None``, cai para a variável de ambiente ``PIPELINE_MODE``
        e, na ausência dela, para ``"api"``.
    pdf_path:
        Caminho de um PDF real. Quando informado, a fase 0 (extração
        determinística) roda antes dos agentes e o texto extraído alimenta o
        pipeline. Quando omitido, usa o artigo de exemplo em texto puro
        (comportamento anterior, preservado para o modo mock/CI).
    extractor:
        Implementação de :class:`~extraction.PdfExtractor` a usar. ``None``
        usa o extrator padrão (LiteParse) — ponto de troca sem reescrever nada.
    """
    config: dict = {}
    if mode is not None:
        config["mode"] = mode
    if pdf_path is not None:
        config["article_ref"] = str(pdf_path)
    else:
        config["article_ref"] = "examples/example_article.txt"

    resolved = resolve_mode(config)
    # A chave de API só é exigida no modo que de fato chama o Gemini.
    if resolved is RunMode.API:
        _require_api_key()

    # Métricas de execução (Grupo 2): coletor guardado — se metrics/ não
    # existir por algum motivo, o pipeline roda normalmente, sem instrumentação.
    coletor: ExecutionCollector | None = ExecutionCollector() if ExecutionCollector is not None else None
    if coletor is not None:
        config["_metrics_collector"] = coletor

    # Observabilidade: cria uma execução identificável (run_id) e um trace local.
    tracer = create_tracer(trace_dir=LOG_DIR / "traces") if create_tracer is not None else None

    # run_id único da execução — usado também para organizar os artefatos de
    # saída em outputs/<run_id>/, evitando que uma execução sobrescreva
    # silenciosamente a anterior.
    run_id = tracer.run_id if tracer is not None else PipelineContext(None).run_id
    config["run_id"] = run_id

    pipeline = build_peer_review_pipeline(tracer=tracer)
    print(f"Pipeline '{pipeline.name}' [modo={resolved.value}] — fases: {pipeline.phase_names}")

    resumo = None

    out_dir = HERE / "outputs" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    def _run_and_save() -> FinalReport:
        nonlocal resumo
        # Fase 0 (apenas com PDF): extração determinística, dentro da mesma
        # linha do tempo (mesmo run_id) das demais fases.
        if pdf_path is not None:
            doc = extract_pdf_input(pdf_path, extractor=extractor, tracer=tracer)
            article_text = doc.text
            config["article_ref"] = doc.filename
            config["document_meta"] = doc.to_metadata()
            print(
                f"[peer_review] Fase 0: '{doc.filename}' extraído por {doc.extractor} "
                f"{doc.extractor_version} — {doc.num_pages} página(s) em "
                f"{doc.extraction_duration_s:.2f} s"
                + (f" — AVISOS: {'; '.join(doc.warnings)}" if doc.warnings else "")
            )
        else:
            article_path = HERE / "examples" / "example_article.txt"
            article_text = article_path.read_text(encoding="utf-8")

        result = pipeline.run(initial_input=article_text, config=config, verbose=True)
        report_local: FinalReport = result.final
        # Grupo 2 — agrega as métricas de execução (eventos, duração, retries) no
        # relatório, para que o JSON salvo já inclua o resumo auditável.
        if coletor is not None and gerar_resumo is not None:
            resumo = gerar_resumo(coletor.eventos, run_id=coletor.run_id)
            report_local.data["resumo_execucao"] = resumo.to_dict()
        # A gravação em disco é um passo pós-pipeline: envolvemos num span "report"
        # (irmão das fases, sob o run) para que "arquivos_gerados" fique ancorado
        # nele — e não solto sob o <run>, como acontecia ao emitir depois que o
        # span da fase 4 já havia fechado.
        span_cm = (
            tracer.span("relatorio_final", kind="report", phase="fase_4_relatorio_final")
            if tracer is not None
            else nullcontext()
        )
        with span_cm:
            (out_dir / "final_report.md").write_text(report_local.markdown, encoding="utf-8")
            (out_dir / "final_report.json").write_text(
                json.dumps(report_local.data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            emit_event(
                "arquivos_gerados", author="grupo3", phase="fase_4_relatorio_final", kind="report",
                attributes={
                    "markdown": str(out_dir / "final_report.md"),
                    "json": str(out_dir / "final_report.json"),
                    "decisao": report_local.data.get("decisao"),
                },
            )
        return report_local

    if tracer is not None:
        # Delimita a execução inteira (run_start/run_end): tudo abaixo entra na
        # mesma linha do tempo, inclusive a fase 0 (extração) e os eventos dos
        # Grupos 1 e 2.
        with tracer.run(
            name="peer_review",
            attributes={"modo": resolved.value, "artigo": config["article_ref"]},
        ):
            report = _run_and_save()
        trace_path = LOG_DIR / "traces" / f"{tracer.run_id}.jsonl"
    else:
        report = _run_and_save()
        trace_path = None

    print("OK: pipeline concluído.")
    print(f"run_id:                {report.data['run_id']}")
    print(f"Artefatos da execução: {out_dir}")
    print(f"Relatório (markdown): {out_dir / 'final_report.md'}")
    print(f"Relatório (json):     {out_dir / 'final_report.json'}")
    print(f"Log do pipeline:      {LOG_DIR / 'pipeline.log'}")
    print(f"Eventos de validação: {LOG_DIR / 'validacao_events.jsonl'}")
    if resumo is not None:
        imprimir_resumo(resumo)
        resumo_path = salvar_resumo_json(resumo, caminho=out_dir / "resumo_execucao.json")
        print(f"Resumo de execução:   {resumo_path}")
    if tracer is not None:
        print(f"Trace (run_id={tracer.run_id}): {trace_path}")
    return report


if __name__ == "__main__":
    # Permite escolher o modo pela linha de comando: `python pipeline.py mock`.
    # Para entrada por PDF, prefira o ponto de entrada oficial: `python main.py --pdf caminho.pdf`.
    cli_mode = sys.argv[1] if len(sys.argv) > 1 else None
    run_demo(mode=cli_mode)
