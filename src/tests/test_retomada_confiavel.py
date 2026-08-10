"""Testes de CONFIABILIDADE da retomada — Grupo 1.

``test_persistencia.py`` já cobre *quais fases são puladas* numa retomada. Estes
testes cobrem a outra metade do problema: *o que sobrevive* a ela. Uma retomada
que pula as fases certas mas perde as métricas, re-extrai o PDF ou regrava um
relatório bom por cima é tão defeituosa quanto uma que re-executa tudo.

Cenário base de todos os testes de ponta a ponta: uma execução real do pipeline
em modo mock (offline, sem chave de API) que FALHA no meio por uma exceção
injetada em uma fase, seguida da retomada pelo mesmo ``run_id``. As asserções
comparam o resultado da retomada com o de uma execução limpa equivalente — o
critério é "a retomada tem que ser indistinguível de uma execução que nunca
falhou", não "a retomada não explodiu".

Isolamento: ``LOG_DIR``, ``OUTPUT_DIR`` e o arquivo de eventos de validação são
redirecionados para ``tmp_path``. Nenhum teste aqui escreve no repositório.
"""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import eventos_validacao as eventos_mod  # noqa: E402
import pipeline as pipeline_mod  # noqa: E402
from extraction import ExtractedDocument, PdfExtractor  # noqa: E402
from metrics.coletor import ExecutionCollector  # noqa: E402
from metrics.eventos import ExecutionEvent  # noqa: E402
from persistencia import CheckpointManager  # noqa: E402
from pipeline import (  # noqa: E402
    EXTRACTION_PHASE,
    CrossReviewPhase,
    EditorVerdictPhase,
    FinalReportPhase,
    IndependentReviewPhase,
    run_demo,
)

FASE_1 = "fase_1_revisao_independente"
FASE_2 = "fase_2_leitura_cruzada"
FASE_3 = "fase_3_editor_chefe"
FASE_4 = "fase_4_relatorio_final"


# ---------------------------------------------------------------------------
# Ambiente isolado
# ---------------------------------------------------------------------------

@pytest.fixture()
def ambiente(tmp_path, monkeypatch):
    """Redireciona TODOS os artefatos da execução para tmp_path."""
    logs = tmp_path / "logs"
    outputs = tmp_path / "outputs"
    logs.mkdir()
    outputs.mkdir()
    monkeypatch.setattr(pipeline_mod, "LOG_DIR", logs)
    monkeypatch.setattr(pipeline_mod, "OUTPUT_DIR", outputs)
    # Os eventos de validação do Grupo 1 têm caminho próprio (não derivam de
    # LOG_DIR): sem redirecioná-los, o teste de preservação de eventos leria o
    # arquivo real do repositório, com o histórico de todas as execuções.
    monkeypatch.setattr(eventos_mod, "EVENTOS_DIR", logs)
    monkeypatch.setattr(eventos_mod, "EVENTOS_PATH", logs / "validacao_events.jsonl")
    return SimpleNamespace(
        logs=logs,
        outputs=outputs,
        checkpoints=logs / "checkpoints",
        traces=logs / "traces",
        eventos=logs / "validacao_events.jsonl",
    )


@contextmanager
def _falha_em(fase_cls, mensagem: str):
    """Substitui ``fase_cls.run`` por uma que sempre falha, e desfaz ao sair.

    Simula uma falha real no meio do pipeline (timeout do modelo, queda de rede,
    resposta irrecuperável). Sair do bloco representa "o problema foi resolvido"
    — é a condição para a retomada fazer sentido.
    """
    def _run(self, data, context):
        raise RuntimeError(mensagem)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fase_cls, "run", _run)
        yield


def _contar_execucoes(mp, contador: dict[str, int]) -> None:
    """Instrumenta as 4 fases para contar quantas vezes cada uma REALMENTE roda."""
    for chave, fase_cls in (
        (FASE_1, IndependentReviewPhase),
        (FASE_2, CrossReviewPhase),
        (FASE_3, EditorVerdictPhase),
        (FASE_4, FinalReportPhase),
    ):
        contador.setdefault(chave, 0)
        original = fase_cls.run

        def _run(self, data, context, _original=original, _chave=chave):
            contador[_chave] += 1
            return _original(self, data, context)

        mp.setattr(fase_cls, "run", _run)


# ---------------------------------------------------------------------------
# 1 — O teste central: a retomada tem que produzir o resumo da execução INTEIRA
# ---------------------------------------------------------------------------

