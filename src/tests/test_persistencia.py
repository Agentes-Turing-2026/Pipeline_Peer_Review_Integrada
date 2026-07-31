"""Testes de persistência e retomada de execuções — Grupo 1.

Cada cenário usa as fases reais do pipeline (IndependentReviewPhase, etc.) com
monkeypatch no método run() individual para controlar o comportamento sem
depender de chamadas à API do Gemini.

Cenários cobertos:
1. Nenhum checkpoint salvo quando a Fase 1 falha.
2. Checkpoint da Fase 1 existe quando a Fase 2 falha.
3. Checkpoints das Fases 1 e 2 existem quando a Fase 3 falha.
4. Na retomada, fases com checkpoint não são re-executadas.
5. Ciclo completo: falha na Fase 3 → retoma → conclui sem repetir Fases 1 e 2.
6. run_demo() salva checkpoints parciais mesmo quando o pipeline falha.
"""

import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1]
EXAMPLES = SRC / "examples"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from persistencia import CheckpointManager
from pipeline import (
    CrossReviewPhase,
    CrossReviews,
    EditorVerdictPhase,
    FinalReport,
    FinalReportPhase,
    IndependentReviewPhase,
    IndependentReviews,
    run_demo,
)
from pipeline_base import Pipeline
from review_schema import CrossReviewSchema, EditorVerdictSchema, ReviewSchema
from reviewer_agent import REVIEWERS


# ---------------------------------------------------------------------------
# Helpers: saídas válidas construídas a partir dos JSONs de exemplo do repo
# ---------------------------------------------------------------------------

def _valid_phase1() -> IndependentReviews:
    data = json.loads((EXAMPLES / "example_valid_output.json").read_text(encoding="utf-8"))
    return IndependentReviews(reviews={rid: ReviewSchema(**data) for rid in REVIEWERS})


def _valid_phase2() -> CrossReviews:
    raw = json.loads(
        (EXAMPLES / "example_cross_review_output.json").read_text(encoding="utf-8")
    )
    return CrossReviews(
        cross_reviews={
            rid: CrossReviewSchema(**raw[f"{rid}_cross_review"]) for rid in REVIEWERS
        }
    )


def _valid_phase3() -> EditorVerdictSchema:
    data = json.loads(
        (EXAMPLES / "example_editor_verdict_output.json").read_text(encoding="utf-8")
    )
    return EditorVerdictSchema(**data)


_FAKE_REPORT = FinalReport(markdown="# Relatório de Teste", data={})


def _falha(msg: str):
    """Devolve uma função que sempre levanta RuntimeError com a mensagem dada."""
    def _raise(data, context):
        raise RuntimeError(msg)
    return _raise


# ---------------------------------------------------------------------------
# Fixture: pipeline com acesso direto a cada fase e ao CheckpointManager
# ---------------------------------------------------------------------------

@pytest.fixture()
def pipeline_setup(tmp_path):
    """Devolve (pipeline, fase1, fase2, fase3, fase4, ckpt, artigo)."""
    fase1 = IndependentReviewPhase()
    fase2 = CrossReviewPhase()
    fase3 = EditorVerdictPhase()
    fase4 = FinalReportPhase()
    pipeline = Pipeline(phases=[fase1, fase2, fase3, fase4], name="peer_review")
    ckpt = CheckpointManager(tmp_path / "checkpoints", "run-teste")
    artigo = (SRC / "examples" / "example_article.txt").read_text(encoding="utf-8")
    return pipeline, fase1, fase2, fase3, fase4, ckpt, artigo


# ---------------------------------------------------------------------------
# Cenário 1 — sem checkpoint quando a Fase 1 falha
# ---------------------------------------------------------------------------

def test_sem_checkpoint_quando_fase_1_falha(pipeline_setup, monkeypatch):
    pipeline, fase1, fase2, fase3, fase4, ckpt, artigo = pipeline_setup

    monkeypatch.setattr(fase1, "run", _falha("falha fase 1"))

    with pytest.raises(RuntimeError, match="falha fase 1"):
        pipeline.run(artigo, config={"mode": "api"}, checkpoint_manager=ckpt)

    assert ckpt.fases_concluidas() == []


# ---------------------------------------------------------------------------
# Cenário 2 — checkpoint da Fase 1 existe quando a Fase 2 falha
# ---------------------------------------------------------------------------

def test_checkpoint_fase1_quando_fase2_falha(pipeline_setup, monkeypatch):
    pipeline, fase1, fase2, fase3, fase4, ckpt, artigo = pipeline_setup

    monkeypatch.setattr(fase1, "run", lambda data, context: _valid_phase1())
    monkeypatch.setattr(fase2, "run", _falha("falha fase 2"))

    with pytest.raises(RuntimeError, match="falha fase 2"):
        pipeline.run(artigo, config={"mode": "api"}, checkpoint_manager=ckpt)

    assert ckpt.load("fase_1_revisao_independente") is not None
    assert ckpt.load("fase_2_leitura_cruzada") is None


# ---------------------------------------------------------------------------
# Cenário 3 — checkpoints das Fases 1 e 2 existem quando a Fase 3 falha
# ---------------------------------------------------------------------------

