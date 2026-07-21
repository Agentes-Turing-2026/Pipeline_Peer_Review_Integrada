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
        duracao_soma_fases_s=None,
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
        duracao_soma_fases_s=0.34567,
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


def _resumo_precisao_total() -> ResumoExecucao:
    return ResumoExecucao(
        run_id="run-precisao",
        duracao_total_s=0.0074233380146324635,
        duracao_soma_fases_s=0.005643990007229149,
        duracao_por_fase_s={
            "fase_1_revisao_independente": 0.0027357779908925295,
            "fase_2_leitura_cruzada": 0.001718544983305037,
            "fase_3_editor_chefe": 0.0009356220252811909,
            "fase_4_relatorio_final": 0.00025404500775039196,
        },
        quantidade_validacoes=7,
        quantidade_retries=0,
        quantidade_falhas=0,
        quantidade_tools_chamadas={"validar_completude": 3, "auditar_decisao_final": 1},
        requer_revisao_humana=False,
        decisao_final=3,
        status_final="sucesso",
        alertas=[],
    )


def _resumo_com_duracao_total(valor: float | None) -> ResumoExecucao:
    return ResumoExecucao(
        run_id="run-fmt",
        duracao_total_s=valor,
        duracao_soma_fases_s=valor,
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


def _linha_duracao_total(saida: str) -> str:
    for linha in saida.splitlines():
        if linha.startswith("Duração total:"):
            return linha.split(":", 1)[1].strip()
    raise AssertionError("linha 'Duração total:' não encontrada na saída")


def test_salvar_resumo_json_grava_duracao_total_s_com_precisao_total(tmp_path):
    resumo = _resumo_precisao_total()
    destino = tmp_path / "resumo.json"

    salvar_resumo_json(resumo, destino)

    conteudo_lido = json.loads(destino.read_text(encoding="utf-8"))
    assert conteudo_lido["duracao_total_s"] == 0.0074233380146324635


def test_salvar_resumo_json_grava_duracao_por_fase_s_com_precisao_total(tmp_path):
    resumo = _resumo_precisao_total()
    destino = tmp_path / "resumo.json"

    salvar_resumo_json(resumo, destino)

    conteudo_lido = json.loads(destino.read_text(encoding="utf-8"))
    assert conteudo_lido["duracao_por_fase_s"] == {
        "fase_1_revisao_independente": 0.0027357779908925295,
        "fase_2_leitura_cruzada": 0.001718544983305037,
        "fase_3_editor_chefe": 0.0009356220252811909,
        "fase_4_relatorio_final": 0.00025404500775039196,
    }


def test_salvar_resumo_json_nao_contem_a_string_duracao_ms(tmp_path):
    resumo = _resumo_precisao_total()
    destino = tmp_path / "resumo.json"

    salvar_resumo_json(resumo, destino)

    conteudo_bruto = destino.read_text(encoding="utf-8")
    assert "duracao_ms" not in conteudo_bruto


def test_imprimir_resumo_com_duracao_muito_pequena_exibe_menor_que_0_01_s(capsys):
    imprimir_resumo(_resumo_com_duracao_total(0.0003))
    saida = capsys.readouterr().out
    assert _linha_duracao_total(saida) == "< 0.01 s"


def test_imprimir_resumo_com_duracao_zero_exata_exibe_0_s(capsys):
    imprimir_resumo(_resumo_com_duracao_total(0.0))
    saida = capsys.readouterr().out
    assert _linha_duracao_total(saida) == "0 s"


def test_imprimir_resumo_com_duracao_none_exibe_n_d(capsys):
    imprimir_resumo(_resumo_com_duracao_total(None))
    saida = capsys.readouterr().out
    assert _linha_duracao_total(saida) == "n/d"


def test_imprimir_resumo_com_12_3456_arredonda_para_12_35_s_na_exibicao(capsys):
    imprimir_resumo(_resumo_com_duracao_total(12.3456))
    saida = capsys.readouterr().out
    assert _linha_duracao_total(saida) == "12.35 s"


def test_imprimir_resumo_nao_muta_o_resumo_recebido(capsys):
    resumo = _resumo_cheio()
    antes = resumo.to_dict()

    imprimir_resumo(resumo)

    depois = resumo.to_dict()
    assert antes == depois
