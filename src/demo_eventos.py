"""Demo offline dos eventos estruturados de validação/retry (Grupo 1).

Roda sem internet e sem GOOGLE_API_KEY. Reaproveita os mesmos casos de
``demo_validacao.py`` (sucesso, falha, retry com correção e esgotamento), mas
sob um único ``run_id`` compartilhado — simulando o que aconteceria dentro de
uma execução real do pipeline — e depois lê de volta o arquivo de eventos
(``src/logs/validacao_events.jsonl``) para mostrar como interpretar o
histórico de uma execução.

Uso:
    python src/demo_eventos.py

O que este script demonstra, na ordem do critério de sucesso do PDF do
Grupo 1:
  1. É possível abrir o arquivo de eventos e entender o histórico de validação
     de uma execução (função ``formatar_linha_do_tempo``).
  2. Fica claro quando houve sucesso, falha, correção, retry ou bloqueio
     (campo ``categoria`` de cada evento).
  3. O pipeline não aceita erro silenciosamente (o caso de esgotamento
     continua levantando ``PipelineValidationError``).
  4. Os eventos estão ligados a um identificador de execução comum (``run_id``),
     prontos para aparecer na linha do tempo geral do pipeline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from eventos_validacao import (  # noqa: E402
    formatar_linha_do_tempo,
    gerar_run_id,
    ler_eventos,
)
from review_schema import validar_cross_review, validar_editor_verdict, validar_review  # noqa: E402
from validacao_retry import PipelineValidationError, validar_com_tentativas  # noqa: E402

EXAMPLES = HERE / "examples"
LARGURA = 70


class _ModoMock:
    """Simula RunMode.MOCK sem importar pipeline.py (que precisa de ADK)."""

    value = "mock"


MODO_MOCK = _ModoMock()


def _sep(titulo: str = "") -> None:
    if titulo:
        print(f"\n{'=' * LARGURA}")
        print(f"  {titulo}")
        print("=" * LARGURA)
    else:
        print("-" * LARGURA)


def _carregar(nome: str) -> dict:
    return json.loads((EXAMPLES / nome).read_text(encoding="utf-8"))


def main() -> None:
    run_id = gerar_run_id()

    print("=" * LARGURA)
    print("  DEMO: Eventos Estruturados de Validação/Retry — Grupo 1")
    print(f"  run_id da execução: {run_id}")
    print("  (offline — sem GOOGLE_API_KEY · sem internet)")
    print("=" * LARGURA)

    _sep("CENÁRIO 1 — ReviewSchema válido, passa de primeira")
    validar_com_tentativas(
        _carregar("example_valid_output.json"), validar_review, MODO_MOCK,
        "statistician", run_id=run_id, fase="demo_caso_1_sucesso",
    )
    print("  OK — evento categoria=passou_de_primeira emitido.")

    _sep("CENÁRIO 2 — ReviewSchema inválido, corrigido no retry")
    validar_com_tentativas(
        _carregar("example_invalid_output.json"), validar_review, MODO_MOCK,
        "domain_expert", run_id=run_id, fase="demo_caso_2_retry",
    )
    print("  OK — eventos falhou_recuperavel -> corrigido -> passou_apos_correcao emitidos.")

    _sep("CENÁRIO 3 — ReviewSchema com campos ausentes, esgota tentativas")
    dados_patologicos = {"revisor": "copyeditor"}  # faltam todos os campos de nota
    try:
        validar_com_tentativas(
            dados_patologicos, validar_review, MODO_MOCK, "copyeditor",
            run_id=run_id, fase="demo_caso_3_bloqueio",
        )
    except PipelineValidationError:
        print("  OK — PipelineValidationError capturada; evento categoria=bloqueado emitido.")

    _sep("CENÁRIO 4 — CrossReviewSchema e EditorVerdictSchema válidos (mesma execução)")
    dados_cross = _carregar("example_cross_review_output.json")["statistician_cross_review"]
    validar_com_tentativas(
        dados_cross, validar_cross_review, MODO_MOCK, "statistician",
        run_id=run_id, fase="demo_caso_4_cross_review",
    )
    validar_com_tentativas(
        _carregar("example_editor_verdict_output.json"), validar_editor_verdict, MODO_MOCK,
        "editor", run_id=run_id, fase="demo_caso_4_editor_verdict",
    )
    print("  OK — eventos passou_de_primeira emitidos para as duas fases.")

    _sep("LINHA DO TEMPO — lida de volta do arquivo de eventos")
    eventos = ler_eventos(run_id=run_id)
    print(f"  {len(eventos)} evento(s) desta execução (run_id={run_id[:8]}…):\n")
    print(formatar_linha_do_tempo(eventos))

    _sep()
    print(f"  Arquivo de eventos: {HERE / 'logs' / 'validacao_events.jsonl'}")
    print("  Cada linha é um JSON independente — pode ser lido com jq, grep,")
    print("  pandas, ou por qualquer outro grupo/ferramenta de métricas/trace.")
    print("  Demo concluída.")
    print("-" * LARGURA)


if __name__ == "__main__":
    main()
