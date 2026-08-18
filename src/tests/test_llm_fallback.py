"""Testes do fallback de LLM (``llm_fallback.py`` + ``model_provider``).

Todos rodam OFFLINE, com falhas SIMULADAS: nenhuma chamada de rede, nenhuma
chave real e nenhuma dependência de indisponibilidade de verdade nas APIs. O
ambiente é zerado antes de cada teste porque ``model_provider`` chama
``load_dotenv()`` na importação.

Escopo (Pessoa 1 do grupo): configuração principal + reserva e a TROCA em
falha temporária. A verificação fina de traces/métricas é da Pessoa 2.
"""

import asyncio
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from llm_fallback import (  # noqa: E402
    MOTIVO_ERRO_DE_REDE,
    MOTIVO_INDISPONIBILIDADE,
    MOTIVO_LIMITE_REQUISICOES,
    MOTIVO_TIMEOUT,
    classificar_falha_temporaria,
    criar_modelo_com_fallback,
)
from model_provider import (  # noqa: E402
    Provider,
    build_model,
    exigir_api_key,
    resolver_config_fallback,
)

#: Variáveis que influenciam a resolução e precisam sair do ambiente do teste.
_VARIAVEIS = [
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_PROVIDER_EDITOR",
    "LLM_MODEL_EDITOR",
    "LLM_FALLBACK_PROVIDER",
    "LLM_FALLBACK_MODEL",
    "LLM_FALLBACK_PROVIDER_EDITOR",
    "LLM_FALLBACK_MODEL_EDITOR",
    "LLM_JSON_SCHEMA",
    "GEMINI_MODEL",
    "GOOGLE_API_KEY",
    "MARITACA_API_KEY",
    "OPENAI_API_KEY",
]


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch):
    """Remove qualquer configuração herdada do ``.env`` ou do shell."""
    for nome in _VARIAVEIS:
        monkeypatch.delenv(nome, raising=False)


# ---------------------------------------------------------------------------
# Falhas simuladas: os mesmos "formatos" de erro que os SDKs reais levantam
# ---------------------------------------------------------------------------

class RateLimitError(Exception):
    """Simula o 429 do litellm/openai (expõe ``status_code``)."""

    status_code = 429


class ServiceUnavailableError(Exception):
    """Simula o 503 (indisponibilidade) pelo NOME, sem código."""


class ErroComCausaDeRede(Exception):
    """Simula o SDK embrulhando um erro de transporte em exceção própria."""


class ChaveInvalidaError(Exception):
    """Simula o 401 — erro PERMANENTE, fallback não deve ser acionado."""

    status_code = 401


# ---------------------------------------------------------------------------
# Classificação: temporária x permanente
# ---------------------------------------------------------------------------

def test_timeout_nativo_e_temporario():
    assert classificar_falha_temporaria(TimeoutError("deadline")) == MOTIVO_TIMEOUT


def test_erro_de_conexao_nativo_e_temporario():
    assert classificar_falha_temporaria(ConnectionError("reset")) == MOTIVO_ERRO_DE_REDE


def test_status_http_temporarios_por_codigo():
    assert classificar_falha_temporaria(RateLimitError()) == MOTIVO_LIMITE_REQUISICOES

    class Erro503(Exception):
        status_code = 503

    assert classificar_falha_temporaria(Erro503()) == MOTIVO_INDISPONIBILIDADE


def test_nome_da_classe_classifica_sem_codigo():
    assert (
        classificar_falha_temporaria(ServiceUnavailableError("down"))
        == MOTIVO_INDISPONIBILIDADE
    )


def test_causa_encadeada_e_inspecionada():
    try:
        try:
            raise ConnectionError("socket fechado")
        except ConnectionError as interno:
            raise ErroComCausaDeRede("falha na chamada") from interno
    except ErroComCausaDeRede as exc:
        assert classificar_falha_temporaria(exc) == MOTIVO_ERRO_DE_REDE


