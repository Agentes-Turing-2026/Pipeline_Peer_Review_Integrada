"""Testes da tabela de preços oficiais e da resolução de custo (Grupo 2).

100% offline: nenhuma chamada de rede. ``resolver_precos`` depende de
``model_provider.resolver_config``, que só decide o provedor/modelo a partir de
config/variáveis de ambiente — não faz nenhuma chamada real.
"""

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from metrics.adk_usage import PrecosModelo, VAR_PRECO_ENTRADA, VAR_PRECO_SAIDA  # noqa: E402
from metrics.precos_modelos import (  # noqa: E402
    _preco_via_litellm,
    fonte_oficial,
    preco_oficial,
    resolver_precos,
)

# ``resolver_precos`` importa ``model_provider`` (e ``_preco_via_litellm``
# importa ``litellm``) de forma PREGUIÇOSA — só na primeira chamada. Os dois
# módulos chamam ``load_dotenv()`` na própria importação. Se essa primeira
# importação acontecesse DENTRO de um teste (depois do fixture
# ``ambiente_limpo`` já ter limpado o ambiente), um ``.env`` real no
# diretório de trabalho repovoaria ``LLM_PROVIDER``/``LLM_MODEL`` que o
# fixture acabou de remover — fragilidade real, não só deste arquivo de
# teste: qualquer dev com um ``.env`` de verdade para testar o modo API
# hitaria isso. Importar aqui, na coleta (antes de qualquer fixture rodar),
# garante que o ``load_dotenv()`` de cada módulo dispare só uma vez, cedo,
# e que o ``monkeypatch.delenv`` de cada teste realmente valha depois.
import model_provider  # noqa: E402,F401
try:
    import litellm  # noqa: E402,F401
except ImportError:
    pass

#: Mesma lista de test_model_provider.py: precisa sair do ambiente do teste
#: para não herdar configuração do .env/shell de quem roda os testes.
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
    VAR_PRECO_ENTRADA,
    VAR_PRECO_SAIDA,
]


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch):
    for nome in _VARIAVEIS:
        monkeypatch.delenv(nome, raising=False)


# ---------------------------------------------------------------------------
# preco_oficial / fonte_oficial
# ---------------------------------------------------------------------------

def test_preco_oficial_casa_modelo_exato():
    precos = preco_oficial("gemini", "gemini-2.5-flash")
    assert precos is not None
    assert precos.usd_por_milhao_tokens_entrada == pytest.approx(0.30)
    assert precos.usd_por_milhao_tokens_saida == pytest.approx(2.50)


def test_preco_oficial_casa_por_prefixo_com_sufixo_de_build():
    # model_version do ADK pode trazer sufixo de data/build.
    precos = preco_oficial("gemini", "gemini-2.5-flash-001")
    assert precos == preco_oficial("gemini", "gemini-2.5-flash")


def test_preco_oficial_e_case_insensitive():
    assert preco_oficial("GEMINI", "Gemini-2.5-Flash") == preco_oficial("gemini", "gemini-2.5-flash")


def test_preco_oficial_modelo_desconhecido_devolve_none():
    assert preco_oficial("gemini", "gemini-9.9-ultra-desconhecido") is None


def test_preco_oficial_provedor_desconhecido_devolve_none():
    assert preco_oficial("azure", "gemini-2.5-flash") is None


def test_preco_oficial_sem_modelo_devolve_none():
    assert preco_oficial("gemini", None) is None
    assert preco_oficial("gemini", "") is None


def test_preco_oficial_cobre_os_tres_provedores_e_o_default_de_cada_um():
    # Os defaults de model_provider.PROVEDORES precisam ter preço na tabela,
    # senão rodar sem LLM_MODEL explícito nunca produziria custo_estimado.
    assert preco_oficial("gemini", "gemini-2.5-flash") is not None
    assert preco_oficial("openai", "gpt-4o-mini") is not None
    assert preco_oficial("maritaca", "sabia-3") is not None


def test_fonte_oficial_acompanha_preco_oficial():
    assert fonte_oficial("gemini", "gemini-2.5-flash") is not None
    assert "ai.google.dev" in fonte_oficial("gemini", "gemini-2.5-flash")
    assert fonte_oficial("gemini", "modelo-desconhecido") is None


# ---------------------------------------------------------------------------
# resolver_precos: precedência ambiente > litellm > tabela oficial > None
#
# Nota: com litellm instalado (é o caso deste venv de teste), os testes
# abaixo que comparam contra preco_oficial(...) só passam porque os valores
# de litellm.model_cost e da tabela estática BATEM para gemini-2.5-flash e
# gpt-4o (conferido manualmente) — não porque o teste força um dos dois
# caminhos. A seção seguinte testa cada caminho de forma explícita.
# ---------------------------------------------------------------------------

def test_resolver_precos_sem_nada_configurado_usa_default_gemini():
    precos = resolver_precos()
    assert precos == preco_oficial("gemini", "gemini-2.5-flash")


