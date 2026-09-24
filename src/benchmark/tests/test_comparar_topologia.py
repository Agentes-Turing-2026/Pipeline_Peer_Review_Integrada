"""Testes do comparativo pareado multiagente vs. agente único (Grupo 3 — topologia).

Mesma estrutura de ``test_ablacao_cross_review.py``: (1) testes 100% offline
de ``comparar_par``/``gerar_conclusao``/``salvar`` com registros sintéticos
(sem rodar o pipeline); (2) um teste de integração em modo mock (offline, sem
chave, mas exercita ``rodar_par`` de ponta a ponta — inclusive as duas
chamadas reais a ``processar_documento``).
"""

import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from benchmark.comparar_topologia import (
    comparar_par,
    gerar_conclusao,
    rodar_par,
    salvar,
    separar_por_modo,
)
from benchmark.corpus import DocumentoCorpus


def _par(multi: dict, unico: dict, *, mode: str = "api", **overrides) -> dict:
    """Monta um par no MESMO formato que ``rodar_par`` devolve."""
    par = {
        "mode": mode,
        "provider": multi.get("provider"),
        "model": multi.get("model"),
        "multiagente": multi,
        "agente_unico": unico,
        "delta": comparar_par(multi, unico),
    }
    par.update(overrides)
    return par


