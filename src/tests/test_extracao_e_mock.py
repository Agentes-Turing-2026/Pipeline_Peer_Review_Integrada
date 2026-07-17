"""Testes de integração da ENTRADA POR PDF e do modo MOCK preservado — Grupo 3.

Tudo roda offline (sem GOOGLE_API_KEY, sem liteparse, sem rede): o extrator é
substituído por um fake — exatamente o que a interface substituível permite — e
as fases dos agentes usam os JSONs locais do modo mock.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from extraction import ExtractedDocument, PdfExtractor  # noqa: E402
from observability import EventType, MemoryExporter, Tracer  # noqa: E402
from pipeline import (  # noqa: E402
    EXTRACTION_PHASE,
    build_peer_review_pipeline,
    extract_pdf_input,
)


class _FakePdfExtractor(PdfExtractor):
    """Extrator determinístico de teste (nenhuma dependência externa)."""

    name = "fake"

    def __init__(self, text: str = "texto extraído de teste", warnings: list[str] | None = None):
        self._text = text
        self._warnings = warnings or []

    @property
    def version(self) -> str:
        return "0.0-teste"

    def extract(self, pdf_path: str | Path) -> ExtractedDocument:
        return ExtractedDocument(
            document_id="doc_fake",
            filename=Path(pdf_path).name,
            text=self._text,
            num_pages=2,
            extractor=self.name,
            extractor_version=self.version,
            extraction_duration_s=0.05,
            warnings=list(self._warnings),
        )


# ---------------------------------------------------------------------------
# Modo mock preservado (execução completa offline)
# ---------------------------------------------------------------------------

def test_modo_mock_continua_funcionando_ponta_a_ponta():
    """As 4 fases rodam offline com os JSONs locais e produzem o relatório final."""
    pipeline = build_peer_review_pipeline()
    artigo = (SRC / "examples" / "example_article.txt").read_text(encoding="utf-8")

    result = pipeline.run(
        initial_input=artigo, config={"mode": "mock"}, verbose=False
    )

    report = result.final
    assert report.data["decisao"] in (1, 2, 3, 4)
    assert "# Relatório Final do Peer Review" in report.markdown
    assert report.data["run_id"] == result.run_id


def test_relatorio_mock_aponta_para_a_execucao():
    """O relatório referencia o run_id da execução que o gerou."""
    pipeline = build_peer_review_pipeline()
    artigo = (SRC / "examples" / "example_article.txt").read_text(encoding="utf-8")

    result = pipeline.run(initial_input=artigo, config={"mode": "mock"}, verbose=False)

    assert result.run_id in result.final.markdown


# ---------------------------------------------------------------------------
# Extração como fase 0 do trace (mesmo run_id, duração em segundos)
# ---------------------------------------------------------------------------

def test_extracao_entra_no_trace_como_fase_com_duracao_em_segundos():
    exp = MemoryExporter()
    tracer = Tracer(exporter=exp, run_id="run_teste_pdf")

    with tracer.run(name="peer_review"):
        doc = extract_pdf_input(
            "artigo.pdf", extractor=_FakePdfExtractor(), tracer=tracer
        )

    assert doc.text == "texto extraído de teste"

    # Todos os eventos pertencem à MESMA execução.
    assert {e.run_id for e in exp.events} == {"run_teste_pdf"}

    inicio = [e for e in exp.events if e.event_type == EventType.SPAN_START.value]
    fim = [e for e in exp.events if e.event_type == EventType.SPAN_END.value]
    assert [e.name for e in inicio] == [EXTRACTION_PHASE]
    assert inicio[0].kind == "phase"
    assert inicio[0].phase == EXTRACTION_PHASE
    # Duração do span em SEGUNDOS (campo novo duration_s).
    assert fim[0].duration_s is not None
    assert fim[0].duration_s < 60  # segundos, não milissegundos

    extraido = [e for e in exp.events if e.name == "documento_extraido"]
    assert len(extraido) == 1
    attrs = extraido[0].attributes
    assert attrs["extractor"] == "fake"
    assert attrs["num_pages"] == 2
    assert attrs["extraction_duration_s"] == 0.05
    # O texto completo NÃO vaza para o trace.
    assert "text" not in attrs
    assert attrs["text_chars"] == len(doc.text)


def test_extracao_com_avisos_emite_alerta_para_o_grupo1():
    exp = MemoryExporter()
    tracer = Tracer(exporter=exp, run_id="run_teste_aviso")
    extrator = _FakePdfExtractor(warnings=["baixa densidade de texto na página 2"])

    with tracer.run(name="peer_review"):
        doc = extract_pdf_input("artigo.pdf", extractor=extrator, tracer=tracer)

    assert doc.warnings
    avisos = [e for e in exp.events if e.name == "aviso_extracao"]
    assert len(avisos) == 1
    assert avisos[0].status == "alerta"
    assert "densidade" in avisos[0].attributes["aviso"]


def test_extracao_sem_texto_e_erro_claro():
    """Sem NENHUM texto não há o que revisar: erro explícito (não silencioso)."""
    extrator = _FakePdfExtractor(
        text="", warnings=["extração não produziu texto: o PDF pode ser escaneado"]
    )
    with pytest.raises(RuntimeError, match="não produziu texto"):
        extract_pdf_input("escaneado.pdf", extractor=extrator, tracer=None)
