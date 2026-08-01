"""Comparativo entre execuções de benchmark já registradas — Grupo 2.

Só lê o que ``executar.py`` já persistiu em ``resultados/execucoes.json`` —
não roda o pipeline, não recalcula métrica nenhuma. O bloco "atenção" é
sempre o primeiro a aparecer (impresso e no Markdown) porque é o que se
olha primeiro num lote grande: qualquer resultado diferente de "sucesso"
(inclui "sucesso_com_alertas", "entrada_bloqueada" e "falha_execucao") entra
nele, mesmo quando não houve exceção nenhuma — um "sucesso_com_alertas" é
silencioso o bastante para passar despercebido se não fosse destacado à
parte.

Uso:
    python -m src.benchmark.comparar --execucoes src/benchmark/resultados/execucoes.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent  # .../src/benchmark
RESULTADOS_DIR = HERE / "resultados"

CAMPOS_TABELA = (
    "doc_id",
    "titulo",
    "area",
    "caracteristicas",
    "mode",
    "timestamp",
    "run_id",
    "resultado",
    "decisao_final",
    "requer_revisao_humana",
    "duracao_total_s",
    "tokens_totais",
    "custo_estimado",
    "quantidade_tools_chamadas",
    "quantidade_retries",
    "quantidade_falhas",
    "alertas",
    "erro",
)


def gerar_comparativo(execucoes: dict) -> dict:
    """Monta o comparativo a partir do mapeamento doc_id -> registro.

    ``execucoes`` é o dict interno (``{"<doc_id>": {...registro...}}``), não o
    JSON completo de ``execucoes.json`` (que tem esse dict sob a chave
    "execucoes" — o chamador desembrulha antes de passar aqui).
    """
    atencao = sorted(
        doc_id
        for doc_id, registro in execucoes.items()
        if registro.get("resultado") != "sucesso"
    )
    return {
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "atencao": atencao,
        "tabela": dict(execucoes),
    }


def _sanitizar_celula(texto: str) -> str:
    return texto.replace("|", "\\|").replace("\n", " ")


def _valor_para_celula(valor) -> str:
    if valor is None:
        return "—"
    if isinstance(valor, bool):
        return _sanitizar_celula("sim" if valor else "não")
    if isinstance(valor, list):
        return _sanitizar_celula(", ".join(str(v) for v in valor) if valor else "(nenhum)")
    if isinstance(valor, dict):
        return _sanitizar_celula(
            "; ".join(f"{k}={v}" for k, v in valor.items()) if valor else "(nenhum)"
        )
    if isinstance(valor, float):
        return _sanitizar_celula(f"{valor:.4f}")
    return _sanitizar_celula(str(valor))


def _tabela_markdown(tabela: dict) -> str:
    linhas = [
        "| " + " | ".join(CAMPOS_TABELA) + " |",
        "|" + "|".join("---" for _ in CAMPOS_TABELA) + "|",
    ]
    for doc_id in sorted(tabela):
        registro = tabela[doc_id]
        celulas = [_valor_para_celula(registro.get(campo)) for campo in CAMPOS_TABELA]
        linhas.append("| " + " | ".join(celulas) + " |")
    return "\n".join(linhas)


def salvar_comparativo(comparativo: dict, destino_dir: Path = RESULTADOS_DIR) -> tuple[Path, Path]:
    """Grava comparativo.json (estrutura completa) e comparativo.md (tabela legível)."""
    destino_dir = Path(destino_dir)
    destino_dir.mkdir(parents=True, exist_ok=True)

    json_path = destino_dir / "comparativo.json"
    json_path.write_text(json.dumps(comparativo, indent=2, ensure_ascii=False), encoding="utf-8")

    linhas_atencao = (
        "\n".join(f"- {doc_id}" for doc_id in comparativo["atencao"])
        if comparativo["atencao"]
        else "Nenhum documento requer atenção — todos com resultado 'sucesso'."
    )
    conteudo_md = (
        "# Comparativo de execuções — benchmark (Grupo 2)\n\n"
        f"Gerado em: {comparativo['gerado_em']}\n\n"
        f"## \u26a0 Atenção\n\n{linhas_atencao}\n\n"
        f"## Tabela completa\n\n{_tabela_markdown(comparativo['tabela'])}\n"
    )
    md_path = destino_dir / "comparativo.md"
    md_path.write_text(conteudo_md, encoding="utf-8")

    return json_path, md_path


def imprimir_comparativo(comparativo: dict) -> None:
    """Versão terminal do comparativo, mesmo estilo de ``imprimir_resumo``."""
    largura = 74
    print("=" * largura)
    print("  COMPARATIVO DE EXECUÇÕES — BENCHMARK")
    print("=" * largura)
    print(f"Gerado em: {comparativo['gerado_em']}")

    print("\n⚠ Atenção:")
    if comparativo["atencao"]:
        for doc_id in comparativo["atencao"]:
            registro = comparativo["tabela"].get(doc_id, {})
            print(f"  - {doc_id:<20} resultado={registro.get('resultado')}")
    else:
        print("  (nenhum — todos com resultado 'sucesso')")

    print("\nTabela completa:")
    for doc_id in sorted(comparativo["tabela"]):
        registro = comparativo["tabela"][doc_id]
        print(
            f"  [{doc_id}] resultado={registro.get('resultado')}  "
            f"decisao={registro.get('decisao_final')}  "
            f"duracao_total_s={registro.get('duracao_total_s')}  "
            f"tokens_totais={registro.get('tokens_totais')}"
        )
    print("=" * largura)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gera o comparativo a partir das execuções já registradas pelo executar.py.",
    )
    parser.add_argument(
        "--execucoes", default=str(RESULTADOS_DIR / "execucoes.json"), metavar="CAMINHO",
        help="Caminho de execucoes.json gerado por executar.py (default: %(default)s).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    caminho = Path(args.execucoes)
    if not caminho.exists():
        raise SystemExit(
            f"Arquivo de execuções não encontrado: {caminho}. "
            "Rode 'python -m src.benchmark.executar' primeiro."
        )
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    execucoes = dados.get("execucoes", {})

    comparativo = gerar_comparativo(execucoes)
    imprimir_comparativo(comparativo)
    json_path, md_path = salvar_comparativo(comparativo, destino_dir=caminho.parent)
    print(f"\nComparativo salvo em: {json_path} e {md_path}")


if __name__ == "__main__":
    main()
