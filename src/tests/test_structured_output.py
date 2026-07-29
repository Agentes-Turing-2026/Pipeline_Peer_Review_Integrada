"""Saída estruturada e observabilidade em provedor NÃO-Gemini, ponta a ponta.

Exercita o caminho real da Fase 1 — ``LlmAgent`` + ``ParallelAgent`` +
``InMemoryRunner`` do ADK, com ``output_schema`` e ``output_key`` — trocando
apenas a chamada de rede por uma resposta canned. Ou seja: nada de mock do nosso
pipeline, só o provedor é falso.

Isso responde a duas perguntas que os testes unitários não alcançam:

1. o parecer continua saindo VALIDADO pelo schema quando o provedor não tem
   ``response_format: json_schema`` (o caso da Maritaca), inclusive quando ele
   embrulha o JSON num bloco markdown;
2. provedor, modelo e consumo de tokens chegam ao trace.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1]  # .../src
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

pytest.importorskip("litellm", reason="Maritaca/OpenAI dependem de litellm.")

from observability import resumir_tokens  # noqa: E402
from observability.tracer import MemoryExporter, Tracer  # noqa: E402
from review_schema import validar_review  # noqa: E402

#: Contadores que o provedor "reporta" na resposta falsa.
TOKENS_ENTRADA = 1200
TOKENS_SAIDA = 300
MODELO_RESPONDEU = "sabia-3"


def _parecer_valido() -> dict:
    """Um ``ReviewSchema`` válido, reaproveitado dos exemplos versionados."""
    caminho = SRC / "examples" / "example_valid_output.json"
    return json.loads(caminho.read_text(encoding="utf-8"))


def _resposta_canned(texto: str):
    """Monta um ``LlmResponse`` do ADK como um provedor real devolveria."""
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part.from_text(text=texto)]),
        model_version=MODELO_RESPONDEU,
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=TOKENS_ENTRADA,
            candidates_token_count=TOKENS_SAIDA,
            total_token_count=TOKENS_ENTRADA + TOKENS_SAIDA,
        ),
    )


@pytest.fixture
def maritaca_falsa(monkeypatch):
    """Aponta o pipeline para a Maritaca e substitui só a ida à rede.

    A resposta vem embrulhada em ```` ```json ```` de propósito: é exatamente o
    que um provedor sem structured output nativo tende a devolver, e o pipeline
    precisa continuar entregando um parecer válido.
    """
    for nome in ("LLM_MODEL", "LLM_PROVIDER_EDITOR", "LLM_MODEL_EDITOR", "LLM_JSON_SCHEMA"):
        monkeypatch.delenv(nome, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "maritaca")
    monkeypatch.setenv("MARITACA_API_KEY", "chave-de-teste")

    from google.adk.models.lite_llm import LiteLlm

    pedidos: list = []
    payload = json.dumps(_parecer_valido(), ensure_ascii=False)

    async def _fake_generate(self, llm_request, stream=False):
        pedidos.append(llm_request)
        yield _resposta_canned(f"```json\n{payload}\n```")

    monkeypatch.setattr(LiteLlm, "generate_content_async", _fake_generate)
    return pedidos


def _rodar_fase_1(article_text: str = "Artigo de teste."):
    """Roda a Fase 1 real dentro de um tracer em memória."""
    import reviewer_agent

    memoria = MemoryExporter()
    tracer = Tracer(memoria)
    with tracer.run(name="teste_estruturado", attributes={"modo": "api"}):
        state = asyncio.run(reviewer_agent._run_reviewers(article_text))
    return state, memoria.events


# ---------------------------------------------------------------------------
# Saída estruturada
# ---------------------------------------------------------------------------

def test_parecer_sai_validado_mesmo_sem_json_schema_nativo(maritaca_falsa):
    """O contrato do schema se mantém sem structured output nativo."""
    import reviewer_agent

    state, _ = _rodar_fase_1()

    for reviewer_id, cfg in reviewer_agent.REVIEWERS.items():
        bruto = state.get(cfg["output_key"])
        assert bruto is not None, f"revisor '{reviewer_id}' não gravou no state"
        dados = bruto if isinstance(bruto, dict) else json.loads(bruto)
        # Passa pela MESMA validação oficial usada pelo pipeline.
        parecer = validar_review(dados)
        assert 1 <= parecer.nota_geral.nota <= 4
        assert 1 <= parecer.confianca.nota <= 3


def test_response_schema_nao_e_enviado_a_provedor_que_nao_suporta(maritaca_falsa):
    """A Maritaca não deve receber um parâmetro que não entende."""
    pedidos = maritaca_falsa
    _rodar_fase_1()

    assert pedidos, "o provedor falso não chegou a ser chamado"
    for pedido in pedidos:
        assert pedido.config.response_schema is None
        assert pedido.config.response_mime_type is None


def test_com_json_schema_ligado_o_schema_vai_no_pedido(maritaca_falsa, monkeypatch):
    """Com LLM_JSON_SCHEMA=on, o structured output nativo volta a ser enviado.

    E o parecer continua saindo válido mesmo se o provedor, apesar de aceitar o
    parâmetro, devolver o JSON dentro de um bloco markdown.
    """
    import reviewer_agent

    monkeypatch.setenv("LLM_JSON_SCHEMA", "on")
    pedidos = maritaca_falsa
    state, _ = _rodar_fase_1()

    assert pedidos
    assert all(p.config.response_schema is not None for p in pedidos)
    for cfg in reviewer_agent.REVIEWERS.values():
        validar_review(state[cfg["output_key"]])


# ---------------------------------------------------------------------------
# Observabilidade: provedor, modelo e tokens
# ---------------------------------------------------------------------------

def test_trace_registra_provedor_modelo_e_tokens(maritaca_falsa):
    _, eventos = _rodar_fase_1()

    com_tokens = [e for e in eventos if "tokens_entrada" in (e.attributes or {})]
    assert com_tokens, "nenhum evento do trace registrou consumo de tokens"

    for evento in com_tokens:
        assert evento.attributes["provedor"] == "maritaca"
        assert evento.attributes["modelo"] == MODELO_RESPONDEU
        assert evento.attributes["tokens_entrada"] == TOKENS_ENTRADA
        assert evento.attributes["tokens_saida"] == TOKENS_SAIDA
        assert evento.phase == "fase_1_revisao_independente"


def test_resumo_de_tokens_soma_os_tres_revisores(maritaca_falsa):
    import reviewer_agent

    _, eventos = _rodar_fase_1()
    resumo = resumir_tokens(eventos)
    revisores = len(reviewer_agent.REVIEWERS)

    assert resumo["medido"] is True
    assert resumo["totais"]["tokens_entrada"] == TOKENS_ENTRADA * revisores
    assert resumo["totais"]["tokens_saida"] == TOKENS_SAIDA * revisores
    assert resumo["totais"]["tokens_total"] == (TOKENS_ENTRADA + TOKENS_SAIDA) * revisores
    assert MODELO_RESPONDEU in resumo["por_modelo"]


def test_execucao_sem_medicao_nao_inventa_zeros():
    """Modo mock não chama provedor nenhum: 'não medido' != 'consumiu zero'."""
    from observability.events import TraceEvent

    eventos = [TraceEvent(run_id="r1", event_type="log", name="qualquer")]
    assert resumir_tokens(eventos) == {"medido": False}
