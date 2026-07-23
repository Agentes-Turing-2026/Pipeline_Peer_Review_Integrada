"""Testes determinísticos de ``ExecutionCollector`` (Grupo 2 — métricas)."""

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from metrics.coletor import ExecutionCollector  # noqa: E402
from metrics.eventos import ExecutionEvent  # noqa: E402


def test_run_id_automatico_gera_ids_diferentes_e_nao_vazios():
    coletor_a = ExecutionCollector()
    coletor_b = ExecutionCollector()
    assert isinstance(coletor_a.run_id, str) and coletor_a.run_id != ""
    assert isinstance(coletor_b.run_id, str) and coletor_b.run_id != ""
    assert coletor_a.run_id != coletor_b.run_id


def test_run_id_explicito_e_mantido():
    coletor = ExecutionCollector(run_id="abc")
    assert coletor.run_id == "abc"


def test_registrar_adiciona_evento_e_retorna_o_mesmo_objeto_do_ultimo_item():
    coletor = ExecutionCollector(run_id="run-1")
    evento = coletor.registrar(
        fase="fase_1_revisao_independente", tipo="tool", nome="completude",
        status="sucesso",
    )
    assert isinstance(evento, ExecutionEvent)
    assert coletor.eventos[-1] == evento
    assert coletor.eventos[-1].run_id == evento.run_id
    assert coletor.eventos[-1].fase == evento.fase
    assert coletor.eventos[-1].tipo == evento.tipo
    assert coletor.eventos[-1].nome == evento.nome
    assert coletor.eventos[-1].status == evento.status
    assert coletor.eventos[-1].timestamp == evento.timestamp
    assert coletor.eventos[-1].duracao_s == evento.duracao_s
    assert coletor.eventos[-1].detalhes == evento.detalhes


def test_eventos_e_uma_copia_nao_vaza_referencia_da_lista_interna():
    coletor = ExecutionCollector(run_id="run-2")
    coletor.registrar(fase="fase_1", tipo="tool", nome="x", status="sucesso")
    lista_obtida = coletor.eventos
    tamanho_original = len(coletor.eventos)
    lista_obtida.append("item_espurio")
    assert len(coletor.eventos) == tamanho_original


def test_eventos_como_dict_devolve_dicts_na_mesma_quantidade():
    coletor = ExecutionCollector(run_id="run-3")
    coletor.registrar(fase="fase_1", tipo="tool", nome="x", status="sucesso")
    coletor.registrar(fase="fase_1", tipo="validacao", nome="y", status="falha")
    dados = coletor.eventos_como_dict()
    assert len(dados) == len(coletor.eventos)
    for item in dados:
        assert isinstance(item, dict)
        assert not isinstance(item, ExecutionEvent)


def test_fase_sucesso_registra_um_evento_com_duracao():
    coletor = ExecutionCollector(run_id="run-4")
    with coletor.fase("fase_1_revisao_independente"):
        pass
    assert len(coletor.eventos) == 1
    evento = coletor.eventos[0]
    assert evento.tipo == "fase"
    assert evento.status == "sucesso"
    assert evento.duracao_s is not None
    assert evento.duracao_s >= 0


def test_fase_falha_relevanta_excecao_e_registra_evento_de_falha():
    coletor = ExecutionCollector(run_id="run-5")
    with pytest.raises(ValueError, match="algo quebrou"):
        with coletor.fase("fase_1_revisao_independente"):
            raise ValueError("algo quebrou")
    assert len(coletor.eventos) == 1
    evento = coletor.eventos[0]
    assert evento.tipo == "fase"
    assert evento.status == "falha"
    assert evento.detalhes["erro"] == "algo quebrou"
    assert evento.detalhes["erro_tipo"] == "ValueError"


def test_tool_sucesso_registra_um_evento_com_fase_e_duracao():
    coletor = ExecutionCollector(run_id="run-6")
    with coletor.tool("validar_completude", fase="fase_1_revisao_independente"):
        pass
    assert len(coletor.eventos) == 1
    evento = coletor.eventos[0]
    assert evento.tipo == "tool"
    assert evento.nome == "validar_completude"
    assert evento.fase == "fase_1_revisao_independente"
    assert evento.status == "sucesso"
    assert evento.duracao_s is not None
    assert evento.duracao_s >= 0


