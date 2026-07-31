"""Testes do CONTRATO de documento extraído (ExtractedDocument) — Grupo 3.

Rodam offline, sem liteparse instalado e sem tocar a rede: exercitam apenas o
contrato (round-trip, metadados sem texto, exemplo versionado).
"""

import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from extraction import ExtractedDocument, make_document_id  # noqa: E402

EXAMPLES = SRC / "examples"

CAMPOS_OBRIGATORIOS = {
    "document_id",
    "filename",
    "text",
    "num_pages",
    "extractor",
    "extractor_version",
    "extraction_duration_s",
    "warnings",
}


def _doc(**overrides) -> ExtractedDocument:
    base = dict(
        document_id="doc_abc123",
        filename="artigo.pdf",
        text="Texto extraído do artigo.",
        num_pages=3,
        extractor="liteparse",
        extractor_version="2.6.0",
        extraction_duration_s=0.5,
        warnings=[],
    )
    base.update(overrides)
    return ExtractedDocument(**base)


def test_contrato_tem_todos_os_campos_minimos():
    data = _doc().to_dict()
    assert CAMPOS_OBRIGATORIOS <= set(data)


def test_round_trip_to_dict_from_dict():
    doc = _doc(warnings=["página 2 possivelmente escaneada"])
    clone = ExtractedDocument.from_dict(doc.to_dict())
    assert clone == doc


def test_from_dict_ignora_campos_extras():
    data = _doc().to_dict()
    data["campo_futuro"] = "ignorado"
    assert ExtractedDocument.from_dict(data) == _doc()


def test_to_metadata_nao_vaza_o_texto_completo():
    """Logs/trace/relatório recebem metadados SEM o conteúdo do artigo."""
    meta = _doc().to_metadata()
    assert "text" not in meta
    assert meta["text_chars"] == len("Texto extraído do artigo.")
    assert meta["document_id"] == "doc_abc123"


def test_duracao_do_contrato_esta_em_segundos():
    assert _doc(extraction_duration_s=1.25).to_dict()["extraction_duration_s"] == 1.25


def test_has_text_detecta_texto_vazio_ou_em_branco():
    assert _doc().has_text
    assert not _doc(text="").has_text
    assert not _doc(text="   \n\t ").has_text


def test_document_id_e_estavel_por_conteudo():
    a = make_document_id(b"mesmo conteudo")
    b = make_document_id(b"mesmo conteudo")
    c = make_document_id(b"outro conteudo")
    assert a == b
    assert a != c
    assert a.startswith("doc_")


def test_exemplo_versionado_respeita_o_contrato():
    """O exemplo publicado para os Grupos 1 e 2 carrega e valida pelo contrato."""
    data = json.loads(
        (EXAMPLES / "example_extracted_document.json").read_text(encoding="utf-8")
    )
    assert CAMPOS_OBRIGATORIOS <= set(data)
    doc = ExtractedDocument.from_dict(data)
    assert doc.has_text
    assert doc.num_pages > 0
    assert doc.extractor == "liteparse"
    assert isinstance(doc.warnings, list)
    assert isinstance(doc.extraction_duration_s, float)
