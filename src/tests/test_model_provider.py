"""Testes da escolha centralizada de provedor/modelo (``model_provider.py``).

Todos rodam OFFLINE: nenhuma chamada de rede e nenhuma chave real. O ambiente é
zerado antes de cada teste porque ``model_provider`` chama ``load_dotenv()`` na
importação — sem isso, o ``.env`` da máquina de quem roda os testes mudaria o
resultado.
"""

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from model_provider import (  # noqa: E402
    PROVEDORES,
    Provider,
    build_model,
    descricao_modelo,
    exigir_api_key,
    normalizar_provider,
    resolver_config,
)

#: Variáveis que influenciam a resolução e precisam sair do ambiente do teste.
_VARIAVEIS = [
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_PROVIDER_EDITOR",
    "LLM_MODEL_EDITOR",
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
# Resolução: default, legado e variáveis globais
# ---------------------------------------------------------------------------

def test_default_e_gemini_pela_rota_nativa():
    cfg = resolver_config()
    assert cfg.provider is Provider.GEMINI
    assert cfg.model == PROVEDORES[Provider.GEMINI].modelo_padrao
    # Rota nativa do ADK: sem prefixo de roteamento do LiteLLM.
    assert cfg.spec.prefixo_litellm is None


def test_env_legado_gemini_model_continua_valendo(monkeypatch):
    """Um .env antigo (sem nenhuma variável LLM_*) deve seguir funcionando."""
    monkeypatch.setenv("GEMINI_MODEL", "gemini-1.5-pro")
    cfg = resolver_config()
    assert cfg.provider is Provider.GEMINI
    assert cfg.model == "gemini-1.5-pro"


def test_llm_model_tem_precedencia_sobre_gemini_model_legado(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-1.5-pro")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o")
    cfg = resolver_config()
    assert cfg.provider is Provider.OPENAI
    assert cfg.model == "gpt-4o"


def test_provedor_sem_modelo_usa_o_padrao_do_provedor(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "maritaca")
    cfg = resolver_config()
    assert cfg.model == "sabiazinho-4"
    assert cfg.litellm_model == "openai/sabiazinho-4"
    assert cfg.spec.api_base == "https://chat.maritaca.ai/api"


def test_config_explicita_vence_o_ambiente(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    cfg = resolver_config({"provider": "maritaca", "model": "sabiazinho-3"})
    assert cfg.provider is Provider.MARITACA
    assert cfg.model == "sabiazinho-3"


# ---------------------------------------------------------------------------
# Sobrescrita por papel
# ---------------------------------------------------------------------------

def test_override_de_papel_nao_afeta_os_demais(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "maritaca")
    monkeypatch.setenv("LLM_PROVIDER_EDITOR", "openai")
    monkeypatch.setenv("LLM_MODEL_EDITOR", "gpt-4o")

    revisores = resolver_config()
    editor = resolver_config(papel="editor")

    assert revisores.provider is Provider.MARITACA
    assert revisores.model == "sabiazinho-4"
    assert editor.provider is Provider.OPENAI
    assert editor.model == "gpt-4o"


def test_papel_sem_override_herda_a_escolha_global(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert resolver_config(papel="editor").provider is Provider.OPENAI


# ---------------------------------------------------------------------------
# Erros explícitos
# ---------------------------------------------------------------------------

def test_provedor_desconhecido_lista_os_aceitos():
    with pytest.raises(ValueError) as exc:
        normalizar_provider("llama")
    mensagem = str(exc.value)
    assert "llama" in mensagem
    for provider in Provider:
        assert provider.value in mensagem


def test_sinonimos_de_provedor_sao_aceitos():
    assert normalizar_provider("Google") is Provider.GEMINI
    assert normalizar_provider("MariTalk") is Provider.MARITACA
    assert normalizar_provider("GPT") is Provider.OPENAI


def test_chave_ausente_cita_a_variavel_do_provedor_escolhido(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "maritaca")
    monkeypatch.setenv("GOOGLE_API_KEY", "chave-do-gemini-nao-serve-aqui")
    with pytest.raises(RuntimeError) as exc:
        exigir_api_key()
    assert "MARITACA_API_KEY" in str(exc.value)


def test_chave_presente_devolve_a_configuracao(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    cfg = exigir_api_key()
    assert cfg.provider is Provider.OPENAI


def test_llm_json_schema_invalido_falha(monkeypatch):
    monkeypatch.setenv("LLM_JSON_SCHEMA", "talvez")
    with pytest.raises(ValueError):
        resolver_config()


# ---------------------------------------------------------------------------
# Structured output
# ---------------------------------------------------------------------------

def test_structured_output_default_por_provedor(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert resolver_config().structured_output is True

    monkeypatch.setenv("LLM_PROVIDER", "maritaca")
    assert resolver_config().structured_output is False


def test_llm_json_schema_forca_os_dois_lados(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "maritaca")
    monkeypatch.setenv("LLM_JSON_SCHEMA", "on")
    assert resolver_config().structured_output is True

    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_JSON_SCHEMA", "off")
    assert resolver_config().structured_output is False


# ---------------------------------------------------------------------------
# Construção do modelo para o ADK
# ---------------------------------------------------------------------------

def test_gemini_devolve_string_preservando_a_rota_nativa(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_MODEL", "gemini-2.5-flash")
    modelo = build_model()
    assert isinstance(modelo, str)
    assert modelo == "gemini-2.5-flash"


def test_maritaca_monta_litellm_com_endpoint_proprio(monkeypatch):
    pytest.importorskip("litellm", reason="Maritaca/OpenAI dependem de litellm.")
    monkeypatch.setenv("LLM_PROVIDER", "maritaca")
    monkeypatch.setenv("MARITACA_API_KEY", "chave-teste")

    from google.adk.models.lite_llm import LiteLlm

    modelo = build_model()
    assert isinstance(modelo, LiteLlm)
    assert modelo.model == "openai/sabiazinho-4"
    # A Maritaca fala o protocolo da OpenAI em outro endpoint.
    assert modelo._additional_args["api_base"] == "https://chat.maritaca.ai/api"
    assert modelo._additional_args["api_key"] == "chave-teste"


def test_openai_monta_litellm_com_prefixo_de_roteamento(monkeypatch):
    pytest.importorskip("litellm", reason="Maritaca/OpenAI dependem de litellm.")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")

    from google.adk.models.lite_llm import LiteLlm

    modelo = build_model()
    assert isinstance(modelo, LiteLlm)
    assert modelo.model == "openai/gpt-4o-mini"


# ---------------------------------------------------------------------------
# Provedores sem structured output nativo (o shim de LiteLlm)
# ---------------------------------------------------------------------------

def _resposta_falsa(texto: str, partial: bool = False):
    """Imita o suficiente de ``LlmResponse`` para exercitar a limpeza do texto."""
    from types import SimpleNamespace

    return SimpleNamespace(
        partial=partial,
        content=SimpleNamespace(parts=[SimpleNamespace(text=texto)]),
    )


def test_resposta_em_bloco_markdown_vira_json_puro():
    """Sem json_schema nativo, o modelo pode embrulhar o JSON em ```json."""
    import json

    from model_provider import _limpar_json_da_resposta

    resposta = _resposta_falsa('```json\n{"decisao": 3}\n```')
    limpa = _limpar_json_da_resposta(resposta)
    assert json.loads(limpa.content.parts[0].text) == {"decisao": 3}


def test_texto_que_nao_e_json_passa_intacto():
    """Falhar na validação é melhor do que mascarar aqui uma resposta ruim."""
    from model_provider import _limpar_json_da_resposta

    resposta = _resposta_falsa("desculpe, não consegui avaliar o artigo")
    limpa = _limpar_json_da_resposta(resposta)
    assert limpa.content.parts[0].text == "desculpe, não consegui avaliar o artigo"


def test_chunk_parcial_nao_e_reescrito():
    from model_provider import _limpar_json_da_resposta

    resposta = _resposta_falsa('{"deci', partial=True)
    assert _limpar_json_da_resposta(resposta).content.parts[0].text == '{"deci'


def test_shim_remove_response_schema_do_pedido(monkeypatch):
    """O pedido não pode levar um parâmetro que o provedor não entende."""
    pytest.importorskip("litellm", reason="Maritaca/OpenAI dependem de litellm.")
    import asyncio
    from types import SimpleNamespace

    from google.adk.models.lite_llm import LiteLlm

    from model_provider import _classe_litellm

    capturado = {}

    async def _fake(self, llm_request, stream=False):
        capturado["response_schema"] = llm_request.config.response_schema
        capturado["response_mime_type"] = llm_request.config.response_mime_type
        yield _resposta_falsa('{"ok": true}')

    monkeypatch.setattr(LiteLlm, "generate_content_async", _fake)

    modelo = _classe_litellm(structured_output=False)(model="openai/sabia-3")
    pedido = SimpleNamespace(
        config=SimpleNamespace(
            response_schema={"type": "object"}, response_mime_type="application/json"
        )
    )

    async def _consumir():
        return [r async for r in modelo.generate_content_async(pedido)]

    respostas = asyncio.run(_consumir())

    assert capturado["response_schema"] is None
    assert capturado["response_mime_type"] is None
    assert len(respostas) == 1


# ---------------------------------------------------------------------------
# Rótulo da execução e integração com o modo mock
# ---------------------------------------------------------------------------

def test_rotulo_identifica_provedor_e_modelo(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "maritaca")
    assert descricao_modelo() == "maritaca:sabiazinho-4"


def test_modo_mock_nao_anuncia_provedor_nem_exige_chave(monkeypatch):
    """No modo offline o relatório não deve alegar uma execução que não houve."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")  # sem OPENAI_API_KEY
    from pipeline import descrever_execucao

    assert descrever_execucao({"mode": "mock"}) == "mock (offline)"
    assert descrever_execucao({"mode": "api"}) == "openai:gpt-4o-mini"
