"""Testes do extrator de PDF (interface substituível + LiteParse) — Grupo 3.

Os testes da interface e dos avisos de qualidade rodam offline, sem liteparse.
Os testes do LiteParse real usam PDFs mínimos gerados em memória (sem arquivos
restritos no repositório) e são pulados se o pacote não estiver instalado.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from extraction import (  # noqa: E402
    ExtractedDocument,
    LiteParseExtractor,
    PdfExtractor,
    get_default_extractor,
)
from extraction.extractor import MIN_AVG_CHARS_PER_PAGE, _quality_warnings  # noqa: E402


# ---------------------------------------------------------------------------
# Geração de PDFs mínimos em memória (sem dependências externas)
# ---------------------------------------------------------------------------

def _pdf_minimo(texto_paginas: list[str]) -> bytes:
    """Monta um PDF 1.4 mínimo e VÁLIDO (xref correta) com uma página por texto.

    Página com texto vazio vira uma página sem conteúdo textual — simula um PDF
    escaneado (imagem) do ponto de vista da extração.
    """
    objetos: list[bytes] = []
    n_paginas = len(texto_paginas)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n_paginas))

    objetos.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objetos.append(
        f"<< /Type /Pages /Kids [{kids}] /Count {n_paginas} >>".encode("latin-1")
    )
    fonte_num = 3 + 2 * n_paginas
    for i, texto in enumerate(texto_paginas):
        conteudo_num = 3 + 2 * i + 1
        objetos.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {conteudo_num} 0 R "
                f"/Resources << /Font << /F1 {fonte_num} 0 R >> >> >>"
            ).encode("latin-1")
        )
        stream = (
            f"BT /F1 12 Tf 72 720 Td ({texto}) Tj ET".encode("latin-1")
            if texto
            else b""
        )
        objetos.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
            + stream + b"\nendstream"
        )
    objetos.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    corpo = b"%PDF-1.4\n"
    offsets = [0]  # objeto 0 (free)
    for num, obj in enumerate(objetos, start=1):
        offsets.append(len(corpo))
        corpo += f"{num} 0 obj\n".encode() + obj + b"\nendobj\n"

    inicio_xref = len(corpo)
    corpo += f"xref\n0 {len(objetos) + 1}\n".encode()
    corpo += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        corpo += f"{off:010d} 00000 n \n".encode()
    corpo += (
        f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\n"
        f"startxref\n{inicio_xref}\n%%EOF\n"
    ).encode()
    return corpo


TEXTO_LONGO = "Artigo cientifico de teste com texto digital suficiente. " * 10


# ---------------------------------------------------------------------------
# Interface substituível (roda offline, sem liteparse)
# ---------------------------------------------------------------------------

class _FakeExtractor(PdfExtractor):
    """Extrator de mentira: prova que a interface é substituível sem LiteParse."""

    name = "fake"

    @property
    def version(self) -> str:
        return "0.0-teste"

    def extract(self, pdf_path: str | Path) -> ExtractedDocument:
        return ExtractedDocument(
            document_id="doc_fake",
            filename=Path(pdf_path).name,
            text="texto simulado",
            num_pages=1,
            extractor=self.name,
            extractor_version=self.version,
            extraction_duration_s=0.01,
            warnings=[],
        )


def test_qualquer_extrator_que_cumpra_a_interface_serve():
    doc = _FakeExtractor().extract("qualquer/artigo.pdf")
    assert isinstance(doc, ExtractedDocument)
    assert doc.extractor == "fake"
    assert doc.filename == "artigo.pdf"


def test_extrator_padrao_e_o_liteparse():
    extrator = get_default_extractor()
    assert isinstance(extrator, LiteParseExtractor)
    assert extrator.name == "liteparse"


def test_avisos_de_qualidade_texto_vazio():
    avisos = _quality_warnings("", num_pages=2)
    assert len(avisos) == 1
    assert "escaneado" in avisos[0]


def test_avisos_de_qualidade_baixa_densidade():
    texto_curto = "a" * (MIN_AVG_CHARS_PER_PAGE // 2)
    avisos = _quality_warnings(texto_curto, num_pages=1)
    assert len(avisos) == 1
    assert "densidade" in avisos[0]


def test_sem_avisos_para_texto_denso():
    texto = "a" * (MIN_AVG_CHARS_PER_PAGE * 2)
    assert _quality_warnings(texto, num_pages=1) == []


# ---------------------------------------------------------------------------
# LiteParse real (pulado se o pacote não estiver instalado)
# ---------------------------------------------------------------------------

liteparse = pytest.importorskip(
    "liteparse", reason="liteparse não instalado (modo mock/CI mínima)"
)


@pytest.fixture()
def pdf_digital(tmp_path: Path) -> Path:
    caminho = tmp_path / "artigo_digital.pdf"
    caminho.write_bytes(_pdf_minimo([TEXTO_LONGO, TEXTO_LONGO]))
    return caminho


@pytest.fixture()
def pdf_escaneado(tmp_path: Path) -> Path:
    """PDF 'complexo': páginas sem camada de texto, como um escaneamento."""
    caminho = tmp_path / "artigo_escaneado.pdf"
    caminho.write_bytes(_pdf_minimo(["", ""]))
    return caminho


def test_liteparse_extrai_pdf_com_texto_digital(pdf_digital: Path):
    doc = LiteParseExtractor().extract(pdf_digital)
    assert "Artigo cientifico de teste" in doc.text
    assert doc.num_pages == 2
    assert doc.extractor == "liteparse"
    assert doc.extractor_version not in ("", "desconhecida")
    assert doc.extraction_duration_s > 0
    assert doc.filename == "artigo_digital.pdf"
    assert doc.document_id.startswith("doc_")


def test_liteparse_gera_aviso_para_pdf_sem_texto(pdf_escaneado: Path):
    """PDF escaneado/complexo NÃO explode aqui: gera aviso claro para o Grupo 1."""
    doc = LiteParseExtractor().extract(pdf_escaneado)
    assert not doc.has_text
    assert doc.warnings, "PDF sem texto deve produzir aviso para a camada de validação"
    assert any("escaneado" in a for a in doc.warnings)


def test_liteparse_mesmo_arquivo_mesmo_document_id(pdf_digital: Path):
    a = LiteParseExtractor().extract(pdf_digital)
    b = LiteParseExtractor().extract(pdf_digital)
    assert a.document_id == b.document_id


def test_liteparse_arquivo_inexistente_erro_claro(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        LiteParseExtractor().extract(tmp_path / "nao_existe.pdf")