def test_erro_permanente_nao_e_classificado():
    assert classificar_falha_temporaria(ChaveInvalidaError("api key")) is None
    assert classificar_falha_temporaria(ValueError("payload inválido")) is None


def test_status_permanente_vence_nome_sugestivo():
    """Um 401 é permanente mesmo que o nome da classe pareça temporário."""

    class UnavailableKeyError(Exception):  # nome contém "unavailable"
        status_code = 401

    assert classificar_falha_temporaria(UnavailableKeyError()) is None


# ---------------------------------------------------------------------------
# Resolução da configuração da reserva
# ---------------------------------------------------------------------------

def test_sem_configuracao_nao_ha_reserva():
    assert resolver_config_fallback() is None


def test_reserva_por_provedor_usa_modelo_padrao(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "maritaca")
    cfg = resolver_config_fallback()
    assert cfg is not None
    assert cfg.provider is Provider.MARITACA
    assert cfg.model == "sabiazinho-4"


def test_reserva_so_com_modelo_herda_o_provedor_principal(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "gemini-2.0-flash")
    cfg = resolver_config_fallback()
    assert cfg is not None
    assert cfg.provider is Provider.GEMINI
    assert cfg.model == "gemini-2.0-flash"


def test_reserva_por_papel_sobrescreve_a_global(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "maritaca")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER_EDITOR", "openai")
    monkeypatch.setenv("LLM_FALLBACK_MODEL_EDITOR", "gpt-4o-mini")

    global_ = resolver_config_fallback()
    editor = resolver_config_fallback(papel="editor")
    assert global_ is not None and global_.provider is Provider.MARITACA
    assert editor is not None and editor.provider is Provider.OPENAI
    assert editor.model == "gpt-4o-mini"


def test_exigir_api_key_cobra_tambem_a_chave_da_reserva(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "chave_principal_falsa")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "openai")
    with pytest.raises(RuntimeError) as excinfo:
        exigir_api_key()
    assert "OPENAI_API_KEY" in str(excinfo.value)
    assert "RESERVA" in str(excinfo.value)

    monkeypatch.setenv("OPENAI_API_KEY", "chave_reserva_falsa")
    assert exigir_api_key().provider is Provider.GEMINI


# ---------------------------------------------------------------------------
# A troca em si (ModeloComFallback), com modelos FALSOS do próprio ADK
# ---------------------------------------------------------------------------

pytest.importorskip("google.adk", reason="o wrapper de fallback é um BaseLlm do ADK")


def _modelo_que_falha(excecao, nome="modelo_falho"):
    """``BaseLlm`` falso cuja chamada sempre levanta ``excecao``."""
    from google.adk.models.base_llm import BaseLlm

    class ModeloQueFalha(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):
            raise excecao
            yield  # pragma: no cover — torna a função um async generator

    return ModeloQueFalha(model=nome)


def _modelo_que_responde(texto, nome="modelo_ok"):
    """``BaseLlm`` falso que devolve uma resposta de texto simples."""
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    class ModeloQueResponde(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):
            yield LlmResponse(
                content=types.Content(
                    role="model", parts=[types.Part.from_text(text=texto)]
                )
            )

    return ModeloQueResponde(model=nome)


def _novo_request(model: str = ""):
    from google.adk.models.llm_request import LlmRequest

    return LlmRequest(model=model)


def _rodar(modelo, llm_request):
    """Consome o async generator do modelo e devolve a lista de respostas."""

    async def _consumir():
        return [r async for r in modelo.generate_content_async(llm_request)]

    return asyncio.run(_consumir())


def _wrapper(primario, reserva):
    return criar_modelo_com_fallback(
        modelo_primario=primario,
        modelo_reserva=reserva,
        rotulo_primario="gemini:principal",
        rotulo_reserva="maritaca:reserva",
    )


