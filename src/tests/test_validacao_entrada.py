"""Testes da validação e resiliência da ENTRADA por PDF (Grupo 1).

Tudo offline: os checks de arquivo usam fixtures em ``tmp_path`` e os de
documento usam o contrato ``ExtractedDocument`` diretamente (sem extrator real).
Os testes de evento redirecionam ``validacao_events.jsonl`` para ``tmp_path``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import eventos_validacao as ev  # noqa: E402
from extraction.document import ExtractedDocument  # noqa: E402
from validacao_entrada import (  # noqa: E402
    DecisaoEntrada,
    EntradaInvalidaError,
    classificar_arquivo_pdf,
    classificar_documento_extraido,
    validar_arquivo_pdf,
    validar_documento_extraido,
    validar_entrada_com_retry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _doc(text: str, *, warnings=None, num_pages: int = 4) -> ExtractedDocument:
    return ExtractedDocument(
        document_id="doc_teste",
        filename="artigo.pdf",
        text=text,
        num_pages=num_pages,
        extractor="fake",
        extractor_version="0.0-teste",
        extraction_duration_s=0.01,
        warnings=list(warnings or []),
    )


_TEXTO_OK = (
    "Este é um artigo com texto digital suficiente para passar em todas as "
    "verificações determinísticas da camada de validação de entrada. Ele tem "
    "densidade de caracteres adequada por página e nenhum caractere ilegível. " * 8
)


def _pdf_valido(tmp_path: Path) -> Path:
    caminho = tmp_path / "artigo.pdf"
    caminho.write_bytes(b"%PDF-1.7\n% conteudo minimo de teste\n")
    return caminho


@pytest.fixture(autouse=True)
def _eventos_em_tmp(tmp_path, monkeypatch):
    """Isola a escrita de eventos: cada teste grava em seu próprio arquivo."""
    monkeypatch.setattr(ev, "EVENTOS_DIR", tmp_path)
    monkeypatch.setattr(ev, "EVENTOS_PATH", tmp_path / "validacao_events.jsonl")


# ---------------------------------------------------------------------------
# Nível 1 — validação de ARQUIVO (pré-extração)
# ---------------------------------------------------------------------------

def test_arquivo_pdf_valido_passa(tmp_path):
    r = classificar_arquivo_pdf(_pdf_valido(tmp_path))
    assert r.decisao is DecisaoEntrada.OK


def test_arquivo_inexistente_bloqueia(tmp_path):
    r = classificar_arquivo_pdf(tmp_path / "nao_existe.pdf")
    assert r.decisao is DecisaoEntrada.BLOQUEAR
    assert not r.permite_retry
    assert "inexistente" in r.mensagem


def test_arquivo_formato_incorreto_bloqueia(tmp_path):
    caminho = tmp_path / "artigo.txt"
    caminho.write_text("texto qualquer", encoding="utf-8")
    r = classificar_arquivo_pdf(caminho)
    assert r.decisao is DecisaoEntrada.BLOQUEAR
    assert "formato incorreto" in r.mensagem


def test_arquivo_vazio_bloqueia(tmp_path):
    caminho = tmp_path / "vazio.pdf"
    caminho.write_bytes(b"")
    r = classificar_arquivo_pdf(caminho)
    assert r.decisao is DecisaoEntrada.BLOQUEAR
    assert "vazio" in r.mensagem


def test_arquivo_corrompido_sem_cabecalho_bloqueia(tmp_path):
    caminho = tmp_path / "corrompido.pdf"
    caminho.write_bytes(b"isto nao e um PDF")
    r = classificar_arquivo_pdf(caminho)
    assert r.decisao is DecisaoEntrada.BLOQUEAR
    assert "%PDF" in r.mensagem


def test_arquivo_protegido_bloqueia(tmp_path):
    caminho = tmp_path / "protegido.pdf"
    caminho.write_bytes(b"%PDF-1.7\n/Encrypt 5 0 R\n")
    r = classificar_arquivo_pdf(caminho)
    assert r.decisao is DecisaoEntrada.BLOQUEAR
    assert "protegido" in r.mensagem or "Encrypt" in r.mensagem


def test_diretorio_bloqueia(tmp_path):
    r = classificar_arquivo_pdf(tmp_path)
    assert r.decisao is DecisaoEntrada.BLOQUEAR


# ---------------------------------------------------------------------------
# Nível 2 — validação do DOCUMENTO EXTRAÍDO (pós-extração)
# ---------------------------------------------------------------------------

def test_documento_valido_ok():
    r = classificar_documento_extraido(_doc(_TEXTO_OK))
    assert r.decisao is DecisaoEntrada.OK


def test_texto_vazio_e_recuperavel():
    r = classificar_documento_extraido(_doc(""))
    assert r.decisao is DecisaoEntrada.RETRY
    assert r.permite_retry
    assert "não produziu texto" in r.mensagem


def test_texto_muito_curto_gera_alerta():
    r = classificar_documento_extraido(_doc("Texto curto demais.", num_pages=1))
    assert r.decisao is DecisaoEntrada.ALERTA
    assert r.requer_revisao_humana


def test_caracteres_ilegiveis_geram_alerta():
    # Metade do texto são caracteres de controle (ilegíveis) → acima do limiar.
    texto = ("A" * 150) + ("\x00" * 150)
    r = classificar_documento_extraido(_doc(texto, num_pages=1))
    assert r.decisao is DecisaoEntrada.ALERTA
    assert "ilegíveis" in r.mensagem


def test_avisos_do_extrator_geram_alerta():
    r = classificar_documento_extraido(
        _doc(_TEXTO_OK, warnings=["página 2 possivelmente escaneada"])
    )
    assert r.decisao is DecisaoEntrada.ALERTA
    assert any("aviso do extrator" in m for m in r.motivos)


# ---------------------------------------------------------------------------
# Emissão de eventos + run_id compartilhado
# ---------------------------------------------------------------------------

def test_validar_documento_valido_emite_passou_de_primeira():
    validar_documento_extraido(_doc(_TEXTO_OK), run_id="run-x", fase="fase_0_extracao_pdf")
    eventos = ev.ler_eventos(run_id="run-x")
    assert len(eventos) == 1
    assert eventos[0]["categoria"] == "passou_de_primeira"
    assert eventos[0]["fase"] == "fase_0_extracao_pdf"


def test_validar_documento_vazio_bloqueia_e_registra():
    with pytest.raises(EntradaInvalidaError):
        validar_documento_extraido(_doc(""), run_id="run-b", fase="fase_0_extracao_pdf")
    eventos = ev.ler_eventos(run_id="run-b")
    assert eventos[-1]["categoria"] == "bloqueado"
    assert eventos[-1]["requer_revisao_humana"] is True


def test_validar_arquivo_corrompido_bloqueia_e_registra(tmp_path):
    caminho = tmp_path / "corrompido.pdf"
    caminho.write_bytes(b"nao e pdf")
    with pytest.raises(EntradaInvalidaError):
        validar_arquivo_pdf(caminho, run_id="run-arq")
    eventos = ev.ler_eventos(run_id="run-arq")
    assert eventos[-1]["categoria"] == "bloqueado"


def test_validar_arquivo_sem_exigir_nao_levanta(tmp_path):
    caminho = tmp_path / "corrompido.pdf"
    caminho.write_bytes(b"nao e pdf")
    r = validar_arquivo_pdf(caminho, run_id="run-soft", exigir=False)
    assert r.bloqueado  # classifica como bloqueio, mas não levanta


# ---------------------------------------------------------------------------
# Orquestrador com retry — os quatro cenários da atividade
# ---------------------------------------------------------------------------

def test_retry_documento_valido_de_primeira(tmp_path):
    doc = validar_entrada_com_retry(
        _pdf_valido(tmp_path), extrair=lambda _p: _doc(_TEXTO_OK), run_id="run-ok",
    )
    assert doc.text == _TEXTO_OK
    categorias = [e["categoria"] for e in ev.ler_eventos(run_id="run-ok")]
    assert categorias == ["passou_de_primeira", "passou_de_primeira"]  # arquivo + documento


def test_retry_alerta_segue(tmp_path):
    doc = validar_entrada_com_retry(
        _pdf_valido(tmp_path),
        extrair=lambda _p: _doc(_TEXTO_OK, warnings=["aviso qualquer"]),
        run_id="run-al",
    )
    assert doc is not None
    categorias = [e["categoria"] for e in ev.ler_eventos(run_id="run-al")]
    assert categorias[-1] == "alerta"


def test_retry_recuperavel_reextrai_e_recupera(tmp_path):
    chamadas = {"n": 0}

    def extrair(_p):
        return _doc("")  # 1ª tentativa: vazio

    def reextrair(_p):
        chamadas["n"] += 1
        return _doc(_TEXTO_OK)  # 2ª tentativa: recuperado

    doc = validar_entrada_com_retry(
        _pdf_valido(tmp_path), extrair=extrair, reextrair=reextrair, run_id="run-rec",
    )
    assert doc.text == _TEXTO_OK
    assert chamadas["n"] == 1
    categorias = [e["categoria"] for e in ev.ler_eventos(run_id="run-rec") if e["schema"] == "validar_documento_extraido"]
    assert categorias == ["falhou_recuperavel", "corrigido", "passou_apos_correcao"]


def test_retry_bloqueia_sem_estrategia(tmp_path):
    with pytest.raises(EntradaInvalidaError):
        validar_entrada_com_retry(
            _pdf_valido(tmp_path), extrair=lambda _p: _doc(""), reextrair=None, run_id="run-bl",
        )
    categorias = [e["categoria"] for e in ev.ler_eventos(run_id="run-bl") if e["schema"] == "validar_documento_extraido"]
    assert categorias == ["bloqueado"]


def test_retry_nao_registra_correcao_quando_texto_nao_muda(tmp_path):
    """Reextração que devolve o MESMO texto não deve emitir 'corrigido'."""
    with pytest.raises(EntradaInvalidaError):
        validar_entrada_com_retry(
            _pdf_valido(tmp_path),
            extrair=lambda _p: _doc(""),
            reextrair=lambda _p: _doc(""),  # não muda nada
            run_id="run-nc",
        )
    categorias = [e["categoria"] for e in ev.ler_eventos(run_id="run-nc")]
    assert "corrigido" not in categorias
    assert categorias[-1] == "bloqueado"


def test_retry_arquivo_ruim_nao_chega_a_extrair(tmp_path):
    """Bloqueio na etapa de arquivo curto-circuita: extrair nem é chamado."""
    corrompido = tmp_path / "corrompido.pdf"
    corrompido.write_bytes(b"nao e pdf")

    def extrair(_p):  # não deve ser chamado
        raise AssertionError("extração não deveria ocorrer com arquivo bloqueado")

    with pytest.raises(EntradaInvalidaError):
        validar_entrada_com_retry(corrompido, extrair=extrair, run_id="run-arqruim")
    categorias = [e["categoria"] for e in ev.ler_eventos(run_id="run-arqruim")]
    assert categorias == ["bloqueado"]
