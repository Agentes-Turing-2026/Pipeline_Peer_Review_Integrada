"""Testes determinísticos de ``carregar_corpus``/``resolver_pdf_local`` (Grupo 2 — benchmark).

100% offline: nenhum teste aqui faz rede ou chama o pipeline.
"""

import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from benchmark.corpus import (
    DocumentoCorpus,
    carregar_corpus,
    resolver_pdf_local,
)


def _escrever_manifest(tmp_path: Path, documentos: list[dict]) -> Path:
    caminho = tmp_path / "manifest.json"
    caminho.write_text(json.dumps({"documentos": documentos}), encoding="utf-8")
    return caminho


def test_manifesto_valido_carrega_documentos_corretamente(tmp_path):
    caminho = _escrever_manifest(
        tmp_path,
        [
            {
                "id": "doc_1",
                "titulo": "Artigo 1",
                "area": "inteligencia_artificial",
                "fonte_url": "https://example.org/a.pdf",
                "caminho_local": None,
                "caracteristicas": ["curto"],
                "observacoes": "nota",
            }
        ],
    )
    documentos = carregar_corpus(caminho)
    assert documentos == [
        DocumentoCorpus(
            id="doc_1",
            titulo="Artigo 1",
            area="inteligencia_artificial",
            fonte_url="https://example.org/a.pdf",
            caminho_local=None,
            caracteristicas=["curto"],
            observacoes="nota",
        )
    ]


def test_campo_obrigatorio_ausente_levanta_value_error_apontando_doc_e_campo(tmp_path):
    caminho = _escrever_manifest(
        tmp_path,
        [{"id": "doc_1", "titulo": "Artigo 1", "caracteristicas": []}],
    )
    with pytest.raises(ValueError) as exc_info:
        carregar_corpus(caminho)
    mensagem = str(exc_info.value)
    assert "doc_1" in mensagem
    assert "area" in mensagem


def test_campo_obrigatorio_ausente_sem_id_usa_indice_como_referencia(tmp_path):
    caminho = _escrever_manifest(
        tmp_path,
        [{"titulo": "Sem id", "area": "x", "caracteristicas": []}],
    )
    with pytest.raises(ValueError) as exc_info:
        carregar_corpus(caminho)
    assert "índice 0" in str(exc_info.value)
    assert "id" in str(exc_info.value)


def test_ids_duplicados_levantam_value_error_listando_os_ids(tmp_path):
    caminho = _escrever_manifest(
        tmp_path,
        [
            {"id": "dup", "titulo": "A", "area": "x", "caracteristicas": []},
            {"id": "dup", "titulo": "B", "area": "y", "caracteristicas": []},
        ],
    )
    with pytest.raises(ValueError) as exc_info:
        carregar_corpus(caminho)
    assert "dup" in str(exc_info.value)


def test_documento_com_fonte_url_e_caminho_local_ambos_none_e_valido(tmp_path):
    caminho = _escrever_manifest(
        tmp_path,
        [
            {
                "id": "smoke",
                "titulo": "Smoke test",
                "area": "smoke_test",
                "fonte_url": None,
                "caminho_local": None,
                "caracteristicas": ["smoke_test"],
            }
        ],
    )
    documentos = carregar_corpus(caminho)
    assert len(documentos) == 1
    assert documentos[0].fonte_url is None
    assert documentos[0].caminho_local is None


def test_resolver_pdf_local_com_caminho_local_existente_retorna_esse_path(tmp_path):
    pdf = tmp_path / "artigo.pdf"
    pdf.write_bytes(b"%PDF-1.4 conteudo fake")
    doc = DocumentoCorpus(
        id="doc_1", titulo="A", area="x", caminho_local=str(pdf), caracteristicas=[],
    )
    resultado = resolver_pdf_local(doc, cache_dir=tmp_path / "cache")
    assert resultado == pdf


def test_resolver_pdf_local_sem_fonte_e_sem_caminho_retorna_none(tmp_path):
    doc = DocumentoCorpus(id="smoke", titulo="A", area="smoke_test", caracteristicas=[])
    resultado = resolver_pdf_local(doc, cache_dir=tmp_path / "cache")
    assert resultado is None