def test_falha_temporaria_troca_para_a_reserva():
    modelo = _wrapper(
        _modelo_que_falha(RateLimitError("quota"), nome="principal"),
        _modelo_que_responde("resposta da reserva", nome="reserva"),
    )
    request = _novo_request(model=modelo.model)

    respostas = _rodar(modelo, request)

    assert len(respostas) == 1
    assert respostas[0].content.parts[0].text == "resposta da reserva"
    # A tentativa que respondeu apontava para o modelo RESERVA.
    assert request.model == "reserva"


def test_sem_falha_o_principal_responde_e_a_reserva_nao_entra():
    modelo = _wrapper(
        _modelo_que_responde("resposta do principal", nome="principal"),
        _modelo_que_falha(AssertionError("a reserva NÃO devia ser chamada")),
    )
    request = _novo_request(model=modelo.model)

    respostas = _rodar(modelo, request)

    assert respostas[0].content.parts[0].text == "resposta do principal"
    assert request.model == "principal"


def test_erro_permanente_nao_aciona_a_reserva():
    modelo = _wrapper(
        _modelo_que_falha(ChaveInvalidaError("401"), nome="principal"),
        _modelo_que_responde("não devia chegar aqui"),
    )
    with pytest.raises(ChaveInvalidaError):
        _rodar(modelo, _novo_request(model=modelo.model))


def test_reserva_tambem_falhando_o_erro_sobe():
    modelo = _wrapper(
        _modelo_que_falha(RateLimitError("quota"), nome="principal"),
        _modelo_que_falha(ServiceUnavailableError("down"), nome="reserva"),
    )
    with pytest.raises(ServiceUnavailableError):
        _rodar(modelo, _novo_request(model=modelo.model))


def test_troca_emite_eventos_no_trace():
    """A troca aparece no trace: provedor inicial, motivo e quem respondeu."""
    from observability import MemoryExporter, Tracer

    exporter = MemoryExporter()
    tracer = Tracer(exporter=exporter)

    modelo = _wrapper(
        _modelo_que_falha(RateLimitError("quota"), nome="principal"),
        _modelo_que_responde("ok", nome="reserva"),
    )
    with tracer.run("teste_fallback"):
        _rodar(modelo, _novo_request(model=modelo.model))

    eventos = {e.name: e for e in exporter.events}
    acionado = eventos["llm_fallback_acionado"]
    assert acionado.attributes["provedor_inicial"] == "gemini:principal"
    assert acionado.attributes["motivo_da_falha"] == MOTIVO_LIMITE_REQUISICOES
    assert acionado.attributes["opcao_fallback"] == "maritaca:reserva"

    respondeu = eventos["llm_fallback_respondeu"]
    assert respondeu.attributes["modelo_que_respondeu"] == "maritaca:reserva"


def test_cada_tentativa_vira_um_span_no_trace():
    """Principal E reserva aparecem como sub-spans próprios (início/fim/duração)."""
    from observability import MemoryExporter, Tracer
    from observability.events import EventType

    exporter = MemoryExporter()
    tracer = Tracer(exporter=exporter)

    modelo = _wrapper(
        _modelo_que_falha(RateLimitError("quota"), nome="principal"),
        _modelo_que_responde("ok", nome="reserva"),
    )
    with tracer.run("teste_fallback"):
        _rodar(modelo, _novo_request(model=modelo.model))

    nomes_span = [
        e.name for e in exporter.events
        if e.event_type in (EventType.SPAN_START.value, EventType.SPAN_END.value)
    ]
    assert "llm_tentativa_principal" in nomes_span
    assert "llm_tentativa_reserva" in nomes_span

    fim_principal = next(
        e for e in exporter.events
        if e.name == "llm_tentativa_principal" and e.event_type == EventType.SPAN_END.value
    )
    assert fim_principal.status == "erro"  # a tentativa principal falhou
    assert fim_principal.duration_s is not None

    fim_reserva = next(
        e for e in exporter.events
        if e.name == "llm_tentativa_reserva" and e.event_type == EventType.SPAN_END.value
    )
    assert fim_reserva.status == "ok"  # a reserva respondeu