def test_checkpoints_fases1_2_quando_fase3_falha(pipeline_setup, monkeypatch):
    pipeline, fase1, fase2, fase3, fase4, ckpt, artigo = pipeline_setup

    monkeypatch.setattr(fase1, "run", lambda data, context: _valid_phase1())
    monkeypatch.setattr(fase2, "run", lambda data, context: _valid_phase2())
    monkeypatch.setattr(fase3, "run", _falha("falha fase 3"))

    with pytest.raises(RuntimeError, match="falha fase 3"):
        pipeline.run(artigo, config={"mode": "api"}, checkpoint_manager=ckpt)

    assert ckpt.load("fase_1_revisao_independente") is not None
    assert ckpt.load("fase_2_leitura_cruzada") is not None
    assert ckpt.load("fase_3_editor_chefe") is None


# ---------------------------------------------------------------------------
# Cenário 4 — na retomada, fases com checkpoint não são re-executadas
# ---------------------------------------------------------------------------

def test_retomada_pula_fases_com_checkpoint(pipeline_setup, monkeypatch):
    pipeline, fase1, fase2, fase3, fase4, ckpt, artigo = pipeline_setup

    # 1ª execução: salva checkpoints de Fases 1 e 2, falha na 3.
    monkeypatch.setattr(fase1, "run", lambda data, context: _valid_phase1())
    monkeypatch.setattr(fase2, "run", lambda data, context: _valid_phase2())
    monkeypatch.setattr(fase3, "run", _falha("falha fase 3"))

    with pytest.raises(RuntimeError):
        pipeline.run(artigo, config={"mode": "api"}, checkpoint_manager=ckpt)

    # 2ª execução: contadores verificam que Fases 1 e 2 não são chamadas.
    chamadas = {"fase1": 0, "fase2": 0}

    def fase1_com_contador(data, context):
        chamadas["fase1"] += 1
        return _valid_phase1()

    def fase2_com_contador(data, context):
        chamadas["fase2"] += 1
        return _valid_phase2()

    monkeypatch.setattr(fase1, "run", fase1_com_contador)
    monkeypatch.setattr(fase2, "run", fase2_com_contador)
    monkeypatch.setattr(fase3, "run", lambda data, context: _valid_phase3())
    monkeypatch.setattr(fase4, "run", lambda data, context: _FAKE_REPORT)

    pipeline.run(artigo, config={"mode": "api"}, checkpoint_manager=ckpt)

    assert chamadas["fase1"] == 0, "Fase 1 não deve ser re-executada (checkpoint existe)"
    assert chamadas["fase2"] == 0, "Fase 2 não deve ser re-executada (checkpoint existe)"


# ---------------------------------------------------------------------------
# Cenário 5 — ciclo completo: falha → retoma → conclui sem repetir fases
# ---------------------------------------------------------------------------

def test_retomada_completa_apos_falha(pipeline_setup, monkeypatch):
    pipeline, fase1, fase2, fase3, fase4, ckpt, artigo = pipeline_setup

    # 1ª execução: falha na Fase 3.
    monkeypatch.setattr(fase1, "run", lambda data, context: _valid_phase1())
    monkeypatch.setattr(fase2, "run", lambda data, context: _valid_phase2())
    monkeypatch.setattr(fase3, "run", _falha("falha temporária"))

    with pytest.raises(RuntimeError):
        pipeline.run(artigo, config={"mode": "api"}, checkpoint_manager=ckpt)

    assert set(ckpt.fases_concluidas()) == {
        "fase_1_revisao_independente",
        "fase_2_leitura_cruzada",
    }

    # 2ª execução: problema corrigido → pipeline conclui sem re-executar 1 e 2.
    monkeypatch.setattr(fase3, "run", lambda data, context: _valid_phase3())
    monkeypatch.setattr(fase4, "run", lambda data, context: _FAKE_REPORT)

    result = pipeline.run(artigo, config={"mode": "api"}, checkpoint_manager=ckpt)

    assert result.final == _FAKE_REPORT
    concluidas = set(ckpt.fases_concluidas())
    assert "fase_1_revisao_independente" in concluidas
    assert "fase_2_leitura_cruzada" in concluidas
    assert "fase_3_editor_chefe" in concluidas


# ---------------------------------------------------------------------------
# Cenário 6 — run_demo() salva checkpoints parciais mesmo em caso de falha
# ---------------------------------------------------------------------------

def test_checkpoints_parciais_em_falha_run_demo(tmp_path, monkeypatch):
    import pipeline as pipeline_mod

    # Redireciona LOG_DIR para tmp_path para isolar o teste do ambiente real.
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(pipeline_mod, "LOG_DIR", log_dir)

    # Faz a Fase 3 falhar via patch na classe (afeta a instância criada por run_demo).
    def _fase3_falha(self, data, context):
        raise RuntimeError("falha simulada no teste")

    monkeypatch.setattr(EditorVerdictPhase, "run", _fase3_falha)

    # mode="mock" (não "api"): Fases 1 e 2 rodam offline, lendo
    # src/mocks/peer_review_mock.json, sem exigir GOOGLE_API_KEY nem chamar o
    # Gemini de verdade — consistente com o resto da suíte (test_run_id_unificado.py,
    # test_extracao_e_mock.py), sem custo nem rede.
    with pytest.raises(RuntimeError, match="falha simulada no teste"):
        run_demo(mode="mock")

    # Fases 1 e 2 devem ter checkpoints; Fase 3 não deve ter.
    ckpt_dir = log_dir / "checkpoints"
    assert ckpt_dir.exists(), "Diretório de checkpoints deveria ter sido criado"
    json_files = list(ckpt_dir.rglob("*.json"))
    assert len(json_files) >= 2, (
        f"Pelo menos 2 checkpoints (Fases 1 e 2) deveriam existir; "
        f"encontrados: {[f.name for f in json_files]}"
    )
