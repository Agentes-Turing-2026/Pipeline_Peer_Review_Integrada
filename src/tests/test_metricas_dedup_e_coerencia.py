"""Testes de integração — coerência visível no resumo e fase 0 nas métricas (Grupo 2).

Cobrem os dois requisitos do PDF da task que ainda não tinham teste:

- "A verificação de coerência deve aparecer mesmo quando for executada dentro
  da auditoria final" — o pipeline registra um evento ``tipo="tool"``,
  ``nome="checar_coerencia"`` no ``ExecutionCollector``, inclusive (como
  aviso) quando a checagem NÃO rodou.
- "Incluir no resumo (...) o tempo de extração do PDF emitido pelo Grupo 3" —
  a fase 0 entra no coletor como evento ``tipo="fase"`` com a duração medida
  pelo extrator, e aparece em ``duracao_por_fase_s`` do resumo.

Tudo offline: modo mock (JSONs locais) e extrator fake, como nos demais testes.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pipeline as pipeline_mod  # noqa: E402
from extraction import ExtractedDocument, PdfExtractor  # noqa: E402
from metrics.coletor import ExecutionCollector  # noqa: E402
from metrics.resumo import gerar_resumo  # noqa: E402
from pipeline import (  # noqa: E402
    EXTRACTION_PHASE,
    build_peer_review_pipeline,
    extract_pdf_input,
)


class _FakePdfExtractor(PdfExtractor):
    """Extrator determinístico de teste (nenhuma dependência externa)."""

    name = "fake"

    @property
    def version(self) -> str:
        return "0.0-teste"

    def extract(self, pdf_path: str | Path) -> ExtractedDocument:
        return ExtractedDocument(
            document_id="doc_fake",
            filename=Path(pdf_path).name,
            text="texto extraído de teste",
            num_pages=2,
            extractor=self.name,
            extractor_version=self.version,
            extraction_duration_s=0.05,
            warnings=[],
        )


def _rodar_pipeline_mock(coletor: ExecutionCollector) -> None:
    pipe = build_peer_review_pipeline()
    artigo = (SRC / "examples" / "example_article.txt").read_text(encoding="utf-8")
    pipe.run(
        initial_input=artigo,
        config={"mode": "mock", "_metrics_collector": coletor},
        verbose=False,
    )


# ---------------------------------------------------------------------------
# Coerência visível no resumo
# ---------------------------------------------------------------------------

def test_checar_coerencia_aparece_no_resumo_mesmo_rodando_dentro_da_auditoria():
    coletor = ExecutionCollector(run_id="run-coerencia")
    _rodar_pipeline_mock(coletor)

    eventos = [
        e for e in coletor.eventos if e.tipo == "tool" and e.nome == "checar_coerencia"
    ]
    assert len(eventos) == 1, "a checagem de coerência deveria virar evento próprio"
    assert "quantidade_inconsistencias" in eventos[0].detalhes
    assert "tipos_inconsistencias" in eventos[0].detalhes

    resumo = gerar_resumo(
        coletor.eventos, run_id=coletor.run_id, duracao_total_s=coletor.duracao_execucao_s
    )
    assert resumo.quantidade_tools_chamadas.get("checar_coerencia") == 1
    # A auditoria continua aparecendo como tool separada — uma não substitui a outra.
    assert resumo.quantidade_tools_chamadas.get("auditar_decisao_final") == 1


def test_coerencia_nao_executada_vira_alerta_no_resumo(monkeypatch):
    """Quando checar_coerencia não roda, o resumo denuncia a lacuna (aviso)."""

    def _auditoria_sem_coerencia(veredito: dict) -> dict:
        return {
            "status": "ok",
            "decisao": veredito.get("decisao"),
            "decisao_rotulo": "Aceitar com ressalvas",
            "media_notas": 3.0,
            "divergencia_notas": 0,
            "criticas_por_tipo": {"fraqueza": 0, "critica": 0},
            "requer_revisao_humana": False,
            "inconsistencias": [],
            "coerencia_executada": False,
            "aviso_coerencia": "checar_coerencia ausente — auditoria sem checagem semântica",
            "resumo_auditoria": "auditoria de teste sem coerência",
            "veredito": veredito,
        }

    monkeypatch.setattr(pipeline_mod, "_tool_auditoria", _auditoria_sem_coerencia)
    coletor = ExecutionCollector(run_id="run-sem-coerencia")
    _rodar_pipeline_mock(coletor)

    eventos = [e for e in coletor.eventos if e.nome == "checar_coerencia"]
    assert len(eventos) == 1
    assert eventos[0].status == "aviso"

    resumo = gerar_resumo(
        coletor.eventos, run_id=coletor.run_id, duracao_total_s=coletor.duracao_execucao_s
    )
    assert any("checar_coerencia" in alerta for alerta in resumo.alertas)


# ---------------------------------------------------------------------------
# Fase 0 (extração do PDF) no resumo
# ---------------------------------------------------------------------------

def test_tempo_de_extracao_do_pdf_entra_no_resumo():
    coletor = ExecutionCollector(run_id="run-fase0")
    doc = extract_pdf_input(
        "artigo.pdf",
        extractor=_FakePdfExtractor(),
        tracer=None,
        run_id="run-fase0",
        coletor=coletor,
    )

    fases = [
        e for e in coletor.eventos if e.tipo == "fase" and e.fase == EXTRACTION_PHASE
    ]
    assert len(fases) == 1, "a extração deveria entrar no coletor como evento de fase"
    assert fases[0].duracao_s == doc.extraction_duration_s == 0.05
    assert fases[0].detalhes["extrator"] == "fake 0.0-teste"

    resumo = gerar_resumo(
        coletor.eventos, run_id=coletor.run_id, duracao_total_s=coletor.duracao_execucao_s
    )
    assert resumo.duracao_por_fase_s[EXTRACTION_PHASE] == 0.05
