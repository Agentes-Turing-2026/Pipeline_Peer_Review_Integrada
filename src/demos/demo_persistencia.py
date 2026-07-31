"""Demo offline de persistência e retomada de execuções (Grupo 1).

Roda sem internet e sem GOOGLE_API_KEY (modo mock): mostra o ciclo completo
que a atividade pede para demonstrar —

  1. Uma execução que FALHA no meio (fase 3): os checkpoints das fases 1 e 2
     ficam salvos em disco, e um resumo de falha (o que terminou, onde
     parou, quais artefatos existem) é impresso.
  2. A MESMA execução, retomada pelo run_id: as fases 1 e 2 são carregadas
     do checkpoint (não rodam de novo) — só a fase 3 em diante roda de
     verdade, e a execução conclui normalmente.

Uso:
    python src/demos/demo_persistencia.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]  # .../src (as demos vivem em src/demos/)
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pipeline import EditorVerdictPhase, run_demo  # noqa: E402

LARGURA = 70
DEMO_RUN_ID = "demo_persistencia_grupo1"


def _sep(titulo: str = "") -> None:
    if titulo:
        print(f"\n{'=' * LARGURA}")
        print(f"  {titulo}")
        print("=" * LARGURA)
    else:
        print("-" * LARGURA)


def _limpar_execucao_anterior() -> None:
    """Remove checkpoints de uma rodada anterior desta demo, para ela ser
    reproduzível (sempre falha e retoma do zero, em vez de já achar tudo
    concluído de uma execução passada com o mesmo run_id fixo)."""
    ckpt_dir = SRC / "logs" / "checkpoints" / DEMO_RUN_ID
    if ckpt_dir.exists():
        shutil.rmtree(ckpt_dir)
    meta_path = SRC / "logs" / "checkpoints" / f"{DEMO_RUN_ID}.meta.json"
    if meta_path.exists():
        meta_path.unlink()


def main() -> None:
    print("=" * LARGURA)
    print("  DEMO: Persistência e Retomada de Execuções — Grupo 1")
    print(f"  run_id da demonstração: {DEMO_RUN_ID}")
    print("  (offline — modo mock, sem GOOGLE_API_KEY, sem internet)")
    print("=" * LARGURA)

    _limpar_execucao_anterior()

    # -------------------------------------------------------------------
    _sep("PASSO 1 — Execução que FALHA no meio (fase 3)")
    print(
        "Simulando uma falha real na fase_3_editor_chefe (ex.: timeout, erro\n"
        "de rede, resposta inválida do modelo) — a fase 3 é substituída para\n"
        "sempre levantar uma exceção nesta demo."
    )
    print()

    original_run = EditorVerdictPhase.run

    def _falha_simulada(self, data, context):
        raise RuntimeError("falha simulada pela demo (ex.: timeout do modelo)")

    EditorVerdictPhase.run = _falha_simulada
    try:
        run_demo(mode="mock", run_id=DEMO_RUN_ID)
    except RuntimeError as exc:
        print(f"\n>>> Falha capturada como esperado: {exc}")
        print(">>> Repare acima: 'Fases concluídas antes da falha' lista fase 1 e 2,")
        print(">>> com o caminho de cada checkpoint salvo em disco.")
    finally:
        # Restaura o comportamento normal da fase 3 antes do PASSO 2 —
        # simula o problema real (ex.: o timeout) ter sido resolvido.
        EditorVerdictPhase.run = original_run

    # -------------------------------------------------------------------
    _sep("PASSO 2 — Retomando a MESMA execução (mesmo run_id)")
    print(
        "O problema que causou a falha foi resolvido (fase 3 voltou ao normal).\n"
        "Retomando com o mesmo run_id — fases 1 e 2 devem ser puladas."
    )
    print()

    report = run_demo(mode="mock", run_id=DEMO_RUN_ID)

    # -------------------------------------------------------------------
    _sep("RESULTADO")
    ckpt_dir = SRC / "logs" / "checkpoints" / DEMO_RUN_ID
    arquivos = sorted(p.name for p in ckpt_dir.glob("*.json"))
    print(f"  Checkpoints salvos em {ckpt_dir}:")
    for nome in arquivos:
        print(f"    - {nome}")
    print(f"\n  Decisão final do relatório: {report.data.get('decisao')}")
    print("  A execução concluiu sem repetir as fases 1 e 2 (veja acima:")
    print("  '(checkpoint restaurado)' nas fases 1 e 2 do PASSO 2).")
    print("-" * LARGURA)
    print("Demo concluída.")


if __name__ == "__main__":
    main()
