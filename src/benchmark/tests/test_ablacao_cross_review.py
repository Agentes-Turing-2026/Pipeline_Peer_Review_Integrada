"""Testes do comparativo pareado com/sem leitura cruzada (Grupo 2 — benchmark).

Duas camadas: (1) testes 100% offline de ``comparar_par``/``gerar_conclusao``/
``salvar`` com registros sintéticos (sem rodar o pipeline); (2) um teste de
integração em modo mock (offline, sem chave, mas exercita ``rodar_par`` de
ponta a ponta — inclusive as duas chamadas reais a ``processar_documento``).
"""

import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from benchmark.ablacao_cross_review import (  # noqa: E402
    comparar_par,
    gerar_conclusao,
    rodar_par,
    salvar,
)
from benchmark.corpus import DocumentoCorpus  # noqa: E402


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

def test_comparar_par_calcula_variacao_percentual_negativa_quando_sem_reduz_consumo():
    com = _registro(duracao_total_s=60.0, tokens_totais=150_000, custo_estimado=0.20, chamadas_llm=7)
    sem = _registro(duracao_total_s=40.0, tokens_totais=90_000, custo_estimado=0.12, chamadas_llm=4)

    delta = comparar_par(com, sem)

    assert delta["comparavel"] is True
    assert delta["duracao_total_s_variacao_pct"] == pytest.approx(-33.333, rel=1e-3)
    assert delta["tokens_totais_variacao_pct"] == pytest.approx(-40.0)
    assert delta["custo_estimado_variacao_pct"] == pytest.approx(-40.0)
    assert delta["chamadas_llm_variacao_pct"] == pytest.approx((4 - 7) / 7 * 100)


def test_comparar_par_sem_dado_devolve_none_em_vez_de_zero():
    com = _registro(tokens_totais=None, custo_estimado=None)
    sem = _registro(tokens_totais=None, custo_estimado=None)
    delta = comparar_par(com, sem)
    assert delta["tokens_totais_variacao_pct"] is None
    assert delta["custo_estimado_variacao_pct"] is None


def test_comparar_par_marca_decisao_e_notas_que_mudaram():
    com = _registro(decisao_final=2, notas_por_revisor={"statistician": 2})
    sem = _registro(decisao_final=3, notas_por_revisor={"statistician": 3})
    delta = comparar_par(com, sem)
    assert delta["decisao_mudou"] is True
    assert delta["notas_por_revisor_mudaram"] is True


def test_comparar_par_decisao_estavel_nao_e_marcada_como_mudanca():
    com = _registro(decisao_final=3)
    sem = _registro(decisao_final=3)
    delta = comparar_par(com, sem)
    assert delta["decisao_mudou"] is False


def test_comparar_par_nao_comparavel_quando_um_lado_falha():
    com = _registro(resultado="sucesso")
    sem = _registro(resultado="falha_execucao", decisao_final=None)
    delta = comparar_par(com, sem)
    assert delta["comparavel"] is False
    assert "decisao_mudou" not in delta


def test_comparar_par_nao_divide_por_zero_quando_com_e_zero():
    com = _registro(chamadas_llm=0)
    sem = _registro(chamadas_llm=4)
    delta = comparar_par(com, sem)
    assert delta["chamadas_llm_variacao_pct"] is None


# ---------------------------------------------------------------------------
# gerar_conclusao
# ---------------------------------------------------------------------------

def test_gerar_conclusao_sem_pares_comparaveis_nao_quebra():
    pares = {
        "doc_bloqueado": {
            "delta": comparar_par(_registro(resultado="entrada_bloqueada"), _registro(resultado="entrada_bloqueada"))
        }
    }
    texto = gerar_conclusao(pares)
    assert "Nenhum par" in texto


def test_gerar_conclusao_relata_documentos_nao_comparaveis_separadamente():
    com_ok = _registro()
    sem_ok = _registro(duracao_total_s=40.0, tokens_totais=90_000, custo_estimado=0.12, chamadas_llm=4)
    pares = {
        "doc_ok": {"delta": comparar_par(com_ok, sem_ok)},
        "doc_falhou": {
            "delta": comparar_par(_registro(resultado="sucesso"), _registro(resultado="falha_execucao"))
        },
    }
    texto = gerar_conclusao(pares)
    assert "1 documento(s) comparável" in texto
    assert "doc_falhou" in texto


def test_gerar_conclusao_relata_quando_decisao_muda():
    com = _registro(decisao_final=2)
    sem = _registro(decisao_final=3, duracao_total_s=40.0, tokens_totais=90_000, custo_estimado=0.12, chamadas_llm=4)
    pares = {"doc_x": {"delta": comparar_par(com, sem)}}
    texto = gerar_conclusao(pares)
    assert "MUDOU em 1/1" in texto
    assert "doc_x" in texto


# ---------------------------------------------------------------------------
# salvar: persistência json/md
# ---------------------------------------------------------------------------

def test_salvar_grava_json_e_md_navegaveis(tmp_path):
    com = _registro()
    sem = _registro(duracao_total_s=40.0, tokens_totais=90_000, custo_estimado=0.12, chamadas_llm=4)
    pares = {"doc_x": {
        "doc_id": "doc_x", "provider": "gemini", "model": "gemini-2.5-flash",
        "com_cross_review": com, "sem_cross_review": sem, "delta": comparar_par(com, sem),
    }}
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
    assert "Tabela" in conteudo_md


# ---------------------------------------------------------------------------
# Integração: rodar_par em modo mock (offline, mas roda o pipeline de verdade)
# ---------------------------------------------------------------------------

def test_rodar_par_modo_mock_produz_registros_das_duas_variantes(tmp_path):
    doc = DocumentoCorpus(
        id="doc_ablacao_mock",
        titulo="Documento de teste (ablação, mock)",
        area="smoke_test",
        caracteristicas=["smoke_test", "sem_pdf_real"],
    )
    par = rodar_par(doc, mode="mock", cache_dir=tmp_path)

    assert par["com_cross_review"]["cross_review_enabled"] is True
    assert par["sem_cross_review"]["cross_review_enabled"] is False
    assert par["com_cross_review"]["resultado"] == "sucesso"
    assert par["sem_cross_review"]["resultado"] == "sucesso"
    assert par["delta"]["comparavel"] is True