def test_sem_fallback_a_tentativa_principal_ainda_vira_span():
    """Mesmo sem troca, a tentativa (bem-sucedida) do principal fica no trace."""
    from observability import MemoryExporter, Tracer
    from observability.events import EventType

    exporter = MemoryExporter()
    tracer = Tracer(exporter=exporter)

    modelo = _wrapper(
        _modelo_que_responde("resposta do principal", nome="principal"),
        _modelo_que_falha(AssertionError("a reserva NÃO devia ser chamada")),
    )
    with tracer.run("teste_fallback"):
        _rodar(modelo, _novo_request(model=modelo.model))

    fim_principal = next(
        e for e in exporter.events
        if e.name == "llm_tentativa_principal" and e.event_type == EventType.SPAN_END.value
    )
    assert fim_principal.status == "ok"
    assert not any(e.name == "llm_tentativa_reserva" for e in exporter.events)


def test_eventos_de_fallback_trazem_papel_e_fase_no_trace():
    """author/phase do evento de trace refletem o papel e a fase da execução."""
    from observability import MemoryExporter, Tracer
    from metrics.coletor import ExecutionCollector

    exporter = MemoryExporter()
    tracer = Tracer(exporter=exporter)
    coletor = ExecutionCollector(run_id="run-trace-fase")

    modelo = criar_modelo_com_fallback(
        modelo_primario=_modelo_que_falha(RateLimitError("quota"), nome="principal"),
        modelo_reserva=_modelo_que_responde("ok", nome="reserva"),
        rotulo_primario="gemini:principal",
        rotulo_reserva="maritaca:reserva",
        papel="editor",
    )
    with tracer.run("teste_fallback"):
        with coletor.fase("fase_3_editor_chefe"):
            _rodar(modelo, _novo_request(model=modelo.model))

    acionado = next(e for e in exporter.events if e.name == "llm_fallback_acionado")
    assert acionado.author == "editor"
    assert acionado.phase == "fase_3_editor_chefe"


# ---------------------------------------------------------------------------
# Integração com métricas (ExecutionCollector / Grupo 2)
# ---------------------------------------------------------------------------

def test_troca_registra_eventos_fallback_llm_no_coletor():
    """A troca também aparece nas MÉTRICAS: tipo="fallback_llm" no coletor."""
    from metrics.coletor import ExecutionCollector
    from metrics.adk_usage import definir_coletor_adk

    coletor = ExecutionCollector(run_id="run-metricas-fallback")
    definir_coletor_adk(coletor)
    try:
        modelo = _wrapper(
            _modelo_que_falha(RateLimitError("quota"), nome="principal"),
            _modelo_que_responde("ok", nome="reserva"),
        )
        with coletor.fase("fase_1_revisao_independente"):
            _rodar(modelo, _novo_request(model=modelo.model))
    finally:
        definir_coletor_adk(None)

    fallback_eventos = [e for e in coletor.eventos if e.tipo == "fallback_llm"]
    assert [e.detalhes["evento"] for e in fallback_eventos] == ["acionado", "respondeu"]

    acionado = fallback_eventos[0]
    assert acionado.fase == "fase_1_revisao_independente"
    assert acionado.status == "aviso"
    assert acionado.detalhes["provedor_inicial"] == "gemini:principal"
    assert acionado.detalhes["motivo_falha"] == MOTIVO_LIMITE_REQUISICOES
    assert acionado.detalhes["opcao_fallback"] == "maritaca:reserva"

    respondeu = fallback_eventos[1]
    assert respondeu.status == "sucesso"
    assert respondeu.detalhes["modelo_que_respondeu"] == "maritaca:reserva"


