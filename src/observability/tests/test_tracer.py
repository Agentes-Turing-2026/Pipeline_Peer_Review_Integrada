"""Testes determinísticos da base de observabilidade (Grupo 3).

Todos usam ``MemoryExporter`` (guarda os eventos em memória): rodam offline, sem
GOOGLE_API_KEY e sem tocar o disco. 
"""

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from observability import (  # noqa: E402
    EventType,
    MemoryExporter,
    Status,
    Tracer,
    trace_adk_event,
)

FASES = [
    "fase_1_revisao_independente",
    "fase_2_leitura_cruzada",
    "fase_3_editor_chefe",
    "fase_4_relatorio_final",
]


# ---------------------------------------------------------------------------
# Item 4 — envelope da execução e hierarquia
# ---------------------------------------------------------------------------

def test_envelope_run_start_quatro_spans_run_end():
    """A execução abre, roda as 4 fases (cada uma um span aberto/fechado) e fecha."""
    exp = MemoryExporter()
    tracer = Tracer(exporter=exp, run_id="run_test")

    with tracer.run(name="peer_review"):
        for nome in FASES:
            with tracer.span(nome, kind="phase", phase=nome):
                pass

    tipos = [e.event_type for e in exp.events]
    assert tipos == [
        EventType.RUN_START.value,
        EventType.SPAN_START.value, EventType.SPAN_END.value,  # fase_1
        EventType.SPAN_START.value, EventType.SPAN_END.value,  # fase_2
        EventType.SPAN_START.value, EventType.SPAN_END.value,  # fase_3
        EventType.SPAN_START.value, EventType.SPAN_END.value,  # fase_4
        EventType.RUN_END.value,
    ]


def test_fases_saem_em_ordem_e_com_os_nomes_oficiais():
    exp = MemoryExporter()
    tracer = Tracer(exporter=exp, run_id="run_test")

    with tracer.run():
        for nome in FASES:
            with tracer.span(nome, kind="phase", phase=nome):
                pass

    abertos = [e.name for e in exp.events if e.event_type == EventType.SPAN_START.value]
    assert abertos == FASES


def test_toda_fase_e_filha_do_run():
    """Hierarquia real: o span de cada fase aponta para o span raiz via parent_span_id."""
    exp = MemoryExporter()
    tracer = Tracer(exporter=exp, run_id="run_test")

    with tracer.run():
        for nome in FASES:
            with tracer.span(nome, kind="phase", phase=nome):
                pass

    starts = [e for e in exp.events if e.event_type == EventType.SPAN_START.value]
    assert all(e.parent_span_id == tracer.root_span_id for e in starts)


def test_run_end_carrega_status_ok_e_duracao():
    exp = MemoryExporter()
    tracer = Tracer(exporter=exp, run_id="run_test")

    with tracer.run():
        with tracer.span(FASES[0], kind="phase"):
            pass

    fim = exp.events[-1]
    assert fim.event_type == EventType.RUN_END.value
    assert fim.status == Status.OK.value
    assert fim.duration_s is not None


def test_todo_span_aberto_e_fechado():
    """Nenhum span fica pendurado: cada span_id aberto tem seu span_end."""
    exp = MemoryExporter()
    tracer = Tracer(exporter=exp, run_id="run_test")

    with tracer.run():
        for nome in FASES:
            with tracer.span(nome, kind="phase"):
                pass

    abertos = {e.span_id for e in exp.events if e.event_type == EventType.SPAN_START.value}
    fechados = {e.span_id for e in exp.events if e.event_type == EventType.SPAN_END.value}
    assert abertos == fechados


# ---------------------------------------------------------------------------
# Item 5 — caminho de erro (requisito 5)
# ---------------------------------------------------------------------------

def test_erro_em_span_registra_evento_e_relevanta():
    """Exceção numa fase: vira evento 'error' com status 'erro' e é re-levantada."""
    exp = MemoryExporter()
    tracer = Tracer(exporter=exp, run_id="run_test")

    with pytest.raises(ValueError):
        with tracer.run():
            with tracer.span("fase_1_revisao_independente", kind="phase"):
                raise ValueError("boom")

    erros = [e for e in exp.events if e.event_type == EventType.ERROR.value]
    assert erros, "deveria ter emitido ao menos um evento de erro"
    assert all(e.status == Status.ERROR.value for e in erros)
    assert any("boom" in (e.attributes or {}).get("erro", "") for e in erros)


