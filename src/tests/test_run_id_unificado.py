"""Testes do run_id UNIFICADO entre métricas, trace, validação e relatório.

Grupos 2 e 3 — ajuste de integração: o relatório, o trace e os eventos de
validação já compartilhavam o mesmo ``run_id``, mas o resumo de métricas
(``resumo_execucao``) criava um identificador próprio no ``ExecutionCollector``.
Estes testes rodam o pipeline completo em modo mock (offline) e garantem que
TODAS as superfícies da execução apontam para o MESMO identificador.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from eventos_validacao import ler_eventos  # noqa: E402
from pipeline import LOG_DIR, run_demo  # noqa: E402


@pytest.fixture(scope="module")
def execucao_mock():
    """Uma execução completa do pipeline em modo mock, compartilhada pelos testes."""
    report = run_demo(mode="mock")
    return report


def test_resumo_de_metricas_usa_o_run_id_da_execucao(execucao_mock):
    """O resumo auditável (Grupo 2) herda o run_id do relatório — não cria outro."""
    run_id = execucao_mock.data["run_id"]
    resumo = execucao_mock.data.get("resumo_execucao")

    assert resumo is not None, "relatório deveria incluir o resumo_execucao"
    assert resumo["run_id"] == run_id


def test_trace_usa_o_mesmo_run_id_do_relatorio(execucao_mock):
    """O arquivo de trace existe sob o run_id do relatório e todos os eventos o carregam."""
    run_id = execucao_mock.data["run_id"]
    trace_path = LOG_DIR / "traces" / f"{run_id}.jsonl"

    assert trace_path.exists(), f"trace da execução não encontrado: {trace_path}"

    eventos = [
        json.loads(linha)
        for linha in trace_path.read_text(encoding="utf-8").splitlines()
        if linha.strip()
    ]
    assert eventos, "trace da execução está vazio"
    assert {evento["run_id"] for evento in eventos} == {run_id}


def test_eventos_de_validacao_usam_o_mesmo_run_id(execucao_mock):
    """Os eventos de validação da execução são encontráveis pelo run_id do relatório."""
    run_id = execucao_mock.data["run_id"]

    eventos = ler_eventos(run_id=run_id)

    assert eventos, "nenhum evento de validação registrado para o run_id da execução"
    assert {evento["run_id"] for evento in eventos} == {run_id}


def test_resumo_salvo_em_disco_usa_o_mesmo_run_id(execucao_mock):
    """O resumo_execucao.json salvo em outputs/<run_id>/ carrega o mesmo identificador."""
    run_id = execucao_mock.data["run_id"]
    resumo_path = SRC / "outputs" / run_id / "resumo_execucao.json"

    assert resumo_path.exists(), f"resumo da execução não encontrado: {resumo_path}"

    resumo = json.loads(resumo_path.read_text(encoding="utf-8"))
    assert resumo["run_id"] == run_id
