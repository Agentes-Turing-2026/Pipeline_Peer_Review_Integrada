"""Testes determinísticos de ``gerar_resumo`` (Grupo 2 — métricas)."""

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from metrics.eventos import ExecutionEvent  # noqa: E402
from metrics.resumo import gerar_resumo  # noqa: E402


def _evento(**overrides) -> ExecutionEvent:
    base = dict(
        run_id="run-x",
        fase="fase_1_revisao_independente",
        tipo="fase",
        nome="fase_1_revisao_independente",
        status="sucesso",
    )
    base.update(overrides)
    return ExecutionEvent(**base)


def test_lista_vazia_com_run_id_explicito_gera_resumo_zerado():
    resumo = gerar_resumo([], run_id="run-vazio")
    assert resumo.run_id == "run-vazio"
    assert resumo.duracao_total_s is None
    assert resumo.duracao_soma_fases_s is None
    assert resumo.duracao_por_fase_s == {}
    assert resumo.quantidade_tools_chamadas == {}
    assert resumo.quantidade_validacoes == 0
    assert resumo.quantidade_retries == 0
    assert resumo.quantidade_falhas == 0
    assert resumo.requer_revisao_humana is False
    assert resumo.decisao_final is None
    assert resumo.status_final == "sucesso"
    assert resumo.alertas == []


def test_lista_vazia_sem_run_id_levanta_value_error():
    with pytest.raises(ValueError):
        gerar_resumo([])


def test_duracao_por_fase_e_soma_fases():
    eventos = [
        _evento(tipo="fase", nome="fase_1", fase="fase_1", duracao_s=0.1),
        _evento(tipo="fase", nome="fase_2", fase="fase_2", duracao_s=0.2),
    ]
    resumo = gerar_resumo(eventos)
    assert resumo.duracao_por_fase_s == {"fase_1": 0.1, "fase_2": 0.2}
    assert resumo.duracao_soma_fases_s == pytest.approx(0.3)


def test_duracao_total_s_explicito_e_respeitado_sem_sobrescrita_pela_soma():
    eventos = [
        _evento(tipo="fase", nome="fase_1", fase="fase_1", duracao_s=0.1),
        _evento(tipo="fase", nome="fase_2", fase="fase_2", duracao_s=0.2),
    ]
    resumo = gerar_resumo(eventos, duracao_total_s=0.5)
    assert resumo.duracao_total_s == 0.5
    assert resumo.duracao_soma_fases_s == pytest.approx(0.3)
    assert resumo.alertas == []


def test_sem_parametro_total_vira_soma_das_fases_e_gera_alerta_de_aproximacao():
    eventos = [
        _evento(tipo="fase", nome="fase_1", fase="fase_1", duracao_s=0.1),
        _evento(tipo="fase", nome="fase_2", fase="fase_2", duracao_s=0.2),
    ]
    resumo = gerar_resumo(eventos)
    assert resumo.duracao_soma_fases_s == pytest.approx(0.3)
    assert resumo.duracao_total_s == pytest.approx(resumo.duracao_soma_fases_s)
    assert len(resumo.alertas) == 1
    assert "aproximad" in resumo.alertas[0].lower()


def test_duracao_total_s_de_parede_maior_que_soma_das_fases_e_preservada_sem_correcao():
    eventos = [
        _evento(tipo="fase", nome="fase_1", fase="fase_1", duracao_s=0.1),
        _evento(tipo="fase", nome="fase_2", fase="fase_2", duracao_s=0.2),
    ]
    resumo = gerar_resumo(eventos, duracao_total_s=5.0)
    assert resumo.duracao_total_s == 5.0
    assert resumo.duracao_soma_fases_s == pytest.approx(0.3)
    assert resumo.duracao_total_s > resumo.duracao_soma_fases_s
    assert resumo.alertas == []


def test_duracao_total_s_zero_e_preservada_sem_cair_para_soma_das_fases():
    eventos = [
        _evento(tipo="fase", nome="fase_1", fase="fase_1", duracao_s=0.1),
        _evento(tipo="fase", nome="fase_2", fase="fase_2", duracao_s=0.2),
    ]
    resumo = gerar_resumo(eventos, duracao_total_s=0.0)
    assert resumo.duracao_total_s == 0.0
    assert resumo.duracao_soma_fases_s == pytest.approx(0.3)
    assert resumo.alertas == []


def test_evento_de_fase_sem_duracao_none_nao_entra_na_soma_como_zero():
    eventos = [
        _evento(tipo="fase", nome="fase_1", fase="fase_1", duracao_s=0.4),
        _evento(tipo="fase", nome="fase_2", fase="fase_2"),
    ]
    resumo = gerar_resumo(eventos)
    assert resumo.duracao_por_fase_s == {"fase_1": 0.4}
    assert resumo.duracao_soma_fases_s == pytest.approx(0.4)


