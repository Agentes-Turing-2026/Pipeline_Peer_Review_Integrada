"""Testes determinísticos de ``ExecutionEvent`` (Grupo 2 — métricas)."""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from metrics.eventos import ExecutionEvent  # noqa: E402


def _evento_basico() -> ExecutionEvent:
    return ExecutionEvent(
        run_id="run-123",
        fase="fase_1_revisao_independente",
        tipo="tool",
        nome="completude",
        status="sucesso",
    )


def test_criacao_com_campos_obrigatorios_preenche_defaults_sozinha():
    evento = _evento_basico()
    assert evento.run_id == "run-123"
    assert evento.fase == "fase_1_revisao_independente"
    assert evento.tipo == "tool"
    assert evento.nome == "completude"
    assert evento.status == "sucesso"
    assert isinstance(evento.timestamp, str) and evento.timestamp != ""
    assert evento.duracao_ms is None
    assert evento.detalhes == {}
    assert evento.detalhes is not None


def test_to_dict_contem_exatamente_as_oito_chaves_com_valores_certos():
    evento = _evento_basico()
    dado = evento.to_dict()
    assert set(dado.keys()) == {
        "run_id", "fase", "tipo", "nome", "status",
        "timestamp", "duracao_ms", "detalhes",
    }
    assert dado["run_id"] == "run-123"
    assert dado["fase"] == "fase_1_revisao_independente"
    assert dado["tipo"] == "tool"
    assert dado["nome"] == "completude"
    assert dado["status"] == "sucesso"
    assert dado["timestamp"] == evento.timestamp
    assert dado["duracao_ms"] is None
    assert dado["detalhes"] == {}


def test_from_dict_de_to_dict_reconstroi_evento_igual():
    original = ExecutionEvent(
        run_id="run-456",
        fase="fase_3_editor_chefe",
        tipo="decisao_final",
        nome="veredito",
        status="aviso",
        duracao_ms=12.5,
        detalhes={"decisao": 3},
    )
    reconstruido = ExecutionEvent.from_dict(original.to_dict())
    assert reconstruido == original
    assert reconstruido.run_id == original.run_id
    assert reconstruido.fase == original.fase
    assert reconstruido.tipo == original.tipo
    assert reconstruido.nome == original.nome
    assert reconstruido.status == original.status
    assert reconstruido.timestamp == original.timestamp
    assert reconstruido.duracao_ms == original.duracao_ms
    assert reconstruido.detalhes == original.detalhes


def test_duracao_ms_aceita_none_por_padrao():
    evento = _evento_basico()
    assert evento.duracao_ms is None


def test_duracao_ms_aceita_float_quando_fornecido():
    evento = ExecutionEvent(
        run_id="run-789",
        fase="fase_2_leitura_cruzada",
        tipo="retry",
        nome="correcao_aplicada",
        status="falha",
        duracao_ms=87.3,
    )
    assert evento.duracao_ms == 87.3
