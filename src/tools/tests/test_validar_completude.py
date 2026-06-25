"""Testes determinísticos de ``validar_completude`` (Tool 1 — Grupo 2)."""

import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tools.validar_completude import validar_completude  # noqa: E402

EXAMPLES = SRC / "examples"


def _carregar(nome: str) -> dict:
    return json.loads((EXAMPLES / nome).read_text(encoding="utf-8"))


def test_parecer_oficial_valido_eh_completo():
    resultado = validar_completude(_carregar("example_valid_output.json"))
    assert resultado["status"] == "ok"
    assert resultado["tipo"] == "parecer"
    assert resultado["completo"] is True
    assert resultado["campos_faltando"] == []
    assert resultado["campos_invalidos"] == []
    assert resultado["score_completude"] == 1.0


def test_parecer_incompleto_lista_campos():
    resultado = validar_completude(_carregar("example_parecer_incompleto.json"))
    assert resultado["status"] == "ok"
    assert resultado["completo"] is False
    # significancia está ausente
    assert "significancia" in resultado["campos_faltando"]
    # clareza (nota 5 fora de 1-4) e confianca (justificativa vazia) são inválidos
    campos_invalidos = {item["campo"] for item in resultado["campos_invalidos"]}
    assert "clareza" in campos_invalidos
    assert "confianca" in campos_invalidos
    # 4 de 7 campos válidos
    assert resultado["score_completude"] == round(4 / 7, 4)


def test_confianca_usa_escala_1_a_3():
    parecer = _carregar("example_valid_output.json")
    parecer["confianca"]["nota"] = 4  # válido em 1-4, mas inválido para confiança (1-3)
    resultado = validar_completude(parecer)
    assert resultado["completo"] is False
    campos_invalidos = {item["campo"] for item in resultado["campos_invalidos"]}
    assert "confianca" in campos_invalidos


def test_entrada_nao_dict_retorna_erro():
    resultado = validar_completude(["não", "é", "dict"])
    assert resultado["status"] == "erro"
    assert resultado["completo"] is False
    assert resultado["score_completude"] == 0.0


def test_nota_booleana_nao_conta_como_inteiro():
    parecer = _carregar("example_valid_output.json")
    parecer["solidez_tecnica"]["nota"] = True  # bool não deve passar como int 1-4
    resultado = validar_completude(parecer)
    campos_invalidos = {item["campo"] for item in resultado["campos_invalidos"]}
    assert "solidez_tecnica" in campos_invalidos


# ---------------------------------------------------------------------------
# Modo veredito (EditorVerdictSchema)
# ---------------------------------------------------------------------------

def test_veredito_oficial_valido_eh_completo():
    resultado = validar_completude(_carregar("example_editor_verdict_output.json"))
    assert resultado["status"] == "ok"
    assert resultado["tipo"] == "veredito"  # detecção automática via campo 'decisao'
    assert resultado["completo"] is True
    assert resultado["score_completude"] == 1.0


def test_veredito_incompleto_lista_campos():
    resultado = validar_completude(_carregar("example_veredito_incompleto.json"))
    assert resultado["tipo"] == "veredito"
    assert resultado["completo"] is False
    campos_invalidos = {item["campo"] for item in resultado["campos_invalidos"]}
    # sintese vazia, notas_por_revisor vazio e critica com 'tipo' inválido
    assert "sintese" in campos_invalidos
    assert "notas_por_revisor" in campos_invalidos
    assert "criticas" in campos_invalidos


def test_deteccao_auto_distingue_parecer_de_veredito():
    assert validar_completude(_carregar("example_valid_output.json"))["tipo"] == "parecer"
    assert validar_completude(_carregar("example_editor_verdict_output.json"))["tipo"] == "veredito"


def test_tipo_forcado_sobrepoe_heuristica():
    # Forçar 'parecer' num veredito faz faltarem os campos do parecer.
    resultado = validar_completude(
        _carregar("example_editor_verdict_output.json"), tipo="parecer"
    )
    assert resultado["tipo"] == "parecer"
    assert "revisor" in resultado["campos_faltando"]


def test_criticas_e_recomendacoes_sao_opcionais():
    # Veredito sem 'criticas' nem 'recomendacoes_aos_autores' ainda é completo.
    veredito = _carregar("example_editor_verdict_output.json")
    veredito.pop("criticas", None)
    veredito.pop("recomendacoes_aos_autores", None)
    resultado = validar_completude(veredito)
    assert resultado["completo"] is True
