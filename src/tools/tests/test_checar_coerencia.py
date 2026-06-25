"""Testes determinísticos de ``checar_coerencia`` (Tool 2 — Grupo 2).

Cobrem os dois modos do contrato oficial (parecer ``ReviewSchema`` e veredito
``EditorVerdictSchema``), a detecção automática de tipo, o comportamento
defensivo (avisos em vez de erro) e a fórmula do ``score_coerencia``.
"""

import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tools.checar_coerencia import checar_coerencia  # noqa: E402

EXAMPLES = SRC / "examples"


def _carregar(nome: str) -> dict:
    return json.loads((EXAMPLES / nome).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Modo parecer (ReviewSchema)
# ---------------------------------------------------------------------------

def test_parecer_valido_eh_coerente():
    resultado = checar_coerencia(_carregar("example_valid_output.json"))
    assert resultado["status"] == "ok"
    assert resultado["tipo"] == "parecer"  # detecção automática (sem 'decisao')
    assert resultado["coerente"] is True
    assert resultado["inconsistencias"] == []
    assert resultado["score_coerencia"] == 1.0


def test_parecer_nota_geral_diverge_dos_criterios():
    parecer = _carregar("example_valid_output.json")
    # Critérios médios ~3, mas nota_geral 1 (Rejeitar) → diferença > 1.0.
    parecer["nota_geral"]["nota"] = 1
    resultado = checar_coerencia(parecer)
    assert resultado["coerente"] is False
    assert resultado["inconsistencias"][0]["tipo"] == "nota_vs_criterios"


def test_parecer_diferenca_no_limiar_ainda_eh_coerente():
    parecer = _carregar("example_valid_output.json")
    # Critérios todos 4 (média 4.0), nota_geral 3 → diferença exatamente 1.0 (não passa).
    for criterio in ("solidez_tecnica", "originalidade", "significancia", "clareza"):
        parecer[criterio]["nota"] = 4
    parecer["nota_geral"]["nota"] = 3
    resultado = checar_coerencia(parecer)
    assert resultado["coerente"] is True


def test_parecer_sem_nota_geral_vira_aviso_nao_erro():
    parecer = _carregar("example_valid_output.json")
    del parecer["nota_geral"]
    resultado = checar_coerencia(parecer)
    assert resultado["status"] == "ok"
    assert resultado["coerente"] is True  # nada incoerente foi verificável
    assert any("nota_geral" in aviso for aviso in resultado["avisos"])


# ---------------------------------------------------------------------------
# Modo veredito (EditorVerdictSchema)
# ---------------------------------------------------------------------------

def test_veredito_valido_eh_coerente():
    resultado = checar_coerencia(_carregar("example_editor_verdict_output.json"))
    assert resultado["status"] == "ok"
    assert resultado["tipo"] == "veredito"  # detecção automática via 'decisao'
    assert resultado["coerente"] is True
    assert resultado["score_coerencia"] == 1.0


def test_veredito_incoerente_versionado_dispara_as_tres_checagens():
    resultado = checar_coerencia(_carregar("example_editor_verdict_incoerente.json"))
    assert resultado["tipo"] == "veredito"
    assert resultado["coerente"] is False
    tipos = {item["tipo"] for item in resultado["inconsistencias"]}
    assert tipos == {
        "decisao_vs_notas",
        "aceite_com_critica_bloqueante",
        "critica_sem_revisor",
    }
    assert resultado["score_coerencia"] == 0.0


def test_decisao_alinhada_com_notas_nao_dispara_decisao_vs_notas():
    veredito = _carregar("example_editor_verdict_output.json")  # decisao 3, notas ~3.3
    tipos = {i["tipo"] for i in checar_coerencia(veredito)["inconsistencias"]}
    assert "decisao_vs_notas" not in tipos


def test_aceite_com_critica_bloqueante_isolado():
    # Decisão 4 coerente com as notas (todas 4), mas há uma crítica bloqueante.
    veredito = {
        "decisao": 4,
        "justificativa": "Aceito.",
        "sintese": "Resumo.",
        "notas_por_revisor": {"statistician": 4, "domain_expert": 4},
        "criticas": [{"revisor": "statistician", "tipo": "critica", "texto": "Falha grave."}],
    }
    resultado = checar_coerencia(veredito)
    tipos = {i["tipo"] for i in resultado["inconsistencias"]}
    assert "aceite_com_critica_bloqueante" in tipos
    assert "decisao_vs_notas" not in tipos  # 4 vs média 4.0 → coerente


def test_fraqueza_nao_bloqueia_aceite():
    # Crítica do tipo 'fraqueza' (não bloqueante) com decisão 4 é coerente.
    veredito = {
        "decisao": 4,
        "justificativa": "Aceito.",
        "sintese": "Resumo.",
        "notas_por_revisor": {"statistician": 4, "domain_expert": 4},
        "criticas": [{"revisor": "statistician", "tipo": "fraqueza", "texto": "Detalhe menor."}],
    }
    resultado = checar_coerencia(veredito)
    assert resultado["coerente"] is True


def test_veredito_notas_vazias_pula_checagens_com_aviso():
    veredito = {
        "decisao": 4,
        "justificativa": "Aceito.",
        "sintese": "Resumo.",
        "notas_por_revisor": {},
        "criticas": [{"revisor": "x", "tipo": "fraqueza", "texto": "y"}],
    }
    resultado = checar_coerencia(veredito)
    assert resultado["status"] == "ok"
    # decisao_vs_notas e critica_sem_revisor pulam; sobra aceite_com_critica (sem
    # crítica bloqueante aqui) → coerente, com avisos das checagens puladas.
    assert any("notas_por_revisor" in aviso for aviso in resultado["avisos"])


# ---------------------------------------------------------------------------
# Detecção de tipo e entrada inválida
# ---------------------------------------------------------------------------

def test_deteccao_auto_distingue_parecer_de_veredito():
    assert checar_coerencia(_carregar("example_valid_output.json"))["tipo"] == "parecer"
    assert checar_coerencia(_carregar("example_editor_verdict_output.json"))["tipo"] == "veredito"


def test_tipo_forcado_sobrepoe_heuristica():
    # Forçar 'parecer' num veredito faz a checagem de parecer não achar critérios.
    resultado = checar_coerencia(_carregar("example_editor_verdict_output.json"), tipo="parecer")
    assert resultado["tipo"] == "parecer"


def test_entrada_nao_dict_retorna_erro():
    resultado = checar_coerencia(["não", "é", "dict"])
    assert resultado["status"] == "erro"
    assert resultado["coerente"] is False
    assert resultado["score_coerencia"] == 0.0


def test_nota_booleana_nao_conta_como_numero():
    parecer = _carregar("example_valid_output.json")
    parecer["solidez_tecnica"]["nota"] = True  # bool não pode contar como nota
    resultado = checar_coerencia(parecer)
    assert any("ignorados na média" in aviso for aviso in resultado["avisos"])
