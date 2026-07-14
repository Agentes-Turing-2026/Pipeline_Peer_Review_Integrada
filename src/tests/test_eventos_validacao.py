"""Testes de ``eventos_validacao.py`` (Grupo 1 — eventos estruturados)."""

import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import eventos_validacao as ev  # noqa: E402
from review_schema import validar_review  # noqa: E402
from validacao_retry import PipelineValidationError, validar_com_tentativas  # noqa: E402

EXAMPLES = SRC / "examples"


def _carregar(nome: str) -> dict:
    return json.loads((EXAMPLES / nome).read_text(encoding="utf-8"))


class _ModoMock:
    value = "mock"


MODO_MOCK = _ModoMock()


# ---------------------------------------------------------------------------
# diff_correcao
# ---------------------------------------------------------------------------


def test_diff_correcao_detecta_campo_de_primeiro_nivel_alterado():
    antes = {"nota": 5}
    depois = {"nota": 4}
    diff = ev.diff_correcao(antes, depois)
    assert diff == {"nota": {"antes": 5, "depois": 4}}


def test_diff_correcao_detecta_campo_aninhado():
    antes = {"confianca": {"nota": 0, "justificativa": "x"}}
    depois = {"confianca": {"nota": 1, "justificativa": "x"}}
    diff = ev.diff_correcao(antes, depois)
    assert diff == {"confianca.nota": {"antes": 0, "depois": 1}}


def test_diff_correcao_sem_mudancas_retorna_vazio():
    dados = {"a": 1, "b": {"c": 2}}
    assert ev.diff_correcao(dados, dict(dados)) == {}


# ---------------------------------------------------------------------------
# EventoValidacao
# ---------------------------------------------------------------------------


def test_evento_categoria_invalida_levanta_erro():
    try:
        ev.EventoValidacao(
            run_id="r1", agente="a", schema="s", tentativa=1, max_tentativas=3,
            categoria="categoria_que_nao_existe", status="sucesso",
        )
        assert False, "deveria ter levantado ValueError"
    except ValueError:
        pass


def test_evento_categoria_valida_ok():
    evento = ev.EventoValidacao(
        run_id="r1", agente="a", schema="s", tentativa=1, max_tentativas=3,
        categoria=ev.CATEGORIA_PASSOU_DE_PRIMEIRA, status="sucesso",
    )
    assert evento.to_dict()["categoria"] == "passou_de_primeira"
    assert "timestamp" in evento.to_dict()


# ---------------------------------------------------------------------------
# emitir_evento / ler_eventos (usam um arquivo temporário via monkeypatch)
# ---------------------------------------------------------------------------


def test_emitir_evento_grava_jsonl_valido(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "EVENTOS_DIR", tmp_path)
    monkeypatch.setattr(ev, "EVENTOS_PATH", tmp_path / "validacao_events.jsonl")

    evento = ev.EventoValidacao(
        run_id="run-teste", agente="statistician", schema="validar_review",
        tentativa=1, max_tentativas=3, categoria=ev.CATEGORIA_PASSOU_DE_PRIMEIRA,
        status="sucesso",
    )
    ev.emitir_evento(evento)

    linhas = (tmp_path / "validacao_events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(linhas) == 1
    registrado = json.loads(linhas[0])
    assert registrado["run_id"] == "run-teste"
    assert registrado["categoria"] == "passou_de_primeira"


def test_ler_eventos_filtra_por_run_id(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "EVENTOS_DIR", tmp_path)
    monkeypatch.setattr(ev, "EVENTOS_PATH", tmp_path / "validacao_events.jsonl")

    for run_id in ("run-a", "run-a", "run-b"):
        ev.emitir_evento(ev.EventoValidacao(
            run_id=run_id, agente="x", schema="validar_review", tentativa=1,
            max_tentativas=3, categoria=ev.CATEGORIA_PASSOU_DE_PRIMEIRA, status="sucesso",
        ))

    assert len(ev.ler_eventos()) == 3
    assert len(ev.ler_eventos(run_id="run-a")) == 2
    assert len(ev.ler_eventos(run_id="run-b")) == 1


def test_ler_eventos_sem_arquivo_retorna_lista_vazia(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "EVENTOS_DIR", tmp_path)
    monkeypatch.setattr(ev, "EVENTOS_PATH", tmp_path / "nao_existe.jsonl")
    assert ev.ler_eventos() == []


def test_formatar_linha_do_tempo_inclui_categoria_e_alerta_revisao_humana():
    eventos = [{
        "run_id": "run-teste", "fase": "fase_1", "agente": "copyeditor",
        "schema": "validar_review", "tentativa": 3, "max_tentativas": 3,
        "categoria": "bloqueado", "status": "bloqueado", "erro": "campo ausente",
        "correcao_aplicada": None, "requer_revisao_humana": True,
        "timestamp": "2026-01-01T00:00:00+00:00",
    }]
    texto = ev.formatar_linha_do_tempo(eventos)
    assert "BLOQUEADO" in texto
    assert "revisão humana" in texto


# ---------------------------------------------------------------------------
# Integração: validar_com_tentativas emite a sequência de eventos esperada
# ---------------------------------------------------------------------------


def test_validar_com_tentativas_emite_evento_de_sucesso_de_primeira(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "EVENTOS_DIR", tmp_path)
    monkeypatch.setattr(ev, "EVENTOS_PATH", tmp_path / "validacao_events.jsonl")

    run_id = "run-sucesso"
    validar_com_tentativas(
        _carregar("example_valid_output.json"), validar_review, MODO_MOCK,
        "statistician", run_id=run_id, fase="fase_1_revisao_independente",
    )

    eventos = ev.ler_eventos(run_id=run_id)
    assert len(eventos) == 1
    assert eventos[0]["categoria"] == "passou_de_primeira"
    assert eventos[0]["fase"] == "fase_1_revisao_independente"


def test_validar_com_tentativas_emite_sequencia_de_retry_ate_corrigir(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "EVENTOS_DIR", tmp_path)
    monkeypatch.setattr(ev, "EVENTOS_PATH", tmp_path / "validacao_events.jsonl")

    run_id = "run-retry"
    validar_com_tentativas(
        _carregar("example_invalid_output.json"), validar_review, MODO_MOCK,
        "domain_expert", run_id=run_id,
    )

    categorias = [e["categoria"] for e in ev.ler_eventos(run_id=run_id)]
    # esperado: falhou (tentativa 1), corrigido, passou_apos_correcao
    assert categorias == ["falhou_recuperavel", "corrigido", "passou_apos_correcao"]


def test_validar_com_tentativas_emite_evento_bloqueado_no_esgotamento(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "EVENTOS_DIR", tmp_path)
    monkeypatch.setattr(ev, "EVENTOS_PATH", tmp_path / "validacao_events.jsonl")

    run_id = "run-bloqueio"
    dados_patologicos = {"revisor": "copyeditor"}
    try:
        validar_com_tentativas(
            dados_patologicos, validar_review, MODO_MOCK, "copyeditor", run_id=run_id,
        )
        assert False, "deveria ter levantado PipelineValidationError"
    except PipelineValidationError:
        pass

    eventos = ev.ler_eventos(run_id=run_id)
    assert eventos[-1]["categoria"] == "bloqueado"
    assert eventos[-1]["requer_revisao_humana"] is True
