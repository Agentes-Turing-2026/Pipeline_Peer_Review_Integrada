"""Testes determinísticos de ``auditar_decisao_final`` (Tool 3 — Grupo 2).

Autocontidos: usam vereditos mock embutidos + um exemplo versionado. NÃO dependem
da Tool 2 (``checar_coerencia``, João Pedro) — quando ela ainda não está integrada,
``inconsistencias`` vem ``[]`` e os testes continuam válidos. As asserções sobre a
flag ``requer_revisao_humana`` que poderiam depender da coerência semântica usam o
caminho da DIVERGÊNCIA de notas, que é independente da Tool 2.
"""

import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tools.auditar_decisao_final import auditar_decisao_final  # noqa: E402

EXAMPLES = SRC / "examples"


def _carregar(nome: str) -> dict:
    return json.loads((EXAMPLES / nome).read_text(encoding="utf-8"))


def _veredito_coerente() -> dict:
    return {
        "decisao": 3,
        "justificativa": "Convergência para aceitar com ressalvas.",
        "sintese": "Contribuição sólida, validação limitada.",
        "notas_por_revisor": {"statistician": 3, "domain_expert": 3, "copyeditor": 4},
        "criticas": [
            {"revisor": "statistician", "tipo": "fraqueza", "texto": "Amostra pequena."},
            {"revisor": "statistician", "tipo": "critica", "texto": "Sem validação externa."},
        ],
        "recomendacoes_aos_autores": ["Ampliar a amostra."],
    }


def test_veredito_coerente_resume_campos_basicos():
    r = auditar_decisao_final(_veredito_coerente())
    assert r["status"] == "ok"
    assert r["decisao"] == 3
    assert r["decisao_rotulo"] == "Aceitar com ressalvas"
    assert r["media_notas"] == round((3 + 3 + 4) / 3, 4)
    assert r["divergencia_notas"] == 1
    assert r["criticas_por_tipo"] == {"fraqueza": 1, "critica": 1}
    assert isinstance(r["inconsistencias"], list)
    assert isinstance(r["requer_revisao_humana"], bool)
    assert isinstance(r["resumo_auditoria"], str) and r["resumo_auditoria"]


def test_divergencia_alta_exige_revisao_humana():
    veredito = _veredito_coerente()
    veredito["notas_por_revisor"] = {"a": 1, "b": 4}  # divergência 3 (>= 2)
    r = auditar_decisao_final(veredito)
    assert r["divergencia_notas"] == 3
    assert r["requer_revisao_humana"] is True


def test_divergencia_baixa_sem_inconsistencias_nao_exige_humano():
    # Notas idênticas → divergência 0; sem Tool 2, inconsistencias=[] → flag False.
    veredito = _veredito_coerente()
    veredito["notas_por_revisor"] = {"a": 3, "b": 3, "c": 3}
    r = auditar_decisao_final(veredito)
    assert r["divergencia_notas"] == 0
    if not r["inconsistencias"]:  # robusto caso a Tool 2 já esteja integrada
        assert r["requer_revisao_humana"] is False


def test_preserva_veredito_bruto():
    veredito = _veredito_coerente()
    r = auditar_decisao_final(veredito)
    assert r["veredito"] == veredito


def test_decisao_ausente_vira_rotulo_desconhecido():
    veredito = _veredito_coerente()
    veredito.pop("decisao")
    r = auditar_decisao_final(veredito)
    assert r["decisao"] is None
    assert r["decisao_rotulo"] == "desconhecida"


def test_notas_vazias_nao_quebram():
    veredito = _veredito_coerente()
    veredito["notas_por_revisor"] = {}
    r = auditar_decisao_final(veredito)
    assert r["status"] == "ok"
    assert r["media_notas"] == 0.0
    assert r["divergencia_notas"] == 0


def test_nota_booleana_e_fora_da_faixa_sao_ignoradas():
    veredito = _veredito_coerente()
    veredito["notas_por_revisor"] = {"a": True, "b": 9, "c": 3}  # só 3 é válida
    r = auditar_decisao_final(veredito)
    assert r["media_notas"] == 3.0
    assert r["divergencia_notas"] == 0


def test_entrada_nao_dict_retorna_erro():
    r = auditar_decisao_final(["não", "é", "dict"])
    assert r["status"] == "erro"
    assert r["requer_revisao_humana"] is True  # na dúvida, vai para humano
    assert r["inconsistencias"] == []


def test_funciona_sobre_exemplo_versionado_do_editor():
    r = auditar_decisao_final(_carregar("example_editor_verdict_output.json"))
    assert r["status"] == "ok"
    assert r["decisao"] == 3
    assert r["decisao_rotulo"] == "Aceitar com ressalvas"
    assert r["divergencia_notas"] == 1
    assert r["criticas_por_tipo"] == {"fraqueza": 2, "critica": 1}