"""Testes determinísticos da captura de usage_metadata do ADK (Grupo 2).

Usa eventos SIMULADOS com o contrato do ADK (google-adk==1.27.5): objetos com
``author``, ``invocation_id``, ``id``, ``partial``, ``content.parts`` e
``usage_metadata`` (``prompt_token_count`` etc.) — como pede a task, as métricas
são desenvolvidas com eventos de exemplo, sem depender de execução real.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parents[2]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from metrics.adk_usage import (  # noqa: E402
    PrecosModelo,
    UsageChamada,
    VAR_PRECO_ENTRADA,
    VAR_PRECO_SAIDA,
    chave_dedup_usage,
    definir_coletor_adk,
    estimar_custo_usd,
    extrair_usage,
    precos_de_ambiente,
    registrar_usage_adk,
)
from metrics.coletor import ExecutionCollector  # noqa: E402
from metrics.resumo import gerar_resumo  # noqa: E402


def _usage(prompt=None, candidates=None, thoughts=None, cached=None, total=None):
    return SimpleNamespace(
        prompt_token_count=prompt,
        candidates_token_count=candidates,
        thoughts_token_count=thoughts,
        cached_content_token_count=cached,
        total_token_count=total,
    )


def _evento_adk(
    *,
    author="revisor_metodologia",
    invocation_id="inv-1",
    event_id="ev-1",
    partial=False,
    usage="padrao",
    texto="parecer gerado",
    model_version="gemini-2.5-flash",
):
    """Evento simulado com a forma do Event do ADK (campos via atributo)."""
    if usage == "padrao":
        usage = _usage(prompt=100, candidates=40, thoughts=15, cached=10, total=165)
    content = SimpleNamespace(parts=[SimpleNamespace(text=texto)]) if texto else None
    return SimpleNamespace(
        author=author,
        invocation_id=invocation_id,
        id=event_id,
        partial=partial,
        content=content,
        usage_metadata=usage,
        model_version=model_version,
    )


@pytest.fixture
def coletor():
    """Coletor registrado como consumidor ADK; desregistrado ao fim do teste."""
    instancia = ExecutionCollector(run_id="run-adk")
    definir_coletor_adk(instancia)
    yield instancia
    definir_coletor_adk(None)


# ---------------------------------------------------------------------------
# extrair_usage
# ---------------------------------------------------------------------------


def test_extrair_usage_le_os_cinco_campos_e_o_contexto():
    usage = extrair_usage(_evento_adk())
    assert usage == UsageChamada(
        tokens_entrada=100,
        tokens_resposta=40,
        tokens_pensamento=15,
        tokens_cache=10,
        tokens_total=165,
        modelo="gemini-2.5-flash",
        agente="revisor_metodologia",
        invocation_id="inv-1",
        event_id="ev-1",
    )
    assert usage.usage_disponivel


def test_extrair_usage_campo_ausente_vira_none_e_nao_zero():
    evento = _evento_adk(usage=_usage(prompt=100, candidates=40, total=140))
    usage = extrair_usage(evento)
    assert usage.tokens_pensamento is None
    assert usage.tokens_cache is None
    assert usage.tokens_total == 140


def test_extrair_usage_ignora_evento_parcial_de_streaming():
    assert extrair_usage(_evento_adk(partial=True)) is None


def test_extrair_usage_ignora_evento_do_usuario():
    assert extrair_usage(_evento_adk(author="user")) is None


def test_extrair_usage_ignora_evento_de_controle_sem_conteudo_e_sem_usage():
    assert extrair_usage(_evento_adk(usage=None, texto=None)) is None


def test_extrair_usage_resposta_sem_usage_metadata_vira_chamada_indisponivel():
    usage = extrair_usage(_evento_adk(usage=None))
    assert usage is not None
    assert not usage.usage_disponivel
    assert usage.tokens_total is None
    assert usage.agente == "revisor_metodologia"


def test_chave_dedup_prefere_event_id_e_cai_para_invocation_author():
    com_id = extrair_usage(_evento_adk(event_id="ev-9"))
    assert chave_dedup_usage(com_id) == "adk_usage:ev-9"
    sem_id = extrair_usage(_evento_adk(event_id=None))
    assert chave_dedup_usage(sem_id) == "adk_usage:inv-1:revisor_metodologia"
    sem_tudo = UsageChamada()
    assert chave_dedup_usage(sem_tudo) is None


# ---------------------------------------------------------------------------
# registrar_usage_adk (consumidor plugado nos loops dos agentes)
# ---------------------------------------------------------------------------


def test_registrar_usage_adk_sem_coletor_registrado_e_noop():
    definir_coletor_adk(None)
    assert registrar_usage_adk(_evento_adk(), fase="fase_1_revisao_independente") is None


def test_registrar_usage_adk_gera_evento_chamada_llm_com_detalhes(coletor):
    evento = registrar_usage_adk(_evento_adk(), fase="fase_1_revisao_independente")
    assert evento is not None
    assert evento.tipo == "chamada_llm"
    assert evento.status == "sucesso"
    assert evento.nome == "revisor_metodologia"
    assert evento.fase == "fase_1_revisao_independente"
    assert evento.detalhes["tokens_total"] == 165
    assert evento.detalhes["modelo"] == "gemini-2.5-flash"
    assert evento.detalhes["invocation_id"] == "inv-1"
    assert evento.detalhes["event_id"] == "ev-1"


def test_registrar_usage_adk_mesmo_event_id_duas_vezes_conta_uma(coletor):
    assert registrar_usage_adk(_evento_adk(), fase="f") is not None
    assert registrar_usage_adk(_evento_adk(), fase="f") is None
    assert len(coletor.eventos) == 1


def test_registrar_usage_adk_dedup_por_invocation_e_author_sem_event_id(coletor):
    assert registrar_usage_adk(_evento_adk(event_id=None), fase="f") is not None
    assert registrar_usage_adk(_evento_adk(event_id=None), fase="f") is None
    # Outro agente na MESMA invocação (ParallelAgent) conta separado.
    assert registrar_usage_adk(
        _evento_adk(event_id=None, author="revisor_clareza"), fase="f"
    ) is not None
    assert len(coletor.eventos) == 2


def test_registrar_usage_adk_parciais_cumulativos_so_o_final_conta(coletor):
    # Streaming: parciais trazem usage cumulativo e NÃO devem somar.
    registrar_usage_adk(
        _evento_adk(event_id="p1", partial=True, usage=_usage(prompt=100, total=110)), fase="f"
    )
    registrar_usage_adk(
        _evento_adk(event_id="p2", partial=True, usage=_usage(prompt=100, total=150)), fase="f"
    )
    final = registrar_usage_adk(
        _evento_adk(event_id="fim", usage=_usage(prompt=100, candidates=65, total=165)), fase="f"
    )
    assert final is not None
    assert len(coletor.eventos) == 1
    assert coletor.eventos[0].detalhes["tokens_total"] == 165


def test_registrar_usage_adk_sem_usage_metadata_registra_aviso(coletor):
    evento = registrar_usage_adk(_evento_adk(usage=None), fase="fase_3_editor_chefe")
    assert evento.status == "aviso"
    assert "sem usage_metadata" in evento.detalhes["mensagem"]
    assert evento.detalhes["tokens_total"] is None


# ---------------------------------------------------------------------------
# Agregação no resumo (por agente, por fase e por execução completa)
# ---------------------------------------------------------------------------


def _popular_execucao(coletor):
    registrar_usage_adk(
        _evento_adk(
            event_id="e1", author="revisor_metodologia",
            usage=_usage(prompt=100, candidates=40, thoughts=15, cached=10, total=165),
        ),
        fase="fase_1_revisao_independente",
    )
    registrar_usage_adk(
        _evento_adk(
            event_id="e2", author="revisor_clareza",
            usage=_usage(prompt=90, candidates=30, total=120),
        ),
        fase="fase_1_revisao_independente",
    )
    registrar_usage_adk(
        _evento_adk(
            event_id="e3", author="editor_chefe",
            usage=_usage(prompt=200, candidates=80, total=280),
        ),
        fase="fase_3_editor_chefe",
    )


def test_gerar_resumo_agrega_tokens_por_execucao_agente_e_fase(coletor):
    _popular_execucao(coletor)
    resumo = gerar_resumo(coletor.eventos, run_id=coletor.run_id)
    assert resumo.chamadas_llm == 3
    assert resumo.tokens_totais == 565
    assert resumo.tokens_execucao == {
        "tokens_entrada": 390,
        "tokens_resposta": 150,
        "tokens_pensamento": 15,
        "tokens_cache": 10,
        "tokens_total": 565,
    }
    assert resumo.tokens_por_agente == {
        "revisor_metodologia": 165,
        "revisor_clareza": 120,
        "editor_chefe": 280,
    }
    assert resumo.tokens_por_fase == {
        "fase_1_revisao_independente": 285,
        "fase_3_editor_chefe": 280,
    }
    assert resumo.modelo_usado == "gemini-2.5-flash"


def test_gerar_resumo_sem_chamadas_llm_mantem_tudo_indisponivel(coletor):
    coletor.registrar(fase="fase_1", tipo="tool", nome="validar_completude", status="sucesso")
    resumo = gerar_resumo(coletor.eventos, run_id=coletor.run_id)
    assert resumo.chamadas_llm is None
    assert resumo.tokens_totais is None
    assert resumo.tokens_execucao is None
    assert resumo.tokens_por_agente == {}
    assert resumo.tokens_por_fase == {}
    assert resumo.modelo_usado is None
    assert resumo.custo_estimado is None


def test_gerar_resumo_usage_parcialmente_indisponivel_soma_o_medido(coletor):
    _popular_execucao(coletor)
    registrar_usage_adk(
        _evento_adk(event_id="e4", author="revisor_significancia", usage=None),
        fase="fase_1_revisao_independente",
    )
    resumo = gerar_resumo(coletor.eventos, run_id=coletor.run_id)
    assert resumo.chamadas_llm == 4
    # Chamada sem usage não zera nem invalida o total medido…
    assert resumo.tokens_totais == 565
    # …o agente sem medição aparece com None (indisponível ≠ zero)…
    assert resumo.tokens_por_agente["revisor_significancia"] is None
    # …e o aviso da chamada fica auditável nos alertas.
    assert any("sem usage_metadata" in alerta for alerta in resumo.alertas)
    assert resumo.status_final == "sucesso_com_alertas"


def test_gerar_resumo_multiplos_modelos_lista_ordenada(coletor):
    registrar_usage_adk(_evento_adk(event_id="a", model_version="gemini-2.5-pro"), fase="f")
    registrar_usage_adk(_evento_adk(event_id="b", model_version="gemini-2.5-flash"), fase="f")
    resumo = gerar_resumo(coletor.eventos, run_id=coletor.run_id)
    assert resumo.modelo_usado == "gemini-2.5-flash, gemini-2.5-pro"


# ---------------------------------------------------------------------------
# Custo preparado (preço configurado, nunca fixo no código)
# ---------------------------------------------------------------------------


def test_precos_de_ambiente_sem_configuracao_devolve_none():
    assert precos_de_ambiente(env={}) is None
    # Configuração parcial também é tratada como ausente.
    assert precos_de_ambiente(env={VAR_PRECO_ENTRADA: "0.30"}) is None
    assert precos_de_ambiente(env={VAR_PRECO_ENTRADA: "abc", VAR_PRECO_SAIDA: "2.50"}) is None


def test_precos_de_ambiente_com_configuracao_completa():
    precos = precos_de_ambiente(env={VAR_PRECO_ENTRADA: "0.30", VAR_PRECO_SAIDA: "2.50"})
    assert precos == PrecosModelo(
        usd_por_milhao_tokens_entrada=0.30, usd_por_milhao_tokens_saida=2.50
    )


def test_estimar_custo_usd_sem_preco_ou_sem_consumo_devolve_none():
    precos = PrecosModelo(0.30, 2.50)
    assert estimar_custo_usd(1000, 500, None) is None
    assert estimar_custo_usd(None, None, precos) is None


def test_estimar_custo_usd_calcula_sobre_o_consumo_medido():
    precos = PrecosModelo(usd_por_milhao_tokens_entrada=0.30, usd_por_milhao_tokens_saida=2.50)
    assert estimar_custo_usd(1_000_000, 1_000_000, precos) == pytest.approx(2.80)
    # Consumo parcialmente medido: estima só a parte disponível.
    assert estimar_custo_usd(1_000_000, None, precos) == pytest.approx(0.30)


def test_gerar_resumo_com_precos_preenche_custo_estimado(coletor):
    _popular_execucao(coletor)
    precos = PrecosModelo(usd_por_milhao_tokens_entrada=1.0, usd_por_milhao_tokens_saida=10.0)
    resumo = gerar_resumo(coletor.eventos, run_id=coletor.run_id, precos=precos)
    assert resumo.custo_estimado == pytest.approx(390 / 1e6 * 1.0 + 150 / 1e6 * 10.0)


def test_gerar_resumo_custo_precifica_cada_chamada_pelo_seu_proprio_modelo(coletor, monkeypatch):
    """Regressão: editor com modelo diferente do resto, ou fallback assumindo
    a resposta, não pode ser precificado por um preço ÚNICO aplicado ao total
    agregado de tokens da execução — cada chamada usa o preço do MODELO que
    respondeu NAQUELA chamada especificamente.
    """
    monkeypatch.delenv(VAR_PRECO_ENTRADA, raising=False)
    monkeypatch.delenv(VAR_PRECO_SAIDA, raising=False)
    registrar_usage_adk(
        _evento_adk(
            event_id="e1", author="revisor_metodologia", model_version="gemini-2.5-flash",
            usage=_usage(prompt=1_000_000, candidates=1_000_000, total=2_000_000),
        ),
        fase="fase_1_revisao_independente",
    )
    registrar_usage_adk(
        _evento_adk(
            event_id="e2", author="editor_chefe", model_version="gpt-4o-mini",
            usage=_usage(prompt=1_000_000, candidates=1_000_000, total=2_000_000),
        ),
        fase="fase_3_editor_chefe",
    )
    resumo = gerar_resumo(coletor.eventos, run_id=coletor.run_id)

    from metrics.precos_modelos import preco_oficial

    preco_gemini = preco_oficial("gemini", "gemini-2.5-flash")
    preco_gpt = preco_oficial("openai", "gpt-4o-mini")
    custo_correto_por_chamada = (
        1_000_000 / 1e6 * preco_gemini.usd_por_milhao_tokens_entrada
        + 1_000_000 / 1e6 * preco_gemini.usd_por_milhao_tokens_saida
        + 1_000_000 / 1e6 * preco_gpt.usd_por_milhao_tokens_entrada
        + 1_000_000 / 1e6 * preco_gpt.usd_por_milhao_tokens_saida
    )
    assert resumo.custo_estimado == pytest.approx(custo_correto_por_chamada)

    # Prova de que o bug antigo (um preço único para a execução inteira)
    # daria um número DIFERENTE: aplicar só o preço do gemini aos 4M de
    # tokens agregados (2M+2M de entrada/saída) superestima o custo real,
    # porque metade desses tokens veio do gpt-4o-mini, mais barato.
    custo_com_preco_unico_do_gemini = (
        2_000_000 / 1e6 * preco_gemini.usd_por_milhao_tokens_entrada
        + 2_000_000 / 1e6 * preco_gemini.usd_por_milhao_tokens_saida
    )
    assert resumo.custo_estimado != pytest.approx(custo_com_preco_unico_do_gemini)
