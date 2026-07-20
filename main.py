"""Ponto de entrada único do repositório: roda TODO o pipeline de peer review.

Este script executa a extração (quando a entrada é um PDF real) e as quatro
fases em sequência (revisão independente, leitura cruzada, editor-chefe e
relatório final) chamando ``run_demo`` de ``src/pipeline.py``.

Uso:
    python main.py                          # modo padrão: PIPELINE_MODE (env) ou 'api'
    python main.py mock                     # offline, lendo os JSONs locais (sem chave/internet)
    python main.py api                      # artigo de exemplo com Gemini real (requer GOOGLE_API_KEY)
    python main.py --pdf caminho/artigo.pdf # PDF REAL: extração + execução completa com ADK
    python main.py mock --pdf artigo.pdf    # extração real do PDF + fases com respostas mock

O modo também pode ser definido pela variável de ambiente ``PIPELINE_MODE``; a
flag de linha de comando tem precedência sobre ela.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Garante que os módulos do pacote em ``src/`` sejam importáveis ao rodar a
# partir de qualquer diretório.
SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pipeline import run_demo  # noqa: E402
from validacao_entrada import EntradaInvalidaError  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pipeline multiagente de peer review (entrada por PDF real ou artigo de exemplo).",
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default=None,
        help="Modo de execução: 'api' (Gemini real) ou 'mock' (offline). "
        "Default: variável de ambiente PIPELINE_MODE ou 'api'.",
    )
    parser.add_argument(
        "--pdf",
        dest="pdf",
        default=None,
        metavar="CAMINHO",
        help="Caminho de um PDF real a revisar. Sem esta flag, usa o artigo de "
        "exemplo em src/examples/example_article.txt.",
    )
    return parser.parse_args(argv)


def main() -> None:
    """Roda o pipeline completo, com modo e PDF opcionais vindos da linha de comando."""
    args = _parse_args()
    try:
        run_demo(mode=args.mode, pdf_path=args.pdf)
    except EntradaInvalidaError as exc:
        # Entrada bloqueada pela validação do Grupo 1: mensagem clara (não um
        # traceback), e código de saída != 0 — a falha não passa despercebida.
        print(f"\n[ENTRADA BLOQUEADA] {exc}", file=sys.stderr)
        print(
            "O documento não entra no pipeline. Verifique o arquivo/extração e "
            "consulte src/logs/validacao_events.jsonl para o evento registrado.",
            file=sys.stderr,
        )
        raise SystemExit(2)


if __name__ == "__main__":
    main()
