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

As fases 1-3 chamam o provedor de LLM escolhido em ``model_provider.py``
(Gemini, Maritaca ou OpenAI — a decisão é única e vale para todo o pipeline). A
fase 4 é pura formatação (sem LLM). Sem a chave do provedor selecionado, a demo
falha com mensagem clara; para rodar offline, use o modo mock.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
from model_provider import descricao_modelo, resolver_config  # noqa: E402
from validacao_retry import PipelineValidationError, validar_com_tentativas  # noqa: E402
from reviewer_agent import REVIEWERS, _require_api_key, _run_reviewers  # noqa: E402
from cross_review import _run_cross_review  # noqa: E402
from editor_agent import _run_editor  # noqa: E402
from extraction import ExtractedDocument, PdfExtractor, get_default_extractor  # noqa: E402

# Validação e resiliência da entrada por PDF (Grupo 1). Decide se o arquivo e o
# texto extraído podem entrar no pipeline (segue / alerta / bloqueia), emitindo
# eventos na mesma trilha de validacao_events.jsonl e do trace do Grupo 3.
from validacao_entrada import (  # noqa: E402
    DecisaoEntrada,
    EntradaInvalidaError,
    registrar_falha_extracao,
    validar_arquivo_pdf,
    validar_documento_extraido,
)

# Persistência e retomada de execuções (Grupo 1). CheckpointManager grava o
# resultado de cada fase em disco; numa retomada, fases já concluídas são
# puladas (ver Pipeline.run() em pipeline_base.py).
from persistencia import CheckpointManager  # noqa: E402

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

# Raiz dos artefatos de saída (``outputs/<run_id>/final_report.{md,json}``).
# Constante de módulo — e não ``HERE / "outputs"`` embutido no meio de
# ``run_demo`` — para que os testes possam redirecioná-la para um diretório
# temporário, como já fazem com LOG_DIR, sem escrever no repositório real.
OUTPUT_DIR = HERE / "outputs"

_handler = logging.FileHandler(LOG_DIR / "pipeline.log", encoding="utf-8")
_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
)
logger = logging.getLogger("pipeline")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(_handler)


# ---------------------------------------------------------------------------
# Modo de execução: API (provedor real) x MOCK (JSONs locais, offline)
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
    from metrics.adk_usage import definir_coletor_adk
except ImportError:
    ExecutionCollector = None
    gerar_resumo = None
    imprimir_resumo = None
    salvar_resumo_json = None
    definir_coletor_adk = None


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

    API = "api"     # chamadas reais ao provedor escolhido (ver model_provider)
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


def descrever_execucao(config: dict) -> str:
    """Descreve QUEM produziu os pareceres, para relatório e traces.

    No modo API devolve ``provedor:modelo`` (ex.: ``maritaca:sabiazinho-4``); no modo
    MOCK devolve ``mock (offline)``, em vez de anunciar um provedor que não foi
    chamado — o relatório não deve sugerir uma execução que não aconteceu.
    """
    if resolve_mode(config) is RunMode.MOCK:
        return "mock (offline)"
    return descricao_modelo(config)


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

    def serialize_output(self, data: IndependentReviews) -> dict:
        return {"reviews": {rid: r.model_dump() for rid, r in data.reviews.items()}}

    def deserialize_output(self, raw: dict) -> IndependentReviews:
        return IndependentReviews(
            reviews={rid: ReviewSchema(**v) for rid, v in raw["reviews"].items()}
        )

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
                    duracao_s = time.perf_counter() - inicio
                    logger.info(
                        "[completude] Fase 1 '%s': score=%.4f completo=%s",
                        rid, audit["score_completude"], audit["completo"],
                    )
                    if coletor is not None:
                        coletor.registrar(
                            fase=self.name, tipo="tool", nome="validar_completude",
                            status="sucesso", duracao_s=duracao_s,
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

    def serialize_output(self, data: CrossReviews) -> dict:
        return {"cross_reviews": {rid: cr.model_dump() for rid, cr in data.cross_reviews.items()}}

    def deserialize_output(self, raw: dict) -> CrossReviews:
        return CrossReviews(
            cross_reviews={rid: CrossReviewSchema(**v) for rid, v in raw["cross_reviews"].items()}
        )

    def run(self, data: IndependentReviews, context: PipelineContext) -> CrossReviews:
        coletor: ExecutionCollector | None = context.config.get("_metrics_collector")
        with _fase_medida(coletor, self.name):
            mode = resolve_mode(context.config)
            cross_review_enabled = context.config.get("cross_review_enabled", True)
            cross: dict[str, CrossReviewSchema] = {}

            if not cross_review_enabled:
                # Variante EXPERIMENTAL (Grupo 2 — benchmark de custo/eficiência,
                # ver src/benchmark/ablacao_cross_review.py): nenhuma chamada LLM
                # nesta fase. Cada revisor "mantém" o parecer da Fase 1 tal como
                # está — o contrato de saída (CrossReviewSchema) é o mesmo, só que
                # preenchido deterministicamente, então a Fase 3 em diante roda
                # sem nenhuma mudança.
                for rid, review in data.reviews.items():
                    payload = {
                        "revisor": rid,
                        "parecer_revisado": review.model_dump(),
                        "mudou_posicao": False,
                        "mudancas": [],
                        "resposta_aos_pares": (
                            "Leitura cruzada desativada nesta execução "
                            "(config['cross_review_enabled'] = False, variante "
                            "experimental do benchmark de custo/eficiência) — "
                            "parecer da Fase 1 mantido sem alteração."
                        ),
                    }
                    resultado = validar_com_tentativas(
                        payload, validar_cross_review, mode, rid,
                        run_id=context.run_id, fase=self.name,
                    )
                    cross[rid] = resultado.dados
                    logger.info("Fase 2 '%s': leitura cruzada desativada, parecer mantido.", rid)
                    _registrar_validacao(coletor, fase=self.name, agente=rid, resultado=resultado)
                    emit_event(
                        "cross_review_pulada", author=rid, phase=self.name, kind="agent",
                        attributes={"motivo": "cross_review_enabled=False"},
                    )
            elif mode is RunMode.MOCK:
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
                "Fase 2 (%s) concluída. Leitura cruzada %s. Revisores que mudaram de posição: %s.",
                mode.value,
                "ativa" if cross_review_enabled else "DESATIVADA (variante experimental)",
                mudaram or "nenhum",
            )
            emit_event(
                "leitura_cruzada_concluida", author="sistema", phase=self.name,
                attributes={"mudaram_de_posicao": mudaram or [], "leitura_cruzada_ativa": cross_review_enabled},
            )
            return CrossReviews(cross_reviews=cross)


