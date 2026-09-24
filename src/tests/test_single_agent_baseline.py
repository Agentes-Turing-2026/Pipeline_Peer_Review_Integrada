"""Testes da baseline experimental de AGENTE ÚNICO (Grupo 3 — topologia).

``run_demo(single_agent=True)`` precisa: (1) trocar a topologia inteira para
a fase única (`SingleAgentVerdictPhase` + `SingleAgentReportPhase`), sem
rodar `IndependentReviewPhase`/`CrossReviewPhase`/`EditorVerdictPhase`; (2)
produzir um veredito válido contra `EditorVerdictSchema`, com
`notas_por_revisor` reduzido à chave sintética `agente_unico`; (3) deixar
rastro claro (no relatório e na retomada) de que a topologia era
`single_agent` nessa execução. Tudo roda em modo mock (offline, sem chave) —
ver `src/mocks/peer_review_mock.json["single_agent_verdict"]`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pipeline import LOG_DIR, run_demo  # noqa: E402
from single_agent_baseline import AGENTE_UNICO_ID  # noqa: E402


@pytest.fixture(scope="module")
def execucao_agente_unico():
    return run_demo(mode="mock", single_agent=True)


@pytest.fixture(scope="module")
def execucao_multiagente():
    return run_demo(mode="mock", single_agent=False)


def test_flag_aparece_no_relatorio_estruturado(execucao_agente_unico, execucao_multiagente):
    assert execucao_agente_unico.data["topology"] == "single_agent"
    assert "topology" not in execucao_multiagente.data


def test_resumo_se_autodescreve_por_topologia(execucao_agente_unico, execucao_multiagente):
    """resumo_execucao.json diz sozinho de qual topologia veio — sem cruzar com final_report.json."""
    resumo_unico = execucao_agente_unico.data["resumo_execucao"]
    resumo_multi = execucao_multiagente.data["resumo_execucao"]
    assert resumo_unico["topologia"] == "agente_unico"
    assert resumo_multi["topologia"] == "multiagente"
    # A mesma comparacao (quantidade de criticas) fica disponível nos dois
    # resumos, no MESMO campo, independente da topologia que os gerou.
    assert resumo_unico["quantidade_criticas"] == 3
    assert resumo_unico["quantidade_criticas_bloqueantes"] == 0
    assert resumo_multi["quantidade_criticas"] == 5
    assert resumo_multi["quantidade_criticas_bloqueantes"] == 1


def test_agente_unico_nao_roda_fases_multiagente(execucao_agente_unico):
    resumo = execucao_agente_unico.data["resumo_execucao"]
    duracao_por_fase = resumo["duracao_por_fase_s"]
    assert "fase_unica_agente_unico" in duracao_por_fase
    assert "fase_1_revisao_independente" not in duracao_por_fase
    assert "fase_2_leitura_cruzada" not in duracao_por_fase
    assert "fase_3_editor_chefe" not in duracao_por_fase


def test_agente_unico_nao_tem_pareceres_por_revisor(execucao_agente_unico):
    assert "phase1_reviews" not in execucao_agente_unico.data
    assert "phase2_cross_reviews" not in execucao_agente_unico.data


def test_agente_unico_veredito_usa_id_sintetico(execucao_agente_unico):
    verdict = execucao_agente_unico.data["phase3_verdict"]
    assert set(verdict["notas_por_revisor"]) == {AGENTE_UNICO_ID}
    assert all(c["revisor"] == AGENTE_UNICO_ID for c in verdict["criticas"])


def test_agente_unico_fase_3_e_4_seguem_normalmente(execucao_agente_unico):
    dados = execucao_agente_unico.data
    assert dados["decisao"] in (1, 2, 3, 4)
    resumo = dados["resumo_execucao"]
    assert resumo["status_final"] == "sucesso"


def test_trace_registra_topologia_como_atributo(execucao_agente_unico):
    run_id = execucao_agente_unico.data["run_id"]
    trace_path = LOG_DIR / "traces" / f"{run_id}.jsonl"
    assert trace_path.exists()
    eventos = [
        json.loads(linha)
        for linha in trace_path.read_text(encoding="utf-8").splitlines()
        if linha.strip()
    ]
    evento_run = next(e for e in eventos if e.get("kind") == "run")
    assert evento_run["attributes"]["single_agent_enabled"] is True


def test_retomada_preserva_a_topologia_agente_unico():
    """Sem passar single_agent de novo, o resume() recupera o valor salvo."""
    report = run_demo(mode="mock", single_agent=True)
    run_id = report.data["run_id"]

    meta_path = LOG_DIR / "checkpoints" / f"{run_id}.meta.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["single_agent"] is True

    retomado = run_demo(mode="mock", run_id=run_id)
    assert retomado.data["topology"] == "single_agent"
