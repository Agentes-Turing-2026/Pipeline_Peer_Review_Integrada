"""Testes da variante experimental SEM leitura cruzada (Grupo 2 — benchmark).

``run_demo(cross_review=False)`` precisa: (1) não quebrar o contrato de saída
da Fase 2 (CrossReviewSchema continua sendo o que a Fase 3 recebe); (2) não
inventar mudança de posição nenhuma; (3) deixar rastro claro (no relatório e
na retomada) de que a leitura cruzada estava desligada nessa execução. Tudo
roda em modo mock (offline, sem chave), então nenhum destes testes mede
tokens/custo reais — isso é o que a Fase 2 real (LLM) mede; aqui garantimos
apenas que a variante determinística é válida e consistente.
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


@pytest.fixture(scope="module")
def execucao_sem_cross_review():
    return run_demo(mode="mock", cross_review=False)


@pytest.fixture(scope="module")
def execucao_com_cross_review():
    return run_demo(mode="mock", cross_review=True)


def test_flag_aparece_no_relatorio_estruturado(execucao_sem_cross_review, execucao_com_cross_review):
    assert execucao_sem_cross_review.data["cross_review_enabled"] is False
    assert execucao_com_cross_review.data["cross_review_enabled"] is True


def test_sem_cross_review_nenhum_revisor_muda_de_posicao(execucao_sem_cross_review):
    cross = execucao_sem_cross_review.data["phase2_cross_reviews"]
    assert cross, "Fase 2 deveria produzir um CrossReviewSchema por revisor mesmo desativada"
    for rid, cr in cross.items():
        assert cr["mudou_posicao"] is False, rid
        assert cr["mudancas"] == [], rid


def test_sem_cross_review_parecer_revisado_e_identico_ao_da_fase_1(execucao_sem_cross_review):
    reviews_fase1 = execucao_sem_cross_review.data["phase1_reviews"]
    cross = execucao_sem_cross_review.data["phase2_cross_reviews"]
    for rid, cr in cross.items():
        assert cr["parecer_revisado"] == reviews_fase1[rid]


def test_sem_cross_review_resposta_aos_pares_documenta_o_motivo(execucao_sem_cross_review):
    for cr in execucao_sem_cross_review.data["phase2_cross_reviews"].values():
        assert "desativada" in cr["resposta_aos_pares"].lower()


def test_sem_cross_review_fase_3_e_4_seguem_normalmente(execucao_sem_cross_review):
    dados = execucao_sem_cross_review.data
    assert dados["decisao"] in (1, 2, 3, 4)
    assert dados["phase3_verdict"]["notas_por_revisor"]
    resumo = dados["resumo_execucao"]
    assert resumo["status_final"] == "sucesso"
    assert "fase_2_leitura_cruzada" in resumo["duracao_por_fase_s"]


def test_trace_registra_leitura_cruzada_ativa_como_atributo(execucao_sem_cross_review):
    run_id = execucao_sem_cross_review.data["run_id"]
    trace_path = LOG_DIR / "traces" / f"{run_id}.jsonl"
    assert trace_path.exists()
    eventos = [
        json.loads(linha)
        for linha in trace_path.read_text(encoding="utf-8").splitlines()
        if linha.strip()
    ]
    evento_run = next(e for e in eventos if e.get("kind") == "run")
    assert evento_run["attributes"]["cross_review_enabled"] is False


def test_retomada_preserva_a_variante_sem_cross_review(tmp_path):
    """Sem passar cross_review de novo, o resume() recupera o valor salvo."""
    report = run_demo(mode="mock", cross_review=False)
    run_id = report.data["run_id"]

    meta_path = LOG_DIR / "checkpoints" / f"{run_id}.meta.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["cross_review"] is False

    retomado = run_demo(mode="mock", run_id=run_id)
    assert retomado.data["cross_review_enabled"] is False