# ---------------------------------------------------------------------------
# Fase 3 — Editor-Chefe
# ---------------------------------------------------------------------------

class EditorVerdictPhase(PipelinePhase[CrossReviews, EditorVerdictSchema]):
    """O Editor-Chefe sintetiza os pareceres finais em um veredito (EditorVerdictSchema)."""

    name = "fase_3_editor_chefe"

    def deserialize_output(self, raw: dict) -> EditorVerdictSchema:
        return EditorVerdictSchema(**raw)

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
                duracao_s = time.perf_counter() - inicio
                logger.info("[auditoria] %s", auditoria["resumo_auditoria"])
                if auditoria["requer_revisao_humana"]:
                    logger.warning("[auditoria] Veredito requer revisão humana.")
                context.config["_auditoria_veredito"] = auditoria
                if coletor is not None:
                    coletor.registrar(
                        fase=self.name, tipo="tool", nome="auditar_decisao_final",
                        status="sucesso", duracao_s=duracao_s,
                        requer_revisao_humana=auditoria["requer_revisao_humana"],
                    )
                    # A checagem de coerência roda por DENTRO da auditoria, mas
                    # precisa aparecer no resumo como verificação própria (task
                    # do Grupo 2) — inclusive quando NÃO rodou, para o resumo
                    # denunciar a lacuna em vez de omiti-la.
                    inconsistencias = auditoria.get("inconsistencias") or []
                    tipos_inconsistencias = sorted(
                        {i.get("tipo", "desconhecido") for i in inconsistencias if isinstance(i, dict)}
                    )
                    if auditoria.get("coerencia_executada"):
                        coletor.registrar(
                            fase=self.name, tipo="tool", nome="checar_coerencia",
                            status="aviso" if inconsistencias else "sucesso",
                            quantidade_inconsistencias=len(inconsistencias),
                            tipos_inconsistencias=tipos_inconsistencias,
                            **(
                                {
                                    "mensagem": (
                                        f"checar_coerencia: {len(inconsistencias)} "
                                        f"inconsistência(s) no veredito "
                                        f"({', '.join(tipos_inconsistencias)})"
                                    )
                                }
                                if inconsistencias
                                else {}
                            ),
                        )
                    else:
                        coletor.registrar(
                            fase=self.name, tipo="tool", nome="checar_coerencia",
                            status="aviso",
                            quantidade_inconsistencias=0,
                            mensagem=auditoria.get("aviso_coerencia")
                            or "checar_coerencia não executada",
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
    modelo_ref: str,
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
    linhas.append(f"- **Modelo:** {modelo_ref}")
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

    def deserialize_output(self, raw: dict) -> FinalReport:
        return FinalReport(markdown=raw["markdown"], data=raw["data"])

    def run(self, data: EditorVerdictSchema, context: PipelineContext) -> FinalReport:
        coletor: ExecutionCollector | None = context.config.get("_metrics_collector")
        with _fase_medida(coletor, self.name):
            verdict = data
            phase1: IndependentReviews = context.get("fase_1_revisao_independente")
            phase2: CrossReviews = context.get("fase_2_leitura_cruzada")
            article_ref: str = context.config.get("article_ref", "entrada")
            document_meta: dict | None = context.config.get("document_meta")
            modelo_ref = descrever_execucao(context.config)

            _tracer_atual = get_current_tracer()
            run_id = _tracer_atual.run_id if _tracer_atual is not None else context.run_id
            markdown = _render_report_md(
                article_ref=article_ref,
                modelo_ref=modelo_ref,
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
                "model": modelo_ref,
                "cross_review_enabled": context.config.get("cross_review_enabled", True),
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
    run_id: str | None = None,
    coletor: "ExecutionCollector | None" = None,
) -> ExtractedDocument:
    """Extrai um PDF real como etapa DETERMINÍSTICA do pipeline (fase 0).

    Não depende de decisão de LLM: é sempre chamada quando a entrada é um PDF.
    A extração entra na MESMA linha do tempo das demais fases (span de fase,
    com início/fim, status, extrator, páginas e duração em segundos). Avisos de
    qualidade viram eventos de alerta.

    Após a extração, a camada de validação do Grupo 1
    (``validar_documento_extraido``) decide se o texto pode seguir, se segue com
    alerta (revisão humana) ou se é bloqueado — registrando um evento
    estruturado ligado ao ``run_id``. Texto vazio/irrecuperável levanta
    ``EntradaInvalidaError`` (subclasse de ``RuntimeError``): a entrada nunca é
    aceita em silêncio.

    ``coletor`` (Grupo 2, opcional): quando a validação gera alerta, também
    registra um evento ``tipo="validacao", status="aviso"`` no
    ``ExecutionCollector`` — é o mesmo gatilho que ``metrics/resumo.py`` usa
    para popular a seção "Alertas" do resumo final. Sem isso, o alerta ficava
    só em ``validacao_events.jsonl``/trace e nunca aparecia no resumo impresso
    ao fim de ``python main.py``.
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
        # Grupo 1 — se o parser do Grupo 3 falhar (PDF protegido por senha ou
        # corrompido de um jeito que só o extrator detecta), a falha vira um
        # evento estruturado 'bloqueado' ligado ao run_id, e não um traceback solto.
        try:
            doc = extractor.extract(pdf_path)
        except Exception as exc:  # noqa: BLE001 - registra e re-levanta como bloqueio de entrada
            erro_extracao = registrar_falha_extracao(
                pdf_path, exc, run_id=run_id, fase=EXTRACTION_PHASE
            )
            if coletor is not None:
                coletor.registrar(
                    fase=EXTRACTION_PHASE, tipo="falha", nome="entrada_pdf",
                    status="falha", erro_final=str(exc),
                )
            raise erro_extracao from exc
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
        # Grupo 2 — a fase 0 entra no coletor como as demais fases, usando a
        # duração que o extrator do Grupo 3 já mede (extraction_duration_s).
        # Sem isso, o tempo de extração só aparecia no trace e na diferença
        # entre duracao_total_s e duracao_soma_fases_s do resumo.
        if coletor is not None:
            coletor.registrar(
                fase=EXTRACTION_PHASE, tipo="fase", nome=EXTRACTION_PHASE,
                status="sucesso", duracao_s=doc.extraction_duration_s,
                extrator=f"{doc.extractor} {doc.extractor_version}",
                paginas=doc.num_pages, avisos_extracao=len(doc.warnings),
            )
        for aviso in doc.warnings:
            logger.warning("[extracao] %s", aviso)
            emit_event(
                "aviso_extracao", author="grupo3", phase=EXTRACTION_PHASE, kind="tool",
                status="alerta", attributes={"aviso": aviso, "document_id": doc.document_id},
            )
        # Grupo 1 — valida o documento extraído. Segue (ok/alerta) ou bloqueia
        # (EntradaInvalidaError), sempre deixando um evento estruturado.
        try:
            resultado_validacao = validar_documento_extraido(
                doc, run_id=run_id, fase=EXTRACTION_PHASE, exigir=True,
            )
        except EntradaInvalidaError as exc:
            if coletor is not None:
                coletor.registrar(
                    fase=EXTRACTION_PHASE, tipo="falha", nome="entrada_pdf",
                    status="falha", erro_final=str(exc),
                )
            raise
        if coletor is not None and resultado_validacao.decisao is DecisaoEntrada.ALERTA:
            coletor.registrar(
                fase=EXTRACTION_PHASE, tipo="validacao", nome="entrada_pdf",
                status="aviso",
                mensagem="; ".join(resultado_validacao.motivos),
                requer_revisao_humana=resultado_validacao.requer_revisao_humana,
            )
    return doc


# ---------------------------------------------------------------------------
# Estado auxiliar da execução (retomada) — Grupo 1
# ---------------------------------------------------------------------------
# Tudo que precisa sobreviver a uma retomada mas NÃO é saída de fase vive no
# sidecar `<run_id>.estado.json` (ver persistencia.py): as métricas já
# coletadas, a auditoria do veredito e o tempo de parede acumulado. Sem isso,
# uma retomada reconstrói os dados das fases (que estão nos checkpoints) mas
# perde tudo que é lateral a elas, e termina com um resumo que descreve só o
# pedaço re-executado.

ESTADO_VERSAO = 1


def _estado_vazio(execucoes: int = 0) -> dict:
    """Estado auxiliar sem nenhum histórico (execução nova ou `--force`)."""
    return {
        "versao": ESTADO_VERSAO,
        "execucoes": execucoes,
        "duracao_acumulada_s": 0.0,
        "auditoria_veredito": None,
        "eventos_metricas": [],
    }


def _alertar_lacunas_no_resumo(resumo, ckpt: CheckpointManager) -> list[str]:
    """Denuncia, no próprio resumo, fases concluídas que não aparecem nele.

    Rede de segurança contra a regressão que esta correção resolve: se uma fase
    tem checkpoint em disco mas nenhuma duração no resumo, as métricas dela se
    perderam em algum lugar do caminho. Melhor um alerta explícito num resumo
    auditável do que um resumo silenciosamente incompleto — que é exatamente o
    que acontecia antes.
    """
    if resumo is None:
        return []
    faltando = sorted(
        fase for fase in ckpt.fases_concluidas() if fase not in resumo.duracao_por_fase_s
    )
    if faltando:
        resumo.alertas.append(
            "Fases com checkpoint salvo que não aparecem neste resumo "
            f"(métricas não restauradas): {', '.join(faltando)}"
        )
    return faltando


def _relatorio_ja_concluido(ckpt: CheckpointManager, out_dir: Path) -> FinalReport | None:
    """Recupera o relatório de uma execução já concluída, sem re-executar nada.

    Preferência pelos arquivos gravados em ``outputs/<run_id>/`` (é o artefato
    oficial da execução). Se eles tiverem sido apagados, reconstrói a partir do
    checkpoint da fase 4 — e nesse caso vale regravá-los, porque aí não há
    relatório anterior sendo sobrescrito, só um artefato ausente sendo
    restaurado. ``None`` quando não há nem uma coisa nem outra.
    """
    md_path = out_dir / "final_report.md"
    json_path = out_dir / "final_report.json"
    if md_path.exists() and json_path.exists():
        return FinalReport(
            markdown=md_path.read_text(encoding="utf-8"),
            data=json.loads(json_path.read_text(encoding="utf-8")),
        )

    raw = ckpt.load(FinalReportPhase.name)
    if raw is None:
        return None
    report = FinalReportPhase().deserialize_output(raw)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report.markdown, encoding="utf-8")
    json_path.write_text(
        json.dumps(report.data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        "[peer_review] Relatório final restaurado do checkpoint da fase 4 "
        f"(os arquivos em {out_dir} não existiam mais)."
    )
    return report


# ---------------------------------------------------------------------------
# Demonstração ponta a ponta
# ---------------------------------------------------------------------------

def run_demo(
    mode: str | None = None,
    pdf_path: str | Path | None = None,
    extractor: PdfExtractor | None = None,
    run_id: str | None = None,
    cross_review: bool | None = None,
    forcar: bool = False,
) -> FinalReport:
    """Roda o pipeline completo (PDF real ou artigo de exemplo) e salva os resultados.

    Parameters
    ----------
    mode:
        Modo de execução: ``"api"`` (provedor real) ou ``"mock"`` (JSONs locais,
        offline). Se ``None``, cai para a variável de ambiente ``PIPELINE_MODE``
        e, na ausência dela, para ``"api"``.

        O provedor e o modelo do modo API vêm de ``model_provider.resolver_config``
        (``LLM_PROVIDER`` / ``LLM_MODEL``), não desta função.
    pdf_path:
        Caminho de um PDF real. Quando informado, a fase 0 (extração
        determinística) roda antes dos agentes e o texto extraído alimenta o
        pipeline. Quando omitido, usa o artigo de exemplo em texto puro
        (comportamento anterior, preservado para o modo mock/CI).
    extractor:
        Implementação de :class:`~extraction.PdfExtractor` a usar. ``None``
        usa o extrator padrão (LiteParse) — ponto de troca sem reescrever nada.
    run_id:
        Identificador de uma execução anterior a retomar. Quando informado,
        os checkpoints já salvos em ``src/logs/checkpoints/<run_id>/`` são
        carregados e as fases já concluídas são puladas (ver
        ``Pipeline.run()`` em ``pipeline_base.py``). Isso inclui a fase 0: um
        PDF já extraído NÃO é extraído de novo. Se ``pdf_path``/``mode``
        não forem passados explicitamente, são recuperados do arquivo de
        metadados gravado na execução original
        (``src/logs/checkpoints/<run_id>.meta.json``). ``None`` (padrão)
        inicia uma execução nova, com um run_id gerado automaticamente.
    cross_review:
        ``True`` (padrão/``None``) roda a Fase 2 normalmente: cada revisor lê
        os argumentos dos colegas e pode revisar sua posição, com uma chamada
        LLM por revisor. ``False`` roda a variante EXPERIMENTAL do benchmark
        de custo/eficiência (Grupo 2): a Fase 2 não faz nenhuma chamada LLM —
        cada revisor "mantém" o parecer da Fase 1 tal como está — e o
        pipeline segue inalterado a partir da Fase 3 em diante, porque
        ``CrossReviewSchema`` continua sendo o contrato de saída da fase,
        só que preenchido deterministicamente. Existe para medir o benefício
        (ou não) da leitura cruzada frente ao custo/tempo adicional dela —
        ver ``src/benchmark/ablacao_cross_review.py``.
    forcar:
        Só faz sentido junto com ``run_id``. Descarta os checkpoints e as
        métricas da execução e roda TUDO de novo sob o mesmo ``run_id``,
        regravando os artefatos. É o jeito explícito de refazer uma execução
        já concluída — que, por padrão, é um no-op justamente para não
        sobrescrever um relatório bom.
    """
    ckpt_dir = LOG_DIR / "checkpoints"
    meta_salva: dict = {}

    if run_id:
        # A retomada precisa do gerenciador ANTES de resolver modo/PDF: é dele
        # que saem os metadados da execução original e o estado auxiliar.
        ckpt_anterior = CheckpointManager(ckpt_dir, run_id)
        meta_salva = ckpt_anterior.carregar_estado("meta") or {}
        fases_ja_concluidas = ckpt_anterior.fases_concluidas()

        if not meta_salva and not fases_ja_concluidas:
            # Retomar um run_id inexistente não é erro fatal (a execução segue
            # com esse identificador), mas passar batido seria enganoso: o
            # usuário acharia que retomou algo.
            print(
                f"[peer_review] AVISO: nenhuma execução anterior encontrada para "
                f"run_id={run_id} em {ckpt_dir}. Seguindo como execução NOVA com esse id."
            )

        # Recupera pdf_path/mode/cross_review da execução original quando não
        # informados explicitamente nesta chamada — sem isso, retomar com um
        # PDF, modo ou variante diferente do original geraria um relatório
        # inconsistente sem nenhum erro visível.
        if pdf_path is None and meta_salva.get("pdf_path"):
            pdf_path = meta_salva["pdf_path"]
        if mode is None and meta_salva.get("mode"):
            mode = meta_salva["mode"]
        if cross_review is None and "cross_review" in meta_salva:
            cross_review = meta_salva["cross_review"]

        if meta_salva.get("status") == "concluida" and not forcar:
            # Execução já terminou: não há nada a retomar. Re-executar aqui
            # significaria regravar final_report.md/json e resumo_execucao.json
            # por cima de artefatos completos — o pior resultado possível para
            # quem só queria conferir uma execução antiga.
            relatorio = _relatorio_ja_concluido(ckpt_anterior, OUTPUT_DIR / run_id)
            if relatorio is not None:
                print(
                    f"[peer_review] Execução {run_id} já está CONCLUÍDA "
                    f"({meta_salva.get('concluida_em', 'data desconhecida')}). "
                    "Nada foi re-executado e nenhum artefato foi regravado."
                )
                print(f"Artefatos preservados em: {OUTPUT_DIR / run_id}")
                print(f"Para refazer esta execução: python main.py --resume {run_id} --force")
                return relatorio
            print(
                f"[peer_review] AVISO: {run_id} está marcada como concluída, mas não "
                "sobrou relatório nem checkpoint da fase 4. Re-executando o que faltar."
            )

        if forcar:
            removidas = ckpt_anterior.limpar_fases()
            # As métricas salvas também vão embora: como TODAS as fases rodam
            # de novo, mantê-las faria cada fase ser contada duas vezes no
            # resumo. O trace e o validacao_events.jsonl continuam acumulando —
            # o histórico forense da execução não se perde.
            estado_atual = ckpt_anterior.carregar_estado("estado") or {}
            ckpt_anterior.salvar_estado(
                "estado", _estado_vazio(int(estado_atual.get("execucoes", 0)))
            )
            print(
                f"[peer_review] --force: {len(removidas)} checkpoint(s) descartado(s) "
                f"({', '.join(removidas) or 'nenhum'}). A execução {run_id} será refeita do zero."
            )

    if cross_review is None:
        cross_review = True

    config: dict = {"cross_review_enabled": cross_review}
    if mode is not None:
        config["mode"] = mode
    if pdf_path is not None:
        config["article_ref"] = str(pdf_path)
    else:
        config["article_ref"] = "examples/example_article.txt"

    resolved = resolve_mode(config)
    # A chave só é exigida no modo que de fato chama um provedor — e a checagem
    # já aponta a variável do serviço selecionado (GOOGLE/MARITACA/OPENAI).
    if resolved is RunMode.API:
        _require_api_key()

    # Custo estimado (Grupo 2): NÃO é resolvido uma vez aqui para a execução
    # inteira — gerar_resumo() precifica cada chamada LLM individualmente
    # pelo modelo que de fato respondeu (metrics/precos_modelos.py,
    # resolver_precos_por_modelo), porque papéis diferentes podem usar
    # modelos diferentes (ex.: o editor configurado à parte) e um fallback
    # pode trocar o modelo que responde no meio da execução — um preço único
    # aplicado ao total agregado erraria os dois casos. Preço via variáveis
    # de ambiente (GRUPO2_PRECO_USD_MILHAO_TOKENS_*) continua valendo para
    # TODAS as chamadas, resolvido automaticamente dentro de gerar_resumo.

    # Observabilidade: cria uma execução identificável (run_id) e um trace local.
    # Se `run_id` foi passado (retomada), o tracer usa o MESMO identificador em
    # vez de gerar um novo, para que trace, checkpoint e métricas continuem
    # apontando para a mesma execução original.
    tracer = (
        create_tracer(trace_dir=LOG_DIR / "traces", run_id=run_id)
        if create_tracer is not None else None
    )

    # run_id único da execução — usado também para organizar os artefatos de
    # saída em outputs/<run_id>/, evitando que uma execução sobrescreva
    # silenciosamente a anterior. Mantém o `run_id` explícito no fallback (não
    # só um novo uuid) para não perder silenciosamente uma retomada quando o
    # pacote de observabilidade não estiver disponível.
    run_id = tracer.run_id if tracer is not None else (run_id or PipelineContext(None).run_id)
    config["run_id"] = run_id

    # Persistência e retomada (Grupo 1): checkpoint por fase em disco. O
    # sidecar `<run_id>.meta.json` (irmão da pasta `<run_id>/`, não dentro
    # dela — para não poluir `fases_concluidas()`) preserva pdf_path/mode da
    # PRIMEIRA execução, para uma retomada futura recuperá-los, e carrega o
    # status que distingue "interrompida" de "concluída". A variante com/sem
    # leitura cruzada também fica congelada na primeira execução, para a
    # retomada não trocar de experimento silenciosamente.
    ckpt = CheckpointManager(ckpt_dir, run_id)
    meta = dict(meta_salva)
    meta.setdefault("pdf_path", str(pdf_path) if pdf_path else None)
    meta.setdefault("mode", resolved.value)
    meta.setdefault("cross_review", cross_review)
    meta["status"] = "em_andamento"
    ckpt.salvar_estado("meta", meta)

    # Estado auxiliar da execução: métricas já coletadas e auditoria do veredito
    # de tentativas anteriores. É o que faz a retomada terminar com um resumo da
    # execução INTEIRA, e não só das fases que rodaram depois da falha.
    # Tolerante a arquivo ilegível: perder as métricas acumuladas degrada o
    # resumo, mas abortar por causa delas custaria a retomada inteira. O `meta`
    # acima é lido de forma ESTRITA justamente por decidir conduta (se a
    # execução já foi concluída) — ver CheckpointManager.carregar_estado.
    estado_anterior = ckpt.carregar_estado("estado", tolerar_corrompido=True) or _estado_vazio()
    execucoes = int(estado_anterior.get("execucoes", 0)) + 1

    # Métricas de execução (Grupo 2): coletor guardado — se metrics/ não
    # existir por algum motivo, o pipeline roda normalmente, sem instrumentação.
    # O coletor herda o run_id da execução: métricas, trace, eventos de
    # validação e relatório compartilham o MESMO identificador (PR #3).
    coletor: ExecutionCollector | None = (
        ExecutionCollector(run_id=run_id) if ExecutionCollector is not None else None
    )
    eventos_restaurados = 0
    if coletor is not None:
        config["_metrics_collector"] = coletor
        # Tokens reais (usage_metadata do ADK): registra o coletor no consumidor
        # plugado nos loops dos agentes (metrics/adk_usage.py) — mesmo padrão de
        # contexto global do tracer; sem execução instrumentada, é no-op lá.
        if definir_coletor_adk is not None:
            definir_coletor_adk(coletor)
        eventos_salvos = estado_anterior.get("eventos_metricas") or []
        if eventos_salvos:
            eventos_restaurados = coletor.restaurar(
                eventos_salvos,
                duracao_anterior_s=float(estado_anterior.get("duracao_acumulada_s") or 0.0),
            )
            print(
                f"[peer_review] Retomada: {eventos_restaurados} evento(s) de métrica "
                "restaurado(s) das fases já concluídas."
            )

    # A auditoria do veredito é produzida pela fase 3 e consumida pela fase 4
    # através do config. Numa retomada em que a fase 3 vem do checkpoint, ela
    # não é reproduzida — e o relatório final sairia com auditoria_veredito=null.
    auditoria_veredito = estado_anterior.get("auditoria_veredito")
    if auditoria_veredito is not None:
        config["_auditoria_veredito"] = auditoria_veredito

    def _salvar_estado(
        contexto: PipelineContext | None = None, *, eventos: list[dict] | None = None,
    ) -> None:
        """Persiste o estado auxiliar no ponto em que a execução está agora.

        Chamado nas FRONTEIRAS de sucesso (fase 0 extraída, cada fase concluída,
        fim da execução) e também no caminho de FALHA (bloco ``except`` de
        ``run_demo``), neste último caso com ``eventos`` já filtrado para
        excluir os que sinalizam a própria falha — ver o chamador. Sem isso, o
        tempo de parede e os eventos de sucesso (chamadas LLM, tools) que a
        tentativa que falhou já havia produzido seriam perdidos: eles só
        existem na memória do processo que caiu, e a retomada partiria do
        último sucesso registrado, sem nenhum rastro do que aconteceu depois
        dele. Excluir os eventos tipo="falha" evita que a retomada
        bem-sucedida herde `quantidade_falhas`/`status_final="falha"` de um
        problema já resolvido — o rastro completo da falha continua no trace e
        em validacao_events.jsonl, que são append-only.

        ``duracao_acumulada_s`` nunca é filtrada: é só um número (o relógio de
        parede do processo inteiro via ``coletor.duracao_execucao_s``), não um
        evento — por isso é sempre persistida, inclusive na falha.

        ``contexto`` é o PipelineContext da fase que acabou de concluir. Ele
        precisa vir por parâmetro porque ``PipelineContext`` COPIA o config
        recebido: o que a fase 3 grava em ``context.config["_auditoria_veredito"]``
        nunca chega ao ``config`` desta função.
        """
        nonlocal auditoria_veredito
        if contexto is not None and contexto.config.get("_auditoria_veredito") is not None:
            auditoria_veredito = contexto.config["_auditoria_veredito"]
        eventos_para_salvar = (
            eventos if eventos is not None
            else (coletor.eventos_como_dict() if coletor is not None else [])
        )
        ckpt.salvar_estado(
            "estado",
            {
                "versao": ESTADO_VERSAO,
                "execucoes": execucoes,
                "duracao_acumulada_s": (
                    coletor.duracao_execucao_s if coletor is not None else 0.0
                ),
                "auditoria_veredito": auditoria_veredito,
                "eventos_metricas": eventos_para_salvar,
            },
        )

    pipeline = build_peer_review_pipeline(tracer=tracer)
    print(
        f"Pipeline '{pipeline.name}' [modo={resolved.value} · "
        f"modelo={descrever_execucao(config)} · "
        f"leitura_cruzada={'ativa' if cross_review else 'DESATIVADA (experimental)'}] — "
        f"fases: {pipeline.phase_names}"
    )

    resumo = None
    fases_restauradas: list[str] = []

    out_dir = OUTPUT_DIR / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    def _run_and_save() -> FinalReport:
        nonlocal resumo
        # Fase 0 (apenas com PDF): extração determinística, dentro da mesma
        # linha do tempo (mesmo run_id) das demais fases.
        if pdf_path is not None:
            doc_salvo = ckpt.load(EXTRACTION_PHASE)
            if doc_salvo is not None:
                # Retomada: a extração já aconteceu e seu resultado está em
                # disco. Repeti-la seria refazer trabalho determinístico que já
                # deu certo — e exigiria que o PDF original ainda existisse no
                # mesmo caminho. O arquivo NÃO é revalidado aqui: ele já passou
                # por validar_arquivo_pdf + validar_documento_extraido na
                # execução original, e esses eventos estão no
                # validacao_events.jsonl deste mesmo run_id.
                doc = ExtractedDocument.from_dict(doc_salvo)
                fases_restauradas.append(EXTRACTION_PHASE)
                logger.info(
                    "Fase 0 restaurada do checkpoint: '%s' (%d página(s)) — extração não repetida.",
                    doc.filename, doc.num_pages,
                )
                print(
                    f"[peer_review] Fase 0: '{doc.filename}' (checkpoint restaurado) — "
                    "PDF não foi extraído de novo"
                )
                emit_event(
                    "documento_restaurado", author="grupo1", phase=EXTRACTION_PHASE, kind="tool",
                    attributes={**doc.to_metadata(), "checkpoint_restaurado": True},
                )
            else:
                # Grupo 1 — validação do ARQUIVO antes de extrair (inexistente,
                # formato incorreto, corrompido, protegido). Bloqueia sem retry.
                validar_arquivo_pdf(pdf_path, run_id=run_id, fase=EXTRACTION_PHASE, exigir=True)
                doc = extract_pdf_input(
                    pdf_path, extractor=extractor, tracer=tracer, run_id=run_id, coletor=coletor,
                )
                # A fase 0 vira checkpoint como as demais: é uma etapa concluída
                # da execução, e é o que permite pular a extração na retomada.
                ckpt.save(EXTRACTION_PHASE, doc.to_dict())
                _salvar_estado()
                print(
                    f"[peer_review] Fase 0: '{doc.filename}' extraído por {doc.extractor} "
                    f"{doc.extractor_version} — {doc.num_pages} página(s) em "
                    f"{doc.extraction_duration_s:.2f} s"
                    + (f" — AVISOS: {'; '.join(doc.warnings)}" if doc.warnings else "")
                )
            article_text = doc.text
            config["article_ref"] = doc.filename
            config["document_meta"] = doc.to_metadata()
        else:
            article_path = HERE / "examples" / "example_article.txt"
            article_text = article_path.read_text(encoding="utf-8")

        if resolved is RunMode.API:
            # Registra na linha do tempo COM QUEM esta execução falou. É o que
            # torna comparável um trace rodado no Gemini e outro no Maritaca.
            escolha = resolver_config(config)
            emit_event(
                "modelo_selecionado", author="sistema", kind="call",
                attributes={
                    "provedor": escolha.provider.value,
                    "modelo": escolha.model,
                    "structured_output": escolha.structured_output,
                },
            )

        result = pipeline.run(
            initial_input=article_text, config=config, verbose=True, checkpoint_manager=ckpt,
            # Salva as métricas na MESMA fronteira em que o checkpoint da fase é
            # gravado: se a fase seguinte falhar, checkpoint e métricas ficam
            # descrevendo exatamente o mesmo ponto da execução.
            on_phase_end=lambda _fase, ctx: _salvar_estado(ctx),
        )
        fases_restauradas.extend(result.fases_restauradas)
        report_local: FinalReport = result.final
        # Grupo 2 — agrega as métricas de execução (eventos, duração, retries) no
        # relatório, para que o JSON salvo já inclua o resumo auditável. Numa
        # retomada, o coletor já vem semeado com os eventos das fases
        # restauradas, então este resumo cobre a execução inteira.
        if coletor is not None and gerar_resumo is not None:
            resumo = gerar_resumo(
                coletor.eventos,
                run_id=coletor.run_id,
                duracao_total_s=coletor.duracao_execucao_s,
            )
            _alertar_lacunas_no_resumo(resumo, ckpt)
            report_local.data["resumo_execucao"] = resumo.to_dict()
        # Proveniência da retomada: o relatório passa a dizer o que rodou agora
        # e o que veio do disco, em vez de apresentar tudo como se fosse de uma
        # única passagem.
        report_local.data["retomada"] = {
            "execucoes": execucoes,
            "fases_restauradas": list(fases_restauradas),
            "eventos_metricas_restaurados": eventos_restaurados,
        }
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
        # Só AQUI a execução vira "concluída": depois que os artefatos finais
        # estão em disco. Marcar antes deixaria um run_id que se diz pronto sem
        # relatório algum — e uma retomada dele viraria um no-op sem entrega.
        _salvar_estado()
        ckpt.salvar_estado(
            "meta",
            {
                **meta,
                "status": "concluida",
                "concluida_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
        )
        return report_local

    try:
        if tracer is not None:
            # Delimita a execução inteira (run_start/run_end): tudo abaixo entra na
            # mesma linha do tempo, inclusive a fase 0 (extração) e os eventos dos
            # Grupos 1 e 2.
            with tracer.run(
                name="peer_review",
                attributes={
                    "modo": resolved.value,
                    "artigo": config["article_ref"],
                    "modelo": descrever_execucao(config),
                    "cross_review_enabled": cross_review,
                },
            ):
                report = _run_and_save()
            trace_path = LOG_DIR / "traces" / f"{tracer.run_id}.jsonl"
        else:
            report = _run_and_save()
            trace_path = None
    except EntradaInvalidaError:
        # Bloqueio de entrada por PDF (Grupo 1) não é retomável — o mesmo
        # arquivo continuaria inválido — então propaga direto, sem o resumo
        # de falha/retomada abaixo (main.py já trata esse erro com mensagem
        # própria e código de saída != 0).
        raise
    except Exception as exc:
        # Resumo parcial: mostra o que foi concluído, onde parou e os
        # checkpoints disponíveis para retomada, mesmo que o pipeline tenha
        # falhado no meio.
        fases_ok = ckpt.fases_concluidas()
        print(f"\nFALHA | run_id={run_id} | {type(exc).__name__}: {exc}")
        print(f"Fases concluídas antes da falha: {fases_ok or 'nenhuma'}")
        for fase in fases_ok:
            print(f"  checkpoint: {ckpt.caminho(fase)}")
        if coletor is not None:
            # Persiste o que a tentativa que falhou já tinha de fato produzido
            # (tempo de parede + eventos de sucesso), para a retomada não
            # partir como se nada tivesse acontecido depois do último
            # checkpoint. Os eventos que sinalizam a própria falha ficam de
            # fora — ver docstring de _salvar_estado.
            eventos_sem_falha = [
                e for e in coletor.eventos_como_dict() if e.get("status") != "falha"
            ]
            _salvar_estado(eventos=eventos_sem_falha)
        if coletor is not None and gerar_resumo is not None:
            resumo_parcial = gerar_resumo(
                coletor.eventos, run_id=run_id, duracao_total_s=coletor.duracao_execucao_s,
            )
            if imprimir_resumo is not None:
                imprimir_resumo(resumo_parcial)
            if salvar_resumo_json is not None:
                resumo_path = salvar_resumo_json(
                    resumo_parcial, caminho=out_dir / "resumo_execucao_parcial.json",
                )
                print(f"Resumo parcial salvo: {resumo_path}")
        print(f"Para retomar: python main.py --resume {run_id}")
        raise

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
    # Para entrada por PDF ou retomada, prefira o ponto de entrada oficial:
    # `python main.py --pdf caminho.pdf` / `python main.py --resume <run_id>`.
    # Um segundo argumento posicional aqui também retoma, por conveniência:
    # `python pipeline.py mock run_abc123`.
    cli_mode = sys.argv[1] if len(sys.argv) > 1 else None
    cli_run_id = sys.argv[2] if len(sys.argv) > 2 else None
    run_demo(mode=cli_mode, run_id=cli_run_id)
