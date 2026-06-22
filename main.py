"""Ponto de entrada único do repositório: roda TODO o pipeline de peer review.

Este script executa as quatro fases em sequência (revisão independente, leitura
cruzada, editor-chefe e relatório final) chamando a demo de ``src/pipeline.py``.

Uso:
    python main.py            # modo padrão: PIPELINE_MODE (env) ou 'api'
    python main.py mock       # offline, lendo os JSONs locais (sem chave/internet)
    python main.py api        # chamadas reais ao Gemini (requer GOOGLE_API_KEY)

O modo também pode ser definido pela variável de ambiente ``PIPELINE_MODE``; a
flag de linha de comando tem precedência sobre ela.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Garante que os módulos do pacote em ``src/`` sejam importáveis ao rodar a
# partir de qualquer diretório.
SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pipeline import run_demo  # noqa: E402


def main() -> None:
    """Roda o pipeline completo, com o modo opcionalmente vindo da linha de comando."""
    mode = sys.argv[1] if len(sys.argv) > 1 else None
    run_demo(mode=mode)


if __name__ == "__main__":
    main()