def _registro(**overrides) -> dict:
    base = {
        "doc_id": "doc_x",
        "mode": "api",
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "resultado": "sucesso",
        "decisao_final": 3,
        "notas_por_revisor": {"statistician": 3, "domain_expert": 3, "copyeditor": 3},
        "quantidade_criticas": 4,
        "quantidade_criticas_bloqueantes": 1,
        "requer_revisao_humana": False,
        "duracao_total_s": 60.0,
        "tokens_totais": 150_000,
        "custo_estimado": 0.20,
        "chamadas_llm": 7,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# comparar_par: deltas numéricos e de qualidade
# ---------------------------------------------------------------------------

def test_comparar_par_calcula_variacao_percentual_negativa_quando_agente_unico_reduz_consumo():
    multi = _registro(duracao_total_s=60.0, tokens_totais=150_000, custo_estimado=0.20, chamadas_llm=7)
    unico = _registro(duracao_total_s=10.0, tokens_totais=20_000, custo_estimado=0.03, chamadas_llm=1)

    delta = comparar_par(multi, unico)

    assert delta["comparavel"] is True
    assert delta["duracao_total_s_variacao_pct"] == pytest.approx(-83.333, rel=1e-3)
    assert delta["tokens_totais_variacao_pct"] == pytest.approx((20_000 - 150_000) / 150_000 * 100)
    assert delta["custo_estimado_variacao_pct"] == pytest.approx((0.03 - 0.20) / 0.20 * 100)
    assert delta["chamadas_llm_variacao_pct"] == pytest.approx((1 - 7) / 7 * 100)


def test_comparar_par_sem_dado_devolve_none_em_vez_de_zero():
    multi = _registro(tokens_totais=None, custo_estimado=None)
    unico = _registro(tokens_totais=None, custo_estimado=None)
    delta = comparar_par(multi, unico)
    assert delta["tokens_totais_variacao_pct"] is None
    assert delta["custo_estimado_variacao_pct"] is None


def test_comparar_par_nao_compara_notas_por_revisor():
    """notas_por_revisor tem formatos diferentes entre as topologias — não entra no delta."""
    multi = _registro(notas_por_revisor={"statistician": 2, "domain_expert": 3, "copyeditor": 2})
    unico = _registro(notas_por_revisor={"agente_unico": 3})
    delta = comparar_par(multi, unico)
    assert "notas_por_revisor_mudaram" not in delta


def test_comparar_par_marca_decisao_que_mudou():
    multi = _registro(decisao_final=2)
    unico = _registro(decisao_final=3)
    delta = comparar_par(multi, unico)
    assert delta["decisao_mudou"] is True


def test_comparar_par_decisao_estavel_nao_e_marcada_como_mudanca():
    multi = _registro(decisao_final=3)
    unico = _registro(decisao_final=3)
    delta = comparar_par(multi, unico)
    assert delta["decisao_mudou"] is False


def test_comparar_par_nao_comparavel_quando_um_lado_falha():
    multi = _registro(resultado="sucesso")
    unico = _registro(resultado="falha_execucao", decisao_final=None)
    delta = comparar_par(multi, unico)
    assert delta["comparavel"] is False
    assert "decisao_mudou" not in delta


def test_comparar_par_nao_divide_por_zero_quando_multiagente_e_zero():
    multi = _registro(chamadas_llm=0)
    unico = _registro(chamadas_llm=1)
    delta = comparar_par(multi, unico)
    assert delta["chamadas_llm_variacao_pct"] is None


# ---------------------------------------------------------------------------
# gerar_conclusao
# ---------------------------------------------------------------------------

def test_gerar_conclusao_sem_pares_comparaveis_nao_quebra():
    bloqueado = _registro(resultado="entrada_bloqueada")
    pares = {"doc_bloqueado": _par(bloqueado, bloqueado)}
    texto = gerar_conclusao(pares)
    assert "Nenhuma execução REAL" in texto


def test_gerar_conclusao_relata_documentos_nao_comparaveis_separadamente():
    multi_ok = _registro()
    unico_ok = _registro(duracao_total_s=10.0, tokens_totais=20_000, custo_estimado=0.03, chamadas_llm=1)
    pares = {
        "doc_ok": _par(multi_ok, unico_ok),
        "doc_falhou": _par(_registro(resultado="sucesso"), _registro(resultado="falha_execucao")),
    }
    texto = gerar_conclusao(pares)
    assert "1 documento(s) comparável" in texto
    assert "doc_falhou" in texto


def test_gerar_conclusao_relata_quando_decisao_muda():
    multi = _registro(decisao_final=2)
    unico = _registro(decisao_final=3, duracao_total_s=10.0, tokens_totais=20_000, custo_estimado=0.03, chamadas_llm=1)
    pares = {"doc_x": _par(multi, unico)}
    texto = gerar_conclusao(pares)
    assert "MUDOU em 1/1" in texto
    assert "doc_x" in texto


# ---------------------------------------------------------------------------
# Separação mock x real: o smoke test não pode contaminar o agregado
# ---------------------------------------------------------------------------

def test_separar_por_modo_classifica_api_como_real_e_o_resto_como_mock():
    reais, mock = separar_por_modo({
        "doc_api": _par(_registro(), _registro(), mode="api"),
        "doc_mock": _par(_registro(), _registro(), mode="mock"),
        "doc_sem_modo": {"delta": {}},
    })
    assert set(reais) == {"doc_api"}
    assert set(mock) == {"doc_mock", "doc_sem_modo"}


def test_gerar_conclusao_nao_conta_o_mock_entre_os_documentos_comparaveis():
    multi = _registro()
    unico = _registro(duracao_total_s=10.0, tokens_totais=20_000, custo_estimado=0.03, chamadas_llm=1)
    pares = {
        "doc_real": _par(multi, unico, mode="api"),
        "exemplo_mock": _par(multi, unico, mode="mock"),
    }
    texto = gerar_conclusao(pares)

    assert "1 documento(s) comparável(is)" in texto
    assert "de 1 rodado(s)" in texto
    assert "2 execuções reais" in texto
    assert "SMOKE TEST" in texto
    assert "exemplo_mock" in texto
    assert "MUDOU em 0/1" in texto


def test_gerar_conclusao_sempre_declara_que_nao_houve_avaliacao_humana():
    multi = _registro()
    unico = _registro(duracao_total_s=10.0, tokens_totais=20_000, custo_estimado=0.03, chamadas_llm=1)
    texto = gerar_conclusao({"doc_x": _par(multi, unico)})
    assert "NÃO houve avaliação humana" in texto
    assert "indicadores automáticos" in texto
    assert "não é comparável entre as topologias" in texto


# ---------------------------------------------------------------------------
# salvar: persistência json/md
# ---------------------------------------------------------------------------

def test_salvar_grava_json_e_md_navegaveis(tmp_path):
    multi = _registro()
    unico = _registro(duracao_total_s=10.0, tokens_totais=20_000, custo_estimado=0.03, chamadas_llm=1)
    pares = {"doc_x": _par(multi, unico, doc_id="doc_x")}
    conclusao = gerar_conclusao(pares)

    json_path, md_path = salvar(pares, conclusao, destino_dir=tmp_path)

    assert json_path.exists()
    assert md_path.exists()
    dados = json.loads(json_path.read_text(encoding="utf-8"))
    assert dados["conclusao"] == conclusao
    assert "doc_x" in dados["pares"]

    conteudo_md = md_path.read_text(encoding="utf-8")
    assert "doc_x" in conteudo_md
    assert "Conclusão" in conteudo_md
    assert "Execuções reais (modo api)" in conteudo_md


def test_salvar_separa_a_tabela_real_da_tabela_de_smoke_test(tmp_path):
    multi = _registro()
    unico = _registro(duracao_total_s=10.0, tokens_totais=20_000, custo_estimado=0.03, chamadas_llm=1)
    pares = {
        "doc_real": _par(multi, unico, mode="api"),
        "exemplo_mock": _par(multi, unico, mode="mock"),
    }
    _, md_path = salvar(pares, gerar_conclusao(pares), destino_dir=tmp_path)
    conteudo = md_path.read_text(encoding="utf-8")

    secao_real = conteudo.index("## Execuções reais (modo api)")
    secao_mock = conteudo.index("## Smoke test")
    assert secao_real < secao_mock
    assert "doc_real" in conteudo[secao_real:secao_mock]
    assert "doc_real" not in conteudo[secao_mock:]
    assert "exemplo_mock" in conteudo[secao_mock:]
    assert "exemplo_mock" not in conteudo[secao_real:secao_mock]


# ---------------------------------------------------------------------------
# rodar_par: integração de ponta a ponta em modo mock (sem chave, sem custo)
# ---------------------------------------------------------------------------

def test_rodar_par_em_modo_mock_produz_as_duas_execucoes(tmp_path):
    doc = DocumentoCorpus(
        id="doc_topologia_mock",
        titulo="Documento de teste (topologia, mock)",
        area="smoke_test",
        caracteristicas=["smoke_test", "sem_pdf_real"],
    )
    par = rodar_par(doc, mode="mock", cache_dir=tmp_path)

    assert par["doc_id"] == "doc_topologia_mock"
    assert par["mode"] == "mock"
    assert par["multiagente"]["resultado"] == "sucesso"
    assert par["agente_unico"]["resultado"] == "sucesso"
    assert par["multiagente"]["single_agent_enabled"] is False
    assert par["agente_unico"]["single_agent_enabled"] is True
    assert par["delta"]["comparavel"] is True
