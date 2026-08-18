"""Script manual de validação: custo por chamada com modelos diferentes na
mesma execução (editor com modelo próprio / fallback acionado).

Uso:
    uv run python scripts/validar_custo_por_chamada.py

Não precisa de chave de API nem de ADK real — simula os eventos como o ADK
os emitiria e mostra o custo calculado pelo pipeline atual.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from metrics.coletor import ExecutionCollector
from metrics.adk_usage import definir_coletor_adk, registrar_usage_adk
from metrics.resumo import gerar_resumo
from metrics.precos_modelos import preco_oficial


def usage(prompt, candidates, total):
    return SimpleNamespace(
        prompt_token_count=prompt,
        candidates_token_count=candidates,
        thoughts_token_count=None,
        cached_content_token_count=None,
        total_token_count=total,
    )


def evento(event_id, author, model_version, u):
    return SimpleNamespace(
        author=author,
        invocation_id="inv",
        id=event_id,
        partial=False,
        content=SimpleNamespace(parts=[SimpleNamespace(text="x")]),
        usage_metadata=u,
        model_version=model_version,
    )


coletor = ExecutionCollector(run_id="run-validacao")
definir_coletor_adk(coletor)

# Cenário: revisores usam o modelo padrão (gemini-2.5-flash); o editor está
# configurado com um modelo diferente (gpt-4o-mini) — ex.: LLM_MODEL_EDITOR.
# Ajuste os valores abaixo para testar outros cenários (ex.: simular um
# fallback trocando o model_version de UMA chamada específica).
registrar_usage_adk(
    evento("e1", "revisor_metodologia", "gemini-2.5-flash", usage(1_000_000, 1_000_000, 2_000_000)),
    fase="fase_1_revisao_independente",
)
registrar_usage_adk(
    evento("e2", "revisor_clareza", "gemini-2.5-flash", usage(500_000, 200_000, 700_000)),
    fase="fase_1_revisao_independente",
)
registrar_usage_adk(
    evento("e3", "editor_chefe", "gpt-4o-mini", usage(1_000_000, 1_000_000, 2_000_000)),
    fase="fase_3_editor_chefe",
)

resumo = gerar_resumo(coletor.eventos, run_id=coletor.run_id)

preco_gemini = preco_oficial("gemini", "gemini-2.5-flash")
preco_gpt = preco_oficial("openai", "gpt-4o-mini")

custo_esperado_por_chamada = (
    (1_000_000 / 1e6 * preco_gemini.usd_por_milhao_tokens_entrada
     + 1_000_000 / 1e6 * preco_gemini.usd_por_milhao_tokens_saida)
    + (500_000 / 1e6 * preco_gemini.usd_por_milhao_tokens_entrada
       + 200_000 / 1e6 * preco_gemini.usd_por_milhao_tokens_saida)
    + (1_000_000 / 1e6 * preco_gpt.usd_por_milhao_tokens_entrada
       + 1_000_000 / 1e6 * preco_gpt.usd_por_milhao_tokens_saida)
)

print(f"modelo_usado (deve listar os dois): {resumo.modelo_usado}")
print(f"custo_estimado (calculado pelo pipeline): {resumo.custo_estimado}")
print(f"custo esperado (somado à mão, por chamada): {custo_esperado_por_chamada}")
print(f"bate? {abs(resumo.custo_estimado - custo_esperado_por_chamada) < 1e-9}")