def test_resolver_precos_respeita_provider_e_model_explicitos_na_config():
    precos = resolver_precos({"provider": "openai", "model": "gpt-4o"})
    assert precos == preco_oficial("openai", "gpt-4o")


def test_resolver_precos_modelo_fora_da_tabela_devolve_none():
    precos = resolver_precos({"provider": "openai", "model": "modelo-inedito-sem-preco"})
    assert precos is None


def test_resolver_precos_variavel_de_ambiente_vence_tudo(monkeypatch):
    monkeypatch.setenv(VAR_PRECO_ENTRADA, "9.99")
    monkeypatch.setenv(VAR_PRECO_SAIDA, "8.88")
    precos = resolver_precos({"provider": "gemini", "model": "gemini-2.5-flash"})
    assert precos is not None
    assert precos.usd_por_milhao_tokens_entrada == pytest.approx(9.99)
    assert precos.usd_por_milhao_tokens_saida == pytest.approx(8.88)


def test_resolver_precos_ambiente_parcial_nao_conta_como_configurado(monkeypatch):
    # As DUAS variáveis precisam estar presentes (mesma regra de
    # precos_de_ambiente); só uma delas não deve suprimir o fallback.
    monkeypatch.setenv(VAR_PRECO_ENTRADA, "9.99")
    precos = resolver_precos({"provider": "gemini", "model": "gemini-2.5-flash"})
    assert precos == preco_oficial("gemini", "gemini-2.5-flash")


# ---------------------------------------------------------------------------
# _preco_via_litellm e a camada intermediária de resolver_precos
# ---------------------------------------------------------------------------

def test_preco_via_litellm_sem_pacote_devolve_none(monkeypatch):
    """Simula ausência do pacote (setup só-Gemini/mock não instala litellm)."""
    import builtins

    importar_de_verdade = builtins.__import__

    def importar_falso(nome, *args, **kwargs):
        if nome == "litellm":
            raise ImportError("simulado: litellm não instalado")
        return importar_de_verdade(nome, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importar_falso)
    assert _preco_via_litellm("gemini", "gemini-2.5-flash") is None


def test_preco_via_litellm_casa_pelo_nome_puro_do_modelo():
    pytest.importorskip("litellm", reason="tabela de preços do litellm não instalada.")
    precos = _preco_via_litellm("gemini", "gemini-2.5-flash")
    assert precos is not None
    # Valor conferido manualmente contra litellm.model_cost em 2026-08-06 —
    # bate com a tabela oficial estática (ver TABELA_PRECOS_OFICIAIS).
    assert precos.usd_por_milhao_tokens_entrada == pytest.approx(0.30)
    assert precos.usd_por_milhao_tokens_saida == pytest.approx(2.50)


def test_preco_via_litellm_maritaca_nao_tem_cobertura():
    pytest.importorskip("litellm", reason="tabela de preços do litellm não instalada.")
    # litellm.model_cost não tem NENHUMA entrada "sabia*"/"maritaca*" — nem
    # pelo nome puro, nem pelo prefixo "openai/" que a Maritaca reaproveita.
    assert _preco_via_litellm("maritaca", "sabia-3") is None
    assert _preco_via_litellm("maritaca", "sabia-4") is None


def test_resolver_precos_maritaca_cai_para_tabela_oficial_mesmo_com_litellm_instalado():
    pytest.importorskip("litellm", reason="tabela de preços do litellm não instalada.")
    precos = resolver_precos({"provider": "maritaca", "model": "sabia-3"})
    assert precos == preco_oficial("maritaca", "sabia-3")


def test_resolver_precos_cai_para_tabela_oficial_quando_litellm_nao_acha_o_modelo(monkeypatch):
    """Modelo reconhecido pela tabela oficial, mas ausente do litellm."""
    import metrics.precos_modelos as precos_modelos

    monkeypatch.setattr(precos_modelos, "_preco_via_litellm", lambda provider, modelo: None)
    precos = precos_modelos.resolver_precos({"provider": "gemini", "model": "gemini-2.5-flash"})
    assert precos == preco_oficial("gemini", "gemini-2.5-flash")


def test_resolver_precos_prefere_litellm_quando_diverge_da_tabela_oficial(monkeypatch):
    """Se litellm e a tabela oficial divergissem, litellm venceria (é a fonte mais viva)."""
    import metrics.precos_modelos as precos_modelos

    preco_falso = PrecosModelo(usd_por_milhao_tokens_entrada=123.0, usd_por_milhao_tokens_saida=456.0)
    monkeypatch.setattr(precos_modelos, "_preco_via_litellm", lambda provider, modelo: preco_falso)
    precos = precos_modelos.resolver_precos({"provider": "gemini", "model": "gemini-2.5-flash"})
    assert precos == preco_falso
    assert precos != preco_oficial("gemini", "gemini-2.5-flash")
