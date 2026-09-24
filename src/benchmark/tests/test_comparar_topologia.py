"""Testes do comparativo de três topologias (Grupo 3 — piloto tres topologias).

Mesma estrutura de ``test_ablacao_cross_review.py``: (1) testes 100% offline
de ``comparar_par``/``gerar_conclusao``/``salvar`` com registros sintéticos
(sem rodar o pipeline); (2) um teste de integração em modo mock (offline, sem
chave, mas exercita ``rodar_trio`` de ponta a ponta — inclusive as três
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
    _carregar_pares_existentes,
    _todas_sucesso,
    comparar_par,
    gerar_conclusao,
    rodar_trio,
    salvar,
    separar_por_modo,
)
from benchmark.corpus import DocumentoCorpus


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


def _trio(c0: dict, c1: dict, c2: dict, *, mode: str = "api", **overrides) -> dict:
    """Monta um registro no MESMO formato que ``rodar_trio`` devolve."""
    execucoes = {"agente_unico": c0, "sem_leitura_cruzada": c1, "completo": c2}
    par = {
        "mode": mode,
        "provider": c2.get("provider"),
        "model": c2.get("model"),
        "execucoes": execucoes,
        "comparavel": _todas_sucesso(execucoes),
        "saltos": {
            "agente_unico_para_sem_leitura_cruzada": comparar_par(c0, c1),
            "sem_leitura_cruzada_para_completo": comparar_par(c1, c2),
        },
        "delta_total": comparar_par(c0, c2),
    }
    par.update(overrides)
    return par


# ---------------------------------------------------------------------------
# comparar_par: deltas numéricos e de qualidade (função genérica, reaproveitada
# pros dois saltos e pro total — testada uma vez só, independente de trio)
# ---------------------------------------------------------------------------

def test_comparar_par_calcula_variacao_percentual_negativa_quando_comparado_reduz_consumo():
    base = _registro(duracao_total_s=60.0, tokens_totais=150_000, custo_estimado=0.20, chamadas_llm=7)
    comparado = _registro(duracao_total_s=10.0, tokens_totais=20_000, custo_estimado=0.03, chamadas_llm=1)

    delta = comparar_par(base, comparado)

    assert delta["comparavel"] is True
    assert delta["duracao_total_s_variacao_pct"] == pytest.approx(-83.333, rel=1e-3)
    assert delta["tokens_totais_variacao_pct"] == pytest.approx((20_000 - 150_000) / 150_000 * 100)
    assert delta["custo_estimado_variacao_pct"] == pytest.approx((0.03 - 0.20) / 0.20 * 100)
    assert delta["chamadas_llm_variacao_pct"] == pytest.approx((1 - 7) / 7 * 100)


def test_comparar_par_sem_dado_devolve_none_em_vez_de_zero():
    base = _registro(tokens_totais=None, custo_estimado=None)
    comparado = _registro(tokens_totais=None, custo_estimado=None)
    delta = comparar_par(base, comparado)
    assert delta["tokens_totais_variacao_pct"] is None
    assert delta["custo_estimado_variacao_pct"] is None


def test_comparar_par_nao_compara_notas_quando_revisores_diferem():
    """Com o agente único envolvido os revisores diferem — as notas não entram no delta."""
    base = _registro(notas_por_revisor={"statistician": 2, "domain_expert": 3, "copyeditor": 2})
    comparado = _registro(notas_por_revisor={"agente_unico": 3})
    delta = comparar_par(base, comparado)
    assert "notas_por_revisor_mudaram" not in delta


def test_comparar_par_compara_notas_quando_os_revisores_sao_os_mesmos():
    """C1 vs C2: os mesmos três revisores — a mudança de nota é detectada."""
    base = _registro(notas_por_revisor={"statistician": 2, "domain_expert": 3, "copyeditor": 2})
    comparado = _registro(notas_por_revisor={"statistician": 3, "domain_expert": 3, "copyeditor": 2})
    assert comparar_par(base, comparado)["notas_por_revisor_mudaram"] is True
    assert comparar_par(base, dict(base))["notas_por_revisor_mudaram"] is False


def test_comparar_par_marca_decisao_que_mudou():
    base = _registro(decisao_final=2)
    comparado = _registro(decisao_final=3)
    delta = comparar_par(base, comparado)
    assert delta["decisao_mudou"] is True


def test_comparar_par_decisao_estavel_nao_e_marcada_como_mudanca():
    base = _registro(decisao_final=3)
    comparado = _registro(decisao_final=3)
    delta = comparar_par(base, comparado)
    assert delta["decisao_mudou"] is False


def test_comparar_par_nao_comparavel_quando_um_lado_falha():
    base = _registro(resultado="sucesso")
    comparado = _registro(resultado="falha_execucao", decisao_final=None)
    delta = comparar_par(base, comparado)
    assert delta["comparavel"] is False
    assert "decisao_mudou" not in delta


def test_comparar_par_nao_divide_por_zero_quando_base_e_zero():
    base = _registro(chamadas_llm=0)
    comparado = _registro(chamadas_llm=1)
    delta = comparar_par(base, comparado)
    assert delta["chamadas_llm_variacao_pct"] is None


# ---------------------------------------------------------------------------
# _todas_sucesso / _trio: comparabilidade exige as TRÊS execuções com sucesso
# ---------------------------------------------------------------------------

def test_todas_sucesso_falso_quando_qualquer_topologia_falha():
    execucoes = {
        "agente_unico": _registro(resultado="sucesso"),
        "sem_leitura_cruzada": _registro(resultado="falha_execucao"),
        "completo": _registro(resultado="sucesso"),
    }
    assert _todas_sucesso(execucoes) is False


def test_trio_comparavel_quando_as_tres_topologias_tem_sucesso():
    par = _trio(_registro(), _registro(), _registro())
    assert par["comparavel"] is True


# ---------------------------------------------------------------------------
# gerar_conclusao
# ---------------------------------------------------------------------------

def test_gerar_conclusao_sem_pares_comparaveis_nao_quebra():
    bloqueado = _registro(resultado="entrada_bloqueada")
    pares = {"doc_bloqueado": _trio(bloqueado, bloqueado, bloqueado)}
    texto = gerar_conclusao(pares)
    assert "Nenhuma execução REAL" in texto


def test_gerar_conclusao_relata_documentos_nao_comparaveis_separadamente():
    c0_ok = _registro(duracao_total_s=10.0, tokens_totais=20_000, custo_estimado=0.03, chamadas_llm=1)
    c1_ok = _registro(duracao_total_s=30.0, tokens_totais=80_000, custo_estimado=0.10, chamadas_llm=4)
    c2_ok = _registro(duracao_total_s=60.0, tokens_totais=150_000, custo_estimado=0.20, chamadas_llm=7)
    pares = {
        "doc_ok": _trio(c0_ok, c1_ok, c2_ok),
        "doc_falhou": _trio(
            _registro(resultado="sucesso"),
            _registro(resultado="falha_execucao"),
            _registro(resultado="sucesso"),
        ),
    }
    texto = gerar_conclusao(pares)
    assert "1 documento(s) comparável" in texto
    assert "doc_falhou" in texto


def test_gerar_conclusao_relata_saltos_separados():
    c0 = _registro(decisao_final=2, duracao_total_s=10.0, tokens_totais=20_000, custo_estimado=0.03, chamadas_llm=1)
    c1 = _registro(decisao_final=3, duracao_total_s=30.0, tokens_totais=80_000, custo_estimado=0.10, chamadas_llm=4)
    c2 = _registro(decisao_final=3, duracao_total_s=60.0, tokens_totais=150_000, custo_estimado=0.20, chamadas_llm=7)
    texto = gerar_conclusao({"doc_x": _trio(c0, c1, c2)})

    assert "Salto 1" in texto
    assert "Salto 2" in texto
    assert "Total" in texto
    # Salto 1 (C0->C1) é onde a decisão muda (2->3); Salto 2 (C1->C2) é estável (3->3).
    assert "Decisão final mudou em 1/1" in texto
    assert "Decisão final mudou em 0/1" in texto


def test_gerar_conclusao_nota_estrutural_do_total_tem_o_mesmo_sinal_da_variacao():
    """A base é C0: de 1 para 7 chamadas a variação é +600%, e a nota precisa dizer o mesmo."""
    c0 = _registro(chamadas_llm=1)
    c1 = _registro(chamadas_llm=4)
    c2 = _registro(chamadas_llm=7)
    texto = gerar_conclusao({"doc_x": _trio(c0, c1, c2)})

    bloco_total = texto[texto.index("### Total"):]
    assert "Chamadas LLM: +600.0% em média. (esperado +600% estrutural" in bloco_total


def test_gerar_conclusao_mostra_notas_por_revisor_so_no_salto_2():
    c0 = _registro(notas_por_revisor={"agente_unico": 3}, chamadas_llm=1)
    c1 = _registro(notas_por_revisor={"statistician": 2, "domain_expert": 3, "copyeditor": 3}, chamadas_llm=4)
    c2 = _registro(notas_por_revisor={"statistician": 3, "domain_expert": 3, "copyeditor": 3}, chamadas_llm=7)
    texto = gerar_conclusao({"doc_x": _trio(c0, c1, c2)})

    inicio_salto_2 = texto.index("### Salto 2")
    inicio_total = texto.index("### Total")
    assert texto.count("Notas por revisor mudaram") == 1
    assert "Notas por revisor mudaram em 1/1" in texto[inicio_salto_2:inicio_total]


def test_gerar_conclusao_sempre_declara_que_nao_houve_avaliacao_humana():
    c0 = _registro(duracao_total_s=10.0, tokens_totais=20_000, custo_estimado=0.03, chamadas_llm=1)
    c1 = _registro(duracao_total_s=30.0, tokens_totais=80_000, custo_estimado=0.10, chamadas_llm=4)
    c2 = _registro(duracao_total_s=60.0, tokens_totais=150_000, custo_estimado=0.20, chamadas_llm=7)
    texto = gerar_conclusao({"doc_x": _trio(c0, c1, c2)})
    assert "NÃO houve avaliação humana" in texto
    assert "indicadores automáticos" in texto
    assert "só é comparável entre C1 e C2" in texto


# ---------------------------------------------------------------------------
# Separação mock x real: o smoke test não pode contaminar o agregado
# ---------------------------------------------------------------------------

def test_separar_por_modo_classifica_api_como_real_e_o_resto_como_mock():
    reais, mock = separar_por_modo({
        "doc_api": _trio(_registro(), _registro(), _registro(), mode="api"),
        "doc_mock": _trio(_registro(), _registro(), _registro(), mode="mock"),
        "doc_sem_modo": {"comparavel": False},
    })
    assert set(reais) == {"doc_api"}
    assert set(mock) == {"doc_mock", "doc_sem_modo"}


def test_gerar_conclusao_nao_conta_o_mock_entre_os_documentos_comparaveis():
    c0 = _registro(duracao_total_s=10.0, tokens_totais=20_000, custo_estimado=0.03, chamadas_llm=1)
    c1 = _registro(duracao_total_s=30.0, tokens_totais=80_000, custo_estimado=0.10, chamadas_llm=4)
    c2 = _registro(duracao_total_s=60.0, tokens_totais=150_000, custo_estimado=0.20, chamadas_llm=7)
    pares = {
        "doc_real": _trio(c0, c1, c2, mode="api"),
        "exemplo_mock": _trio(c0, c1, c2, mode="mock"),
    }
    texto = gerar_conclusao(pares)

    assert "1 documento(s) comparável(is)" in texto
    assert "de 1 rodado(s)" in texto
    assert "3 execuções reais" in texto
    assert "SMOKE TEST" in texto
    assert "exemplo_mock" in texto


# ---------------------------------------------------------------------------
# salvar: persistência json/md
# ---------------------------------------------------------------------------

def test_salvar_grava_json_e_md_navegaveis(tmp_path):
    c0 = _registro(duracao_total_s=10.0, tokens_totais=20_000, custo_estimado=0.03, chamadas_llm=1)
    c1 = _registro(duracao_total_s=30.0, tokens_totais=80_000, custo_estimado=0.10, chamadas_llm=4)
    c2 = _registro(duracao_total_s=60.0, tokens_totais=150_000, custo_estimado=0.20, chamadas_llm=7)
    pares = {"doc_x": _trio(c0, c1, c2)}
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
    c0 = _registro(duracao_total_s=10.0, tokens_totais=20_000, custo_estimado=0.03, chamadas_llm=1)
    c1 = _registro(duracao_total_s=30.0, tokens_totais=80_000, custo_estimado=0.10, chamadas_llm=4)
    c2 = _registro(duracao_total_s=60.0, tokens_totais=150_000, custo_estimado=0.20, chamadas_llm=7)
    pares = {
        "doc_real": _trio(c0, c1, c2, mode="api"),
        "exemplo_mock": _trio(c0, c1, c2, mode="mock"),
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
# _carregar_pares_existentes: pares no formato PAREADO antigo (antes das três
# vias) não podem sumir em silêncio — foram execuções reais, pagas em API.
# ---------------------------------------------------------------------------

def test_carregar_pares_existentes_arquiva_formato_antigo_sem_perder_o_dado(tmp_path):
    caminho = tmp_path / "comparativo_topologia.json"
    par_antigo = {
        "doc_id": "doc_legado",
        "mode": "api",
        "multiagente": _registro(),
        "agente_unico": _registro(),
        "delta": {"comparavel": True},
    }
    caminho.write_text(
        json.dumps({"pares": {"doc_legado": par_antigo}}, ensure_ascii=False),
        encoding="utf-8",
    )

    validos = _carregar_pares_existentes(caminho)

    assert validos == {}
    legado_path = tmp_path / "comparativo_topologia_legado.json"
    assert legado_path.exists()
    arquivado = json.loads(legado_path.read_text(encoding="utf-8"))
    assert arquivado["pares"]["doc_legado"] == par_antigo


def test_carregar_pares_existentes_mescla_com_arquivamento_anterior(tmp_path):
    caminho = tmp_path / "comparativo_topologia.json"
    legado_path = tmp_path / "comparativo_topologia_legado.json"
    legado_path.write_text(
        json.dumps({"pares": {"ja_arquivado": {"algo": True}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    caminho.write_text(
        json.dumps({"pares": {"doc_novo_legado": {"multiagente": {}, "agente_unico": {}}}}),
        encoding="utf-8",
    )

    _carregar_pares_existentes(caminho)

    arquivado = json.loads(legado_path.read_text(encoding="utf-8"))
    assert set(arquivado["pares"]) == {"ja_arquivado", "doc_novo_legado"}


def test_carregar_pares_existentes_preserva_os_dois_quando_doc_id_colide(tmp_path):
    caminho = tmp_path / "comparativo_topologia.json"
    legado_path = tmp_path / "comparativo_topologia_legado.json"
    antigo = {"multiagente": {}, "agente_unico": {}, "timestamp": "2026-09-01T00:00:00+00:00"}
    outro = {"multiagente": {"x": 1}, "agente_unico": {}, "timestamp": "2026-09-17T00:00:00+00:00"}
    legado_path.write_text(json.dumps({"pares": {"doc_x": antigo}}), encoding="utf-8")
    caminho.write_text(json.dumps({"pares": {"doc_x": outro}}), encoding="utf-8")

    _carregar_pares_existentes(caminho)

    arquivado = json.loads(legado_path.read_text(encoding="utf-8"))["pares"]
    assert arquivado["doc_x"] == antigo
    assert arquivado["doc_x@2026-09-17T00:00:00+00:00"] == outro


def test_carregar_pares_existentes_mantem_pares_ja_no_formato_novo(tmp_path):
    caminho = tmp_path / "comparativo_topologia.json"
    par_novo = _trio(_registro(), _registro(), _registro())
    caminho.write_text(
        json.dumps({"pares": {"doc_novo": par_novo}}, ensure_ascii=False),
        encoding="utf-8",
    )

    validos = _carregar_pares_existentes(caminho)

    assert set(validos) == {"doc_novo"}
    assert not (tmp_path / "comparativo_topologia_legado.json").exists()


# ---------------------------------------------------------------------------
# rodar_trio: integração de ponta a ponta em modo mock (sem chave, sem custo)
# ---------------------------------------------------------------------------

def test_rodar_trio_em_modo_mock_produz_as_tres_execucoes(tmp_path):
    doc = DocumentoCorpus(
        id="doc_topologia_mock",
        titulo="Documento de teste (topologia, mock)",
        area="smoke_test",
        caracteristicas=["smoke_test", "sem_pdf_real"],
    )
    par = rodar_trio(doc, mode="mock", cache_dir=tmp_path)

    assert par["doc_id"] == "doc_topologia_mock"
    assert par["mode"] == "mock"
    execucoes = par["execucoes"]
    assert execucoes["agente_unico"]["resultado"] == "sucesso"
    assert execucoes["sem_leitura_cruzada"]["resultado"] == "sucesso"
    assert execucoes["completo"]["resultado"] == "sucesso"
    assert execucoes["agente_unico"]["single_agent_enabled"] is True
    assert execucoes["sem_leitura_cruzada"]["single_agent_enabled"] is False
    assert execucoes["sem_leitura_cruzada"]["cross_review_enabled"] is False
    assert execucoes["completo"]["cross_review_enabled"] is True
    assert par["comparavel"] is True
    assert set(par["saltos"]) == {
        "agente_unico_para_sem_leitura_cruzada", "sem_leitura_cruzada_para_completo",
    }
