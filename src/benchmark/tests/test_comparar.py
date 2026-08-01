"""Testes determinísticos de ``gerar_comparativo``/``salvar_comparativo`` (Grupo 2 — benchmark).

100% offline: usa registros de execução sintéticos, nunca chama run_demo/pipeline.
"""

import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from benchmark.comparar import gerar_comparativo, imprimir_comparativo, salvar_comparativo  # noqa: E402


def _registro(**overrides) -> dict:
    base = {
        "doc_id": "doc_x",
        "titulo": "Artigo X",
        "area": "inteligencia_artificial",
        "caracteristicas": ["curto"],
        "mode": "mock",
        "timestamp": "2026-08-01T12:00:00+00:00",
        "run_id": "run_abc",
        "resultado": "sucesso",
        "decisao_final": 3,
        "requer_revisao_humana": False,
        "duracao_total_s": 1.23,
        "tokens_totais": 100,
        "custo_estimado": None,
        "quantidade_tools_chamadas": {"validar_completude": 3},
        "quantidade_retries": 0,
        "quantidade_falhas": 0,
        "alertas": [],
        "erro": None,
    }
    base.update(overrides)
    return base


def _execucoes_mistas() -> dict:
    return {
        "doc_sucesso": _registro(doc_id="doc_sucesso", resultado="sucesso"),
        "doc_bloqueado": _registro(
            doc_id="doc_bloqueado", resultado="entrada_bloqueada", run_id=None,
            decisao_final=None, requer_revisao_humana=None, duracao_total_s=None,
            tokens_totais=None, quantidade_tools_chamadas=None, quantidade_retries=None,
            quantidade_falhas=None,
            erro={"tipo": "EntradaInvalidaError", "mensagem": "PDF corrompido"},
        ),
        "doc_falhou": _registro(
            doc_id="doc_falhou", resultado="falha_execucao",
            erro={"tipo": "RuntimeError", "mensagem": "algo quebrou"},
        ),
        "doc_alerta": _registro(doc_id="doc_alerta", resultado="sucesso_com_alertas"),
    }


def test_gerar_comparativo_separa_apenas_nao_sucesso_no_bloco_atencao():
    execucoes = _execucoes_mistas()
    comparativo = gerar_comparativo(execucoes)

    assert set(comparativo["atencao"]) == {"doc_bloqueado", "doc_falhou", "doc_alerta"}
    assert "doc_sucesso" not in comparativo["atencao"]


def test_gerar_comparativo_tabela_preserva_todos_os_campos_do_registro():
    execucoes = _execucoes_mistas()
    comparativo = gerar_comparativo(execucoes)

    assert comparativo["tabela"]["doc_sucesso"] == execucoes["doc_sucesso"]
    assert comparativo["tabela"]["doc_bloqueado"] == execucoes["doc_bloqueado"]
    assert "gerado_em" in comparativo


def test_gerar_comparativo_sem_pendencias_gera_atencao_vazia():
    execucoes = {"doc_ok": _registro(doc_id="doc_ok", resultado="sucesso")}
    comparativo = gerar_comparativo(execucoes)
    assert comparativo["atencao"] == []


def test_salvar_comparativo_grava_json_e_markdown_com_atencao_primeiro(tmp_path):
    execucoes = _execucoes_mistas()
    comparativo = gerar_comparativo(execucoes)

    json_path, md_path = salvar_comparativo(comparativo, destino_dir=tmp_path)

    assert json_path.exists()
    assert md_path.exists()

    dados_json = json.loads(json_path.read_text(encoding="utf-8"))
    assert dados_json["atencao"] == comparativo["atencao"]

    conteudo_md = md_path.read_text(encoding="utf-8")
    indice_atencao = conteudo_md.index("Atenção")
    indice_tabela = conteudo_md.index("Tabela completa")
    assert indice_atencao < indice_tabela
    for doc_id in comparativo["atencao"]:
        assert doc_id in conteudo_md


def test_imprimir_comparativo_nao_levanta_excecao(capsys):
    comparativo = gerar_comparativo(_execucoes_mistas())
    imprimir_comparativo(comparativo)
    saida = capsys.readouterr().out
    assert "COMPARATIVO" in saida
    assert "doc_bloqueado" in saida