def test_retomada_reproduz_o_resumo_de_uma_execucao_limpa(ambiente):
    """Falha na fase 3 -> retomada -> resumo idêntico ao de uma execução limpa.

    Antes da correção, o resumo da retomada só continha as fases 3 e 4: o
    coletor de métricas nascia vazio a cada chamada e as fases restauradas não
    emitiam evento nenhum.
    """
    limpo = run_demo(mode="mock")
    resumo_limpo = limpo.data["resumo_execucao"]

    run_id = "run_interrompida"
    with _falha_em(EditorVerdictPhase, "timeout simulado do modelo"):
        with pytest.raises(RuntimeError, match="timeout simulado do modelo"):
            run_demo(mode="mock", run_id=run_id)

    retomado = run_demo(mode="mock", run_id=run_id)
    resumo = retomado.data["resumo_execucao"]

    assert sorted(resumo["duracao_por_fase_s"]) == sorted(resumo_limpo["duracao_por_fase_s"]), (
        "o resumo da retomada deve cobrir as MESMAS fases de uma execução limpa"
    )
    assert sorted(resumo["duracao_por_fase_s"]) == [FASE_1, FASE_2, FASE_3, FASE_4]
    assert resumo["quantidade_validacoes"] == resumo_limpo["quantidade_validacoes"]
    assert resumo["quantidade_tools_chamadas"] == resumo_limpo["quantidade_tools_chamadas"]
    assert resumo["decisao_final"] == resumo_limpo["decisao_final"]
    assert resumo["status_final"] == "sucesso", (
        "a falha já resolvida não pode contaminar o status da execução retomada"
    )
    assert resumo["alertas"] == [], "nenhuma lacuna de métrica deve ser detectada"
    # A duração total acumula as duas tentativas; a soma das fases inclui as
    # restauradas. Total < soma seria aritmeticamente impossível.
    assert resumo["duracao_total_s"] >= resumo["duracao_soma_fases_s"]


# ---------------------------------------------------------------------------
# 2 — Somente o que faltava roda de novo
# ---------------------------------------------------------------------------

def test_retomada_executa_somente_o_que_faltava(ambiente):
    """Fases com checkpoint não rodam; o relatório registra o que veio do disco."""
    run_id = "run_parcial"
    with _falha_em(EditorVerdictPhase, "falha simulada na fase 3"):
        with pytest.raises(RuntimeError):
            run_demo(mode="mock", run_id=run_id)

    contador: dict[str, int] = {}
    with pytest.MonkeyPatch.context() as mp:
        _contar_execucoes(mp, contador)
        retomado = run_demo(mode="mock", run_id=run_id)

    assert contador[FASE_1] == 0, "fase 1 tinha checkpoint e não podia rodar de novo"
    assert contador[FASE_2] == 0, "fase 2 tinha checkpoint e não podia rodar de novo"
    assert contador[FASE_3] == 1, "fase 3 era a que faltava"
    assert contador[FASE_4] == 1, "fase 4 nunca chegou a rodar"

    retomada = retomado.data["retomada"]
    assert retomada["fases_restauradas"] == [FASE_1, FASE_2]
    assert retomada["execucoes"] == 2
    assert retomada["eventos_metricas_restaurados"] > 0


# ---------------------------------------------------------------------------
# 3 — O PDF já extraído não é extraído de novo
# ---------------------------------------------------------------------------

TEXTO_EXTRAIDO = (
    "Resumo do artigo submetido para revisao por pares nesta bateria de testes. " * 12
)


class _ExtratorEspiao(PdfExtractor):
    """Extrator que conta quantas vezes foi chamado (e nunca abre o arquivo)."""

    name = "espiao"

    def __init__(self) -> None:
        self.chamadas = 0

    @property
    def version(self) -> str:
        return "1.0-teste"

    def extract(self, pdf_path: str | Path) -> ExtractedDocument:
        self.chamadas += 1
        return ExtractedDocument(
            document_id="doc_espiao_fixo",
            filename=Path(pdf_path).name,
            text=TEXTO_EXTRAIDO,
            num_pages=2,
            extractor=self.name,
            extractor_version=self.version,
            extraction_duration_s=0.4242,
            warnings=[],
        )


