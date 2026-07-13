"""Demo da base de observabilidade (Grupo 3) — reconstrói a linha do tempo.

Roda o pipeline completo no modo MOCK (offline, sem GOOGLE_API_KEY) e, ao final,
RECONSTRÓI a execução em ordem a partir do trace gravado — respondendo
"por onde a execução passou?": quais fases rodaram, quais agentes participaram,
o que deu certo, onde apareceu alerta e quais arquivos foram gerados.

Uso:
    python src/demo_observabilidade.py            # modo mock (padrão)
    python src/demo_observabilidade.py api        # usa o Gemini real (requer chave)
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from observability import render_timeline  # noqa: E402
from pipeline import run_demo  # noqa: E402


def main() -> None:
    modo = sys.argv[1] if len(sys.argv) > 1 else "mock"

    print("=" * 72)
    print(f"DEMO OBSERVABILIDADE — executando o pipeline [modo={modo}]")
    print("=" * 72)

    report = run_demo(mode=modo)

    run_id = report.data.get("run_id")
    if not run_id:
        print("\n(Observabilidade indisponível: nenhum run_id foi gerado.)")
        return

    trace_path = HERE / "logs" / "traces" / f"{run_id}.jsonl"

    print("\n" + "=" * 72)
    print(f"LINHA DO TEMPO RECONSTRUÍDA — run_id={run_id}")
    print(f"(fonte: {trace_path})")
    print("=" * 72)
    print(render_timeline(trace_path))
    print("=" * 72)
    print(
        "Legenda: <kind> nome (autor) [duração] · eventos pontuais.\n"
        "Status: ✓ ok · ! alerta · ✗ erro · … em andamento."
    )


if __name__ == "__main__":
    main()
