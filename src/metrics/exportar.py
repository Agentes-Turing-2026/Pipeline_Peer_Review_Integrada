"""Exportação do resumo de execução — Grupo 2.

Duas formas simples de visualizar um ResumoExecucao (resumo.py), como pede a
tarefa: tabela no terminal (imprimir_resumo) e arquivo JSON (salvar_resumo_json).
Sem dependências externas — só biblioteca padrão.
"""

from __future__ import annotations

import json
from pathlib import Path

from .resumo import ResumoExecucao

HERE = Path(__file__).resolve().parent      # .../src/metrics
SRC = HERE.parent                           # .../src
LARGURA = 74


def caminho_padrao_resumo() -> Path:
    """Caminho padrão do resumo de execução, ao lado de final_report.json."""
    return SRC / "outputs" / "resumo_execucao.json"


def _fmt_s(valor: float | None) -> str:
    """Formata uma duração em segundos para exibição no terminal.

    Só afeta exibição — o float completo continua sendo gravado sem
    transformação por salvar_resumo_json().
    """
    if valor is None:
        return "n/d"
    if valor == 0.0:
        return "0 s"
    if valor < 0.01:
        return "< 0.01 s"
    return f"{valor:.2f} s"


def imprimir_resumo(resumo: ResumoExecucao) -> None:
    """Imprime o resumo de execução como uma tabela simples no terminal."""
    print("=" * LARGURA)
    print(f"  RESUMO DA EXECUÇÃO — run_id={resumo.run_id}")
    print("=" * LARGURA)
    print(f"Status final:        {resumo.status_final}")
    print(f"Decisão final:       {resumo.decisao_final if resumo.decisao_final is not None else '—'}")
    print(f"Revisão humana:      {'RECOMENDADA' if resumo.requer_revisao_humana else 'não recomendada'}")
    print(f"Duração total:       {_fmt_s(resumo.duracao_total_s)}")

    print("\nDuração por fase:")
    if resumo.duracao_por_fase_s:
        for fase, duracao in resumo.duracao_por_fase_s.items():
            print(f"  {fase:<40} {_fmt_s(duracao)}")
    else:
        print("  (nenhuma fase registrada)")

    print(f"\nValidações: {resumo.quantidade_validacoes}    "
          f"Retries: {resumo.quantidade_retries}    "
          f"Falhas: {resumo.quantidade_falhas}")

    print("\nTools chamadas:")
    if resumo.quantidade_tools_chamadas:
        for nome, qtd in resumo.quantidade_tools_chamadas.items():
            print(f"  {nome:<40} {qtd}x")
    else:
        print("  (nenhuma tool registrada)")

    print("\nAlertas:")
    if resumo.alertas:
        for alerta in resumo.alertas:
            print(f"  - {alerta}")
    else:
        print("  (nenhum)")
    print("=" * LARGURA)


def salvar_resumo_json(resumo: ResumoExecucao, caminho: Path | None = None) -> Path:
    """Salva resumo.to_dict() em JSON. Usa caminho_padrao_resumo() se omitido.

    Cria os diretórios pais se não existirem. Retorna o Path efetivamente
    escrito.
    """
    destino = caminho or caminho_padrao_resumo()
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        json.dumps(resumo.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return destino
