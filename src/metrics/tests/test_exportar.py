"""Testes determinísticos de exportação do resumo (Grupo 2 — métricas)."""

import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from metrics.exportar import imprimir_resumo, salvar_resumo_json  # noqa: E402
from metrics.resumo import ResumoExecucao  # noqa: E402


def _resumo_vazio() -> ResumoExecucao:
    return ResumoExecucao(
        run_id="run-vazio",
        duracao_total_s=None,
        duracao_por_fase_s={},
        quantidade_validacoes=0,
        quantidade_retries=0,
        quantidade_falhas=0,
        quantidade_tools_chamadas={},
        requer_revisao_humana=False,
        decisao_final=None,
        status_final="sucesso",
        alertas=[],
    )


def _resumo_cheio() -> ResumoExecucao:
    return ResumoExecucao(
        run_id="run-cheio",
        duracao_total_s=0.34567,
        duracao_por_fase_s={
            "fase_1_revisao_independente": 0.1,
            "fase_2_leitura_cruzada": 0.12,
            "fase_3_editor_chefe": 0.08,
            "fase_4_relatorio_final": 0.04567,
        },
        quantidade_validacoes=6,
        quantidade_retries=2,
        quantidade_falhas=1,
        quantidade_tools_chamadas={"validar_completude": 3, "auditar_decisao_final": 1},
        requer_revisao_humana=True,
        decisao_final=3,
        status_final="sucesso_com_alertas",
        alertas=["[fase_3_editor_chefe] divergência alta entre revisores"],
        tokens_totais=1234,
        custo_estimado=0.05,
        modelo_usado="gemini-2.5-flash",
        chamadas_llm=7,
    )


def test_salvar_resumo_json_com_caminho_explicito_grava_conteudo_identico(tmp_path):
    resumo = _resumo_cheio()
    destino = tmp_path / "resumo_execucao.json"

    caminho_retornado = salvar_resumo_json(resumo, destino)

    assert caminho_retornado == destino
    conteudo_lido = json.loads(destino.read_text(encoding="utf-8"))
    assert conteudo_lido == resumo.to_dict()


def test_salvar_resumo_json_cria_diretorios_pais_inexistentes(tmp_path):
    resumo = _resumo_vazio()
    destino = tmp_path / "subdir_novo" / "resumo.json"

    caminho_retornado = salvar_resumo_json(resumo, destino)

    assert caminho_retornado == destino
    assert destino.exists()
    conteudo_lido = json.loads(destino.read_text(encoding="utf-8"))
    assert conteudo_lido == resumo.to_dict()


def test_imprimir_resumo_nao_levanta_excecao_com_resumo_vazio(capsys):
    imprimir_resumo(_resumo_vazio())
    saida = capsys.readouterr().out
    assert saida != ""


def test_imprimir_resumo_nao_levanta_excecao_com_resumo_cheio(capsys):
    imprimir_resumo(_resumo_cheio())
    saida = capsys.readouterr().out
    assert saida != ""
