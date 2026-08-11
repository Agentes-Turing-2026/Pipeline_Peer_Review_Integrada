"""Testes de ``executar.processar_documento``/``_config_llm`` (Grupo 2 — benchmark).

Só o modo mock é exercitado aqui (offline, sem chave, sem rede): o objetivo é
garantir que o registro por execução passa a identificar provedor/modelo/
structured_output e a variante de leitura cruzada usada — o que faltava antes
(ver docs/benchmark_reference.md §6/§7). O modo api não é testado aqui (exigiria
chave e chamada real); ``model_provider`` já tem cobertura própria offline.
"""

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from benchmark.corpus import DocumentoCorpus  # noqa: E402
from benchmark.executar import _config_llm, processar_documento  # noqa: E402


def _doc_smoke_test() -> DocumentoCorpus:
    return DocumentoCorpus(
        id="doc_teste_executar",
        titulo="Documento de teste (sem PDF real)",
        area="smoke_test",
        caracteristicas=["smoke_test", "sem_pdf_real"],
    )


def test_config_llm_em_modo_mock_nao_anuncia_provedor_nenhum():
    # Modo mock não faz chamada LLM — anunciar um provedor/modelo aqui
    # sugeriria uma configuração que não foi de fato usada.
    assert _config_llm("mock") == {"provider": None, "model": None, "structured_output": None}


def test_config_llm_em_modo_api_resolve_o_provedor_configurado(monkeypatch):
    for var in ("LLM_PROVIDER", "LLM_MODEL", "GEMINI_MODEL"):
        monkeypatch.delenv(var, raising=False)
    config = _config_llm("api")
    assert config["provider"] == "gemini"
    assert config["model"] == "gemini-2.5-flash"
    assert config["structured_output"] is True


def test_processar_documento_mock_registra_cross_review_enabled_true_por_default(tmp_path):
    registro, resumo = processar_documento(
        _doc_smoke_test(), mode="mock", cache_dir=tmp_path,
    )
    assert registro["cross_review_enabled"] is True
    assert registro["provider"] is None  # modo mock
    assert registro["resultado"] == "sucesso"
    assert resumo is not None
    assert registro["notas_por_revisor"]
    assert isinstance(registro["quantidade_criticas"], int)
    assert isinstance(registro["quantidade_criticas_bloqueantes"], int)


def test_processar_documento_mock_com_cross_review_desativado(tmp_path):
    registro, resumo = processar_documento(
        _doc_smoke_test(), mode="mock", cache_dir=tmp_path, cross_review=False,
    )
    assert registro["cross_review_enabled"] is False
    assert registro["resultado"] == "sucesso"
    assert resumo is not None
