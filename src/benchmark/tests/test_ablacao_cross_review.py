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

from benchmark.ablacao_cross_review import (
    comparar_par,
    gerar_conclusao,
    rodar_par,
    salvar,
    separar_por_modo,
)
from benchmark.corpus import DocumentoCorpus


def _par(com: dict, sem: dict, *, mode: str = "api", **overrides) -> dict:
    """Monta um par no MESMO formato que ``rodar_par`` devolve.

    O ``mode`` no nível do par é o que separa execução real de smoke test em
    ``separar_por_modo`` — um par sem ele conta como mock.
    """
    par = {
        "mode": mode,
        "provider": com.get("provider"),
        "model": com.get("model"),
        "com_cross_review": com,
        "sem_cross_review": sem,
        "delta": comparar_par(com, sem),
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
    bloqueado = _registro(resultado="entrada_bloqueada")
    pares = {"doc_bloqueado": _par(bloqueado, bloqueado)}
    texto = gerar_conclusao(pares)
    assert "Nenhuma execução REAL" in texto


def test_gerar_conclusao_relata_documentos_nao_comparaveis_separadamente():
    com_ok = _registro()
    sem_ok = _registro(duracao_total_s=40.0, tokens_totais=90_000, custo_estimado=0.12, chamadas_llm=4)
    pares = {
        "doc_ok": _par(com_ok, sem_ok),
        "doc_falhou": _par(_registro(resultado="sucesso"), _registro(resultado="falha_execucao")),
    }
    texto = gerar_conclusao(pares)
    assert "1 documento(s) comparável" in texto
    assert "doc_falhou" in texto


def test_gerar_conclusao_relata_quando_decisao_muda():
    com = _registro(decisao_final=2)
    sem = _registro(decisao_final=3, duracao_total_s=40.0, tokens_totais=90_000, custo_estimado=0.12, chamadas_llm=4)
    pares = {"doc_x": _par(com, sem)}
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
    com = _registro()
    sem = _registro(duracao_total_s=40.0, tokens_totais=90_000, custo_estimado=0.12, chamadas_llm=4)
    pares = {
        "doc_real": _par(com, sem, mode="api"),
        "exemplo_mock": _par(com, sem, mode="mock"),
    }
    texto = gerar_conclusao(pares)

    # 1 real, não 2 — o mock sai do agregado e vira uma linha própria.
    assert "1 documento(s) comparável(is)" in texto
    assert "de 1 rodado(s)" in texto
    assert "2 execuções reais" in texto
    assert "SMOKE TEST" in texto
    assert "exemplo_mock" in texto
    assert "MUDOU em 0/1" in texto


def test_gerar_conclusao_mock_nao_desloca_a_media_das_execucoes_reais():
    """O mock tem duração ~0 e nenhum token: se entrasse na média, mexeria nela."""
    com = _registro(duracao_total_s=60.0)
    sem = _registro(duracao_total_s=30.0, tokens_totais=90_000, custo_estimado=0.12, chamadas_llm=4)
    so_real = {"doc_real": _par(com, sem, mode="api")}
    com_mock = {
        "doc_real": _par(com, sem, mode="api"),
        "exemplo_mock": _par(
            _registro(duracao_total_s=0.01, tokens_totais=None, custo_estimado=None, chamadas_llm=None),
            _registro(duracao_total_s=0.01, tokens_totais=None, custo_estimado=None, chamadas_llm=None),
            mode="mock",
        ),
    }

    linha_duracao = "Duração total: -50.0% em média sem leitura cruzada."
    assert linha_duracao in gerar_conclusao(so_real)
    assert linha_duracao in gerar_conclusao(com_mock)


def test_gerar_conclusao_sempre_declara_que_nao_houve_avaliacao_humana():
    com = _registro()
    sem = _registro(duracao_total_s=40.0, tokens_totais=90_000, custo_estimado=0.12, chamadas_llm=4)
    texto = gerar_conclusao({"doc_x": _par(com, sem)})
    assert "NÃO houve avaliação humana" in texto
    assert "indicadores automáticos" in texto


# ---------------------------------------------------------------------------
# salvar: persistência json/md
# ---------------------------------------------------------------------------

def test_salvar_grava_json_e_md_navegaveis(tmp_path):
    com = _registro()
    sem = _registro(duracao_total_s=40.0, tokens_totais=90_000, custo_estimado=0.12, chamadas_llm=4)
    pares = {"doc_x": _par(com, sem, doc_id="doc_x")}
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
    com = _registro()
    sem = _registro(duracao_total_s=40.0, tokens_totais=90_000, custo_estimado=0.12, chamadas_llm=4)
    pares = {
        "doc_real": _par(com, sem, mode="api"),
        "exemplo_mock": _par(com, sem, mode="mock"),
    }
    _, md_path = salvar(pares, gerar_conclusao(pares), destino_dir=tmp_path)
    conteudo = md_path.read_text(encoding="utf-8")

    secao_real = conteudo.index("## Execuções reais (modo api)")
    secao_mock = conteudo.index("## Smoke test")
    assert secao_real < secao_mock
    # cada doc aparece só na sua seção
    assert "doc_real" in conteudo[secao_real:secao_mock]
    assert "doc_real" not in conteudo[secao_mock:]
    assert "exemplo_mock" in conteudo[secao_mock:]
    assert "exemplo_mock" not in conteudo[secao_real:secao_mock]


def test_salvar_arredonda_numeros_da_tabela_em_vez_de_imprimir_o_float_cru(tmp_path):
    com = _registro(duracao_total_s=36.0511250999989, custo_estimado=0.0335826)
    sem = _registro(duracao_total_s=26.973181100009242, custo_estimado=0.019246399999999997)
    pares = {"doc_x": _par(com, sem)}
    _, md_path = salvar(pares, gerar_conclusao(pares), destino_dir=tmp_path)
    conteudo = md_path.read_text(encoding="utf-8")

    assert "36.05/26.97" in conteudo
    assert "0.0336/0.0192" in conteudo
    assert "36.0511250999989" not in conteudo
    assert "0.019246399999999997" not in conteudo


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