def test_tool_falha_relevanta_excecao_e_registra_evento_de_falha_com_fase():
    coletor = ExecutionCollector(run_id="run-7")
    with pytest.raises(RuntimeError, match="tool quebrou"):
        with coletor.tool("auditar_decisao_final", fase="fase_3_editor_chefe"):
            raise RuntimeError("tool quebrou")
    assert len(coletor.eventos) == 1
    evento = coletor.eventos[0]
    assert evento.tipo == "tool"
    assert evento.nome == "auditar_decisao_final"
    assert evento.fase == "fase_3_editor_chefe"
    assert evento.status == "falha"
    assert evento.detalhes["erro"] == "tool quebrou"
    assert evento.detalhes["erro_tipo"] == "RuntimeError"


def test_duracao_execucao_s_usa_perf_counter_desde_a_criacao(monkeypatch):
    import metrics.coletor as coletor_mod

    valores = iter([100.0, 100.0, 107.5])
    monkeypatch.setattr(coletor_mod.time, "perf_counter", lambda: next(valores))

    coletor = ExecutionCollector(run_id="run-duracao")  # consome 100.0 no __init__
    assert coletor.duracao_execucao_s == 0.0
    assert coletor.duracao_execucao_s == 7.5


def test_multiplas_fases_e_tools_ficam_na_ordem_de_registro():
    coletor = ExecutionCollector(run_id="run-8")
    with coletor.fase("fase_1_revisao_independente"):
        pass
    with coletor.tool("validar_completude", fase="fase_1_revisao_independente"):
        pass
    with coletor.fase("fase_2_leitura_cruzada"):
        pass
    assert len(coletor.eventos) == 3
    tipos_e_nomes = [(e.tipo, e.nome) for e in coletor.eventos]
    assert tipos_e_nomes == [
        ("fase", "fase_1_revisao_independente"),
        ("tool", "validar_completude"),
        ("fase", "fase_2_leitura_cruzada"),
    ]


# ---------------------------------------------------------------------------
# Prevenção de contagem duplicada (chave_dedup)
# ---------------------------------------------------------------------------

def test_chave_dedup_ignora_o_mesmo_evento_registrado_duas_vezes():
    coletor = ExecutionCollector(run_id="run-dedup")
    primeiro = coletor.registrar(
        fase="fase_1", tipo="tool", nome="chamada_llm", status="sucesso",
        chave_dedup="evento-adk-123",
    )
    repetido = coletor.registrar(
        fase="fase_1", tipo="tool", nome="chamada_llm", status="sucesso",
        chave_dedup="evento-adk-123",
    )
    assert isinstance(primeiro, ExecutionEvent)
    assert repetido is None
    assert len(coletor.eventos) == 1


def test_chaves_dedup_diferentes_registram_todos_os_eventos():
    coletor = ExecutionCollector(run_id="run-dedup")
    coletor.registrar(
        fase="fase_1", tipo="tool", nome="chamada_llm", status="sucesso",
        chave_dedup="inv-1:reviewer_a",
    )
    coletor.registrar(
        fase="fase_1", tipo="tool", nome="chamada_llm", status="sucesso",
        chave_dedup="inv-1:reviewer_b",
    )
    assert len(coletor.eventos) == 2


def test_sem_chave_dedup_eventos_identicos_continuam_entrando():
    """Comportamento pré-existente preservado: sem chave, todo registro entra."""
    coletor = ExecutionCollector(run_id="run-dedup")
    for _ in range(2):
        coletor.registrar(fase="fase_1", tipo="validacao", nome="reviewer_a", status="sucesso")
    assert len(coletor.eventos) == 2


def test_chave_dedup_nao_vaza_para_os_detalhes_do_evento():
    coletor = ExecutionCollector(run_id="run-dedup")
    evento = coletor.registrar(
        fase="fase_1", tipo="tool", nome="chamada_llm", status="sucesso",
        chave_dedup="evento-adk-123", agente="reviewer_a",
    )
    assert evento is not None
    assert evento.detalhes == {"agente": "reviewer_a"}