def test_contagem_de_tools_chamadas():
    eventos = [
        _evento(tipo="tool", nome="validar_completude"),
        _evento(tipo="tool", nome="validar_completude"),
        _evento(tipo="tool", nome="auditar_decisao_final"),
    ]
    resumo = gerar_resumo(eventos)
    assert resumo.quantidade_tools_chamadas == {
        "validar_completude": 2,
        "auditar_decisao_final": 1,
    }


def test_contagem_de_validacoes_retries_falhas():
    eventos = [
        _evento(tipo="validacao", nome="v1"),
        _evento(tipo="validacao", nome="v2"),
        _evento(tipo="validacao", nome="v3"),
        _evento(tipo="retry", nome="r1"),
        _evento(tipo="retry", nome="r2"),
        _evento(tipo="falha", nome="f1", status="falha"),
        _evento(tipo="tool", nome="outra_tool"),
    ]
    resumo = gerar_resumo(eventos)
    assert resumo.quantidade_validacoes == 3
    assert resumo.quantidade_retries == 2
    assert resumo.quantidade_falhas == 1


def test_requer_revisao_humana_true_quando_algum_evento_sinaliza():
    eventos = [
        _evento(tipo="tool", nome="t1"),
        _evento(tipo="validacao", nome="v1", detalhes={"requer_revisao_humana": True}),
        _evento(tipo="retry", nome="r1"),
    ]
    resumo = gerar_resumo(eventos)
    assert resumo.requer_revisao_humana is True


def test_requer_revisao_humana_false_quando_nenhum_evento_sinaliza():
    eventos = [
        _evento(tipo="tool", nome="t1"),
        _evento(tipo="validacao", nome="v1"),
    ]
    resumo = gerar_resumo(eventos)
    assert resumo.requer_revisao_humana is False


def test_decisao_final_extraida_de_evento_tipo_decisao_final():
    eventos = [
        _evento(tipo="decisao_final", nome="veredito", detalhes={"decisao": 4}),
    ]
    resumo = gerar_resumo(eventos)
    assert resumo.decisao_final == 4


def test_status_final_sucesso_sem_avisos_ou_falhas():
    eventos = [
        _evento(status="sucesso"),
        _evento(status="sucesso", tipo="tool", nome="t1"),
    ]
    resumo = gerar_resumo(eventos)
    assert resumo.status_final == "sucesso"
    assert resumo.alertas == []


def test_status_final_sucesso_com_alertas_quando_ha_aviso_sem_falha():
    eventos = [
        _evento(status="sucesso"),
        _evento(status="aviso", tipo="tool", nome="t1", fase="fase_2_leitura_cruzada"),
    ]
    resumo = gerar_resumo(eventos)
    assert resumo.status_final == "sucesso_com_alertas"
    assert len(resumo.alertas) == 1
    assert "fase_2_leitura_cruzada" in resumo.alertas[0]


def test_status_final_falha_tem_prioridade_sobre_aviso():
    eventos = [
        _evento(status="aviso", tipo="tool", nome="t1", fase="fase_2_leitura_cruzada"),
        _evento(status="falha", tipo="falha", nome="f1", fase="fase_3_editor_chefe"),
    ]
    resumo = gerar_resumo(eventos)
    assert resumo.status_final == "falha"


def test_to_dict_tem_exatamente_as_dezenove_chaves_esperadas():
    resumo = gerar_resumo([], run_id="run-dict")
    dado = resumo.to_dict()
    assert set(dado.keys()) == {
        "run_id", "duracao_total_s", "duracao_soma_fases_s", "duracao_por_fase_s",
        "quantidade_validacoes", "quantidade_retries", "quantidade_falhas",
        "quantidade_tools_chamadas", "requer_revisao_humana", "decisao_final",
        "status_final", "alertas", "tokens_totais", "custo_estimado",
        "modelo_usado", "chamadas_llm", "tokens_execucao", "tokens_por_agente",
        "tokens_por_fase",
    }
    assert len(dado) == 19
    # Sem chamadas LLM registradas, tokens ficam indisponíveis — nunca 0.
    assert dado["tokens_totais"] is None
    assert dado["custo_estimado"] is None
    assert dado["modelo_usado"] is None
    assert dado["chamadas_llm"] is None
    assert dado["tokens_execucao"] is None
    assert dado["tokens_por_agente"] == {}
    assert dado["tokens_por_fase"] == {}