def test_esgotamento_registra_falha_no_coletor():
    """Principal e reserva falhando: métrica de status="falha" para auditoria."""
    from metrics.coletor import ExecutionCollector
    from metrics.adk_usage import definir_coletor_adk

    coletor = ExecutionCollector(run_id="run-metricas-esgotado")
    definir_coletor_adk(coletor)
    try:
        modelo = _wrapper(
            _modelo_que_falha(RateLimitError("quota"), nome="principal"),
            _modelo_que_falha(ServiceUnavailableError("down"), nome="reserva"),
        )
        with coletor.fase("fase_2_leitura_cruzada"):
            with pytest.raises(ServiceUnavailableError):
                _rodar(modelo, _novo_request(model=modelo.model))
    finally:
        definir_coletor_adk(None)

    fallback_eventos = [e for e in coletor.eventos if e.tipo == "fallback_llm"]
    assert [e.detalhes["evento"] for e in fallback_eventos] == ["acionado", "esgotado"]
    esgotado = fallback_eventos[1]
    assert esgotado.status == "falha"
    assert esgotado.detalhes["opcao_fallback"] == "maritaca:reserva"


def test_sem_coletor_registrado_nao_gera_metrica_nem_erro():
    """Sem definir_coletor_adk(...), a troca continua funcionando (no-op nas métricas)."""
    from metrics.adk_usage import obter_coletor_adk

    assert obter_coletor_adk() is None  # nada registrado nesta execução de teste

    modelo = _wrapper(
        _modelo_que_falha(RateLimitError("quota"), nome="principal"),
        _modelo_que_responde("ok", nome="reserva"),
    )
    respostas = _rodar(modelo, _novo_request(model=modelo.model))
    assert respostas[0].content.parts[0].text == "ok"


def test_resumo_agrega_fallbacks_acionados_e_respondidos():
    """gerar_resumo() conta as trocas e lista o desfecho de cada uma."""
    from metrics.coletor import ExecutionCollector
    from metrics.adk_usage import definir_coletor_adk
    from metrics.resumo import gerar_resumo

    coletor = ExecutionCollector(run_id="run-resumo-fallback")
    definir_coletor_adk(coletor)
    try:
        modelo = _wrapper(
            _modelo_que_falha(RateLimitError("quota"), nome="principal"),
            _modelo_que_responde("ok", nome="reserva"),
        )
        with coletor.fase("fase_1_revisao_independente"):
            _rodar(modelo, _novo_request(model=modelo.model))
    finally:
        definir_coletor_adk(None)

    resumo = gerar_resumo(coletor.eventos, run_id=coletor.run_id)
    assert resumo.quantidade_fallbacks_acionados == 1
    assert resumo.quantidade_fallbacks_respondidos == 1
    assert resumo.quantidade_fallbacks_esgotados == 0
    assert len(resumo.fallbacks_llm) == 2
    assert resumo.fallbacks_llm[0]["provedor_inicial"] == "gemini:principal"
    assert resumo.fallbacks_llm[0]["motivo_falha"] == MOTIVO_LIMITE_REQUISICOES
    assert resumo.fallbacks_llm[1]["modelo_que_respondeu"] == "maritaca:reserva"
    # O aviso do "acionado" também aparece na lista de alertas do resumo.
    assert any("fallback acionado" in alerta for alerta in resumo.alertas)


# ---------------------------------------------------------------------------
# Integração com build_model
# ---------------------------------------------------------------------------

def test_build_model_sem_fallback_mantem_a_rota_nativa():
    assert build_model() == "gemini-2.5-flash"  # string = rota nativa do ADK


def test_build_model_com_fallback_devolve_o_wrapper(monkeypatch):
    # Reserva no MESMO provedor (Gemini) para o teste não exigir litellm.
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "gemini-2.0-flash")

    modelo = build_model()

    assert type(modelo).__name__ == "ModeloComFallback"
    assert modelo.rotulo_primario == "gemini:gemini-2.5-flash"
    assert modelo.rotulo_reserva == "gemini:gemini-2.0-flash"
    assert modelo.modelo_primario.model == "gemini-2.5-flash"
    assert modelo.modelo_reserva.model == "gemini-2.0-flash"
