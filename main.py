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
    python main.py --resume run_abc123      # retoma uma execução interrompida (pula fases já concluídas)
    python main.py --resume run_abc123 --force  # refaz do zero uma execução já concluída (mesmo run_id)

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
    parser.add_argument(
        "--resume",
        dest="resume",
        default=None,
        metavar="RUN_ID",
        help="Retoma uma execução interrompida por run_id (reusa os checkpoints "
        "de src/logs/checkpoints/<run_id>/, pulando fases já concluídas, "
        "inclusive a extração do PDF). Sem --pdf/modo, reaproveita o "
        "pdf_path/mode salvos na execução original. Retomar uma execução JÁ "
        "concluída não re-executa nada e não regrava os artefatos.",
    )
    parser.add_argument(
        "--force",
        dest="force",
        action="store_true",
        help="Só com --resume: descarta os checkpoints e as métricas da execução "
        "e refaz TUDO sob o mesmo run_id, regravando os artefatos. É o jeito "
        "explícito de refazer uma execução já concluída.",
    )
    args = parser.parse_args(argv)
    if args.force and not args.resume:
        parser.error("--force só faz sentido junto com --resume <RUN_ID>.")
    return args


def main() -> None:
    """Roda o pipeline completo, com modo, PDF e retomada opcionais vindos da linha de comando."""
    args = _parse_args()
    try:
        run_demo(mode=args.mode, pdf_path=args.pdf, run_id=args.resume, forcar=args.force)
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