def test_erro_marca_span_e_run_como_erro_no_fechamento():
    """O status 'erro' propaga: o span da fase e o run fecham com status 'erro'."""
    exp = MemoryExporter()
    tracer = Tracer(exporter=exp, run_id="run_test")

    with pytest.raises(ValueError):
        with tracer.run():
            with tracer.span("fase_1_revisao_independente", kind="phase"):
                raise ValueError("boom")

    span_end = next(e for e in exp.events if e.event_type == EventType.SPAN_END.value)
    run_end = next(e for e in exp.events if e.event_type == EventType.RUN_END.value)
    assert span_end.status == Status.ERROR.value
    assert run_end.status == Status.ERROR.value


def test_falha_no_exporter_nao_derruba_o_pipeline():
    """Decisão de projeto: se a observabilidade falhar, o fluxo continua."""

    class ExporterQuebrado(MemoryExporter):
        def export(self, event):  # noqa: D401 - sempre falha
            raise RuntimeError("exporter caiu")

    tracer = Tracer(exporter=ExporterQuebrado(), run_id="run_test")

    # Não deve levantar, apesar de todo export falhar internamente.
    with tracer.run():
        with tracer.span("fase_1_revisao_independente", kind="phase"):
            pass


# ---------------------------------------------------------------------------
# Item 1 — captura de eventos do ADK (requisito 6), sem API
# ---------------------------------------------------------------------------

class _FakeFunctionCall:
    def __init__(self, name: str):
        self.name = name


class _FakePart:
    def __init__(self, text: str):
        self.text = text


class _FakeContent:
    def __init__(self, text: str):
        self.parts = [_FakePart(text)]


class _FakeAdkEvent:
    """Imita o ``Event`` do Runner do ADK, sem depender do pacote nem de rede."""

    def __init__(self, author="statistician", invocation_id="inv_123",
                 tools=None, text="parecer gerado", final=True):
        self.author = author
        self.invocation_id = invocation_id
        self.partial = False
        self.content = _FakeContent(text) if text else None
        self._tools = tools or []
        self._final = final

    def is_final_response(self):
        return self._final

    def get_function_calls(self):
        return [_FakeFunctionCall(n) for n in self._tools]


def test_captura_evento_adk_preserva_author_e_invocation_id():
    exp = MemoryExporter()
    tracer = Tracer(exporter=exp, run_id="run_test")

    with tracer.run():
        with tracer.span("fase_1_revisao_independente", kind="phase",
                         phase="fase_1_revisao_independente"):
            trace_adk_event(_FakeAdkEvent(), phase="fase_1_revisao_independente")

    adk = [e for e in exp.events if e.event_type == EventType.ADK_EVENT.value]
    assert len(adk) == 1
    ev = adk[0]
    assert ev.author == "statistician"
    assert ev.invocation_id == "inv_123"          # vínculo com a invocação do ADK
    assert ev.phase == "fase_1_revisao_independente"
    assert ev.attributes.get("final_response") is True


def test_captura_evento_adk_registra_tool_calls():
    exp = MemoryExporter()
    tracer = Tracer(exporter=exp, run_id="run_test")

    with tracer.run():
        trace_adk_event(
            _FakeAdkEvent(tools=["validar_completude", "checar_coerencia"]),
            phase="fase_1_revisao_independente",
        )

    adk = next(e for e in exp.events if e.event_type == EventType.ADK_EVENT.value)
    assert adk.attributes.get("tool_calls") == ["validar_completude", "checar_coerencia"]


def test_captura_evento_adk_ancora_no_run_id_atual():
    """O evento capturado pertence à MESMA execução (run_id) em curso."""
    exp = MemoryExporter()
    tracer = Tracer(exporter=exp, run_id="run_test")

    with tracer.run():
        trace_adk_event(_FakeAdkEvent(), phase="fase_1_revisao_independente")

    adk = next(e for e in exp.events if e.event_type == EventType.ADK_EVENT.value)
    assert adk.run_id == "run_test"


def test_trace_adk_event_e_noop_fora_de_execucao():
    """Sem execução em curso, capturar um Event do ADK é um no-op (não quebra)."""
    # Nenhum tracer ativo -> get_current_tracer() é None -> não deve levantar.
    trace_adk_event(_FakeAdkEvent(), phase="fase_1_revisao_independente")
