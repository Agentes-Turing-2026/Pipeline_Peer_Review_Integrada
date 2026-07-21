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
    assert evento.duracao_s is None
    assert evento.detalhes == {}
    assert evento.detalhes is not None


def test_to_dict_contem_exatamente_as_oito_chaves_com_valores_certos():
    evento = _evento_basico()
    dado = evento.to_dict()
    assert set(dado.keys()) == {
        "run_id", "fase", "tipo", "nome", "status",
        "timestamp", "duracao_s", "detalhes",
    }
    assert dado["run_id"] == "run-123"
    assert dado["fase"] == "fase_1_revisao_independente"
    assert dado["tipo"] == "tool"
    assert dado["nome"] == "completude"
    assert dado["status"] == "sucesso"
    assert dado["timestamp"] == evento.timestamp
    assert dado["duracao_s"] is None
    assert dado["detalhes"] == {}


def test_from_dict_de_to_dict_reconstroi_evento_igual():
    original = ExecutionEvent(
        run_id="run-456",
        fase="fase_3_editor_chefe",
        tipo="decisao_final",
        nome="veredito",
        status="aviso",
        duracao_s=0.0125,
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
    assert reconstruido.duracao_s == original.duracao_s
    assert reconstruido.detalhes == original.detalhes


def test_duracao_s_aceita_none_por_padrao():
    evento = _evento_basico()
    assert evento.duracao_s is None


def test_duracao_s_aceita_float_quando_fornecido():
    evento = ExecutionEvent(
        run_id="run-789",
        fase="fase_2_leitura_cruzada",
        tipo="retry",
        nome="correcao_aplicada",
        status="falha",
        duracao_s=0.0873,
    )
    assert evento.duracao_s == 0.0873


def _payload_legado(**overrides) -> dict:
    base = dict(
        run_id="run-legado",
        fase="fase_1_revisao_independente",
        tipo="tool",
        nome="completude",
        status="sucesso",
    )
    base.update(overrides)
    return base


def test_from_dict_payload_novo_com_duracao_s_preserva_valor_identico():
    evento = ExecutionEvent.from_dict(_payload_legado(duracao_s=1.5))
    assert evento.duracao_s == 1.5


def test_from_dict_payload_legado_com_duracao_ms_converte_para_segundos():
    evento = ExecutionEvent.from_dict(_payload_legado(duracao_ms=1500))
    assert evento.duracao_s == 1.5


def test_from_dict_payload_com_ambas_as_chaves_duracao_s_vence_e_duracao_ms_e_ignorado():
    evento = ExecutionEvent.from_dict(_payload_legado(duracao_s=2.0, duracao_ms=9999))
    assert evento.duracao_s == 2.0


def test_from_dict_payload_sem_nenhuma_das_duas_chaves_resulta_em_duracao_s_none():
    evento = ExecutionEvent.from_dict(_payload_legado())
    assert evento.duracao_s is None


def test_from_dict_payload_com_duracao_ms_none_nao_vira_zero():
    evento = ExecutionEvent.from_dict(_payload_legado(duracao_ms=None))
    assert evento.duracao_s is None


def test_from_dict_round_trip_preserva_valor_float_exato_sem_aritmetica():
    original = ExecutionEvent(**_payload_legado(duracao_s=0.123456789))
    reconstruido = ExecutionEvent.from_dict(original.to_dict())
    assert reconstruido.duracao_s == original.duracao_s


def test_from_dict_de_payload_legado_nao_reintroduz_chave_duracao_ms_no_to_dict():
    evento = ExecutionEvent.from_dict(_payload_legado(duracao_ms=500))
    dado = evento.to_dict()
    assert "duracao_ms" not in dado
    assert dado["duracao_s"] == 0.5