def test_retomada_nao_reextrai_o_pdf(ambiente, tmp_path):
    """A fase 0 vira checkpoint: a retomada reusa o texto já extraído.

    O PDF é APAGADO antes da retomada — se o pipeline tentasse extrair de novo,
    ele falharia na validação de arquivo inexistente. O teste passa justamente
    porque o resultado da extração foi preservado em disco.
    """
    pdf = tmp_path / "artigo.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% PDF minimo para os testes de retomada\n%%EOF\n")

    espiao = _ExtratorEspiao()
    run_id = "run_com_pdf"
    with _falha_em(CrossReviewPhase, "falha simulada na fase 2"):
        with pytest.raises(RuntimeError):
            run_demo(mode="mock", pdf_path=pdf, extractor=espiao, run_id=run_id)

    assert espiao.chamadas == 1
    checkpoint_fase0 = ambiente.checkpoints / run_id / f"{EXTRACTION_PHASE}.json"
    assert checkpoint_fase0.exists(), "a extração concluída deve virar checkpoint"

    pdf.unlink()  # o arquivo original some entre a falha e a retomada
    retomado = run_demo(mode="mock", extractor=espiao, run_id=run_id)

    assert espiao.chamadas == 1, "a extração NÃO pode ser repetida na retomada"
    assert retomado.data["retomada"]["fases_restauradas"][0] == EXTRACTION_PHASE

    documento = retomado.data["document"]
    assert documento["document_id"] == "doc_espiao_fixo"
    assert documento["extraction_duration_s"] == pytest.approx(0.4242)
    assert documento["text_chars"] == len(TEXTO_EXTRAIDO)
    # A fase 0 também é uma fase: a duração dela precisa continuar no resumo.
    assert EXTRACTION_PHASE in retomado.data["resumo_execucao"]["duracao_por_fase_s"]
    assert retomado.data["resumo_execucao"]["alertas"] == []


# ---------------------------------------------------------------------------
# 4 — Retomar uma execução concluída não regrava nem re-executa
# ---------------------------------------------------------------------------

def test_retomar_execucao_concluida_nao_regrava_nem_reexecuta(ambiente):
    """O caso que corrompia relatórios: retomar algo que já tinha terminado.

    Antes, todas as fases eram restauradas e, na sequência, o resumo recém-criado
    (praticamente vazio) sobrescrevia o resumo bom dentro de final_report.json.
    """
    original = run_demo(mode="mock")
    run_id = original.data["run_id"]
    out_dir = ambiente.outputs / run_id
    antes = {
        arquivo.name: arquivo.read_bytes()
        for arquivo in (
            out_dir / "final_report.md",
            out_dir / "final_report.json",
            out_dir / "resumo_execucao.json",
        )
    }

    # Todas as fases passam a explodir: qualquer re-execução vira erro visível.
    contador: dict[str, int] = {}
    with pytest.MonkeyPatch.context() as mp:
        for fase_cls in (
            IndependentReviewPhase, CrossReviewPhase, EditorVerdictPhase, FinalReportPhase,
        ):
            mp.setattr(
                fase_cls,
                "run",
                lambda self, data, context: pytest.fail("nenhuma fase podia rodar"),
            )
        _contar_execucoes(mp, contador)
        repetido = run_demo(mode="mock", run_id=run_id)

    depois = {nome: (out_dir / nome).read_bytes() for nome in antes}
    assert depois == antes, "nenhum artefato da execução concluída pode ser regravado"

    assert repetido.data["run_id"] == original.data["run_id"]
    assert repetido.data["decisao"] == original.data["decisao"]
    assert repetido.data["resumo_execucao"] == original.data["resumo_execucao"]
    assert repetido.data["resumo_execucao"]["duracao_por_fase_s"], (
        "o resumo devolvido não pode ser o resumo vazio de uma execução que não rodou"
    )

    meta = json.loads((ambiente.checkpoints / f"{run_id}.meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "concluida"
    assert meta["concluida_em"]


# ---------------------------------------------------------------------------
# 5 — Trace e eventos anteriores continuam lá
# ---------------------------------------------------------------------------

def test_retomada_preserva_trace_e_eventos_anteriores(ambiente):
    """A retomada ACRESCENTA ao histórico da execução; não o substitui."""
    run_id = "run_historico"
    with _falha_em(EditorVerdictPhase, "falha simulada na fase 3"):
        with pytest.raises(RuntimeError):
            run_demo(mode="mock", run_id=run_id)

    trace_path = ambiente.traces / f"{run_id}.jsonl"
    trace_antes = trace_path.read_text(encoding="utf-8").splitlines()
    eventos_antes = ambiente.eventos.read_text(encoding="utf-8").splitlines()
    assert trace_antes and eventos_antes

    run_demo(mode="mock", run_id=run_id)

    trace_depois = trace_path.read_text(encoding="utf-8").splitlines()
    eventos_depois = ambiente.eventos.read_text(encoding="utf-8").splitlines()
    assert trace_depois[: len(trace_antes)] == trace_antes, "o trace anterior foi truncado"
    assert eventos_depois[: len(eventos_antes)] == eventos_antes, "eventos anteriores sumiram"
    assert len(trace_depois) > len(trace_antes)

    tipos = [json.loads(linha) for linha in trace_depois]
    assert any(evento["event_type"] == "error" for evento in tipos), (
        "a tentativa que falhou tem que continuar visível na linha do tempo"
    )
    assert [evento for evento in tipos if evento["event_type"] == "run_end"][-1]["status"] == "ok"
    assert any(
        evento["attributes"].get("checkpoint_restaurado") is True
        for evento in tipos
        if evento["event_type"] == "span_start"
    ), "as fases restauradas devem aparecer na timeline da retomada"


# ---------------------------------------------------------------------------
# 6 — Estado que vive só no config também sobrevive (auditoria do veredito)
# ---------------------------------------------------------------------------

def test_auditoria_do_veredito_sobrevive_a_retomada(ambiente):
    """A auditoria nasce na fase 3 e é consumida pela fase 4, via config.

    Com a fase 3 restaurada do checkpoint, ela não é reproduzida — e o relatório
    final saía com ``auditoria_veredito: null``, sem nenhum aviso.
    """
    limpo = run_demo(mode="mock")
    assert limpo.data["auditoria_veredito"] is not None, "pré-condição do teste"

    run_id = "run_falha_na_fase_4"
    with _falha_em(FinalReportPhase, "falha simulada na fase 4"):
        with pytest.raises(RuntimeError):
            run_demo(mode="mock", run_id=run_id)

    retomado = run_demo(mode="mock", run_id=run_id)

    assert retomado.data["retomada"]["fases_restauradas"] == [FASE_1, FASE_2, FASE_3]
    assert retomado.data["auditoria_veredito"] is not None
    assert (
        retomado.data["auditoria_veredito"]["resumo_auditoria"]
        == limpo.data["auditoria_veredito"]["resumo_auditoria"]
    )


# ---------------------------------------------------------------------------
# 7 — Unitários das duas peças novas
# ---------------------------------------------------------------------------

def test_estado_auxiliar_nao_e_confundido_com_uma_fase(tmp_path):
    """O sidecar de estado não pode aparecer em fases_concluidas()."""
    ckpt = CheckpointManager(tmp_path / "checkpoints", "run_x")
    ckpt.save(FASE_1, {"reviews": {}})
    ckpt.salvar_estado("estado", {"eventos_metricas": [], "duracao_acumulada_s": 1.5})

    assert ckpt.fases_concluidas() == [FASE_1]
    assert ckpt.carregar_estado("estado")["duracao_acumulada_s"] == 1.5
    assert ckpt.carregar_estado("inexistente") is None
    assert ckpt.caminho_estado("estado").parent == ckpt.dir.parent

    assert ckpt.limpar_fases() == [FASE_1]
    assert ckpt.fases_concluidas() == []
    assert ckpt.carregar_estado("estado") is not None, "limpar fases não apaga o estado"


def test_estado_ilegivel_nao_impede_a_retomada(ambiente):
    """Métricas corrompidas degradam o resumo; não podem custar a retomada.

    O sidecar de estado é informativo. Abortar por causa dele deixaria a
    execução sem forma de ser retomada — o oposto do que a persistência existe
    para garantir. O arquivo ruim é preservado para diagnóstico, e a lacuna
    aparece como alerta no resumo em vez de passar batida.
    """
    run_id = "run_estado_corrompido"
    with _falha_em(EditorVerdictPhase, "falha simulada na fase 3"):
        with pytest.raises(RuntimeError):
            run_demo(mode="mock", run_id=run_id)

    estado = ambiente.checkpoints / f"{run_id}.estado.json"
    estado.write_text("{ isto não é JSON válido", encoding="utf-8")

    retomado = run_demo(mode="mock", run_id=run_id)

    assert retomado.data["decisao"], "a retomada precisa concluir mesmo assim"
    assert (ambiente.checkpoints / f"{run_id}.estado.json.corrompido").exists()
    alertas = retomado.data["resumo_execucao"]["alertas"]
    assert any("não aparecem neste resumo" in alerta for alerta in alertas), (
        "a perda das métricas restauradas tem que ser denunciada, não silenciada"
    )


def test_coletor_restaurado_preserva_duracao_e_acumula_tempo():
    """Eventos restaurados mantêm a duração ORIGINAL e somam ao tempo de parede."""
    anterior = ExecutionCollector(run_id="run_y")
    anterior.registrar(fase=FASE_1, tipo="fase", nome=FASE_1, status="sucesso", duracao_s=2.5)
    salvos = anterior.eventos_como_dict()

    novo = ExecutionCollector(run_id="run_y")
    novo.registrar(fase=FASE_2, tipo="fase", nome=FASE_2, status="sucesso", duracao_s=1.0)
    assert novo.restaurar(salvos, duracao_anterior_s=9.0) == 1

    eventos = novo.eventos
    assert [evento.fase for evento in eventos] == [FASE_1, FASE_2], "ordem cronológica"
    assert eventos[0].duracao_s == 2.5, "a duração medida na execução original não muda"
    assert eventos[0].timestamp == ExecutionEvent.from_dict(salvos[0]).timestamp
    assert eventos[0].detalhes["restaurado_de_checkpoint"] is True
    assert novo.duracao_execucao_s >= 9.0, "o tempo já gasto na tentativa anterior conta"
