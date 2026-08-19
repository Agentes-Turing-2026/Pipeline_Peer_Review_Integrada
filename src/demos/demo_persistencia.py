"""Demo offline de persistência e retomada de execuções (Grupo 1).

Roda sem internet e sem GOOGLE_API_KEY (modo mock): mostra o ciclo completo
que a atividade pede para demonstrar —

  1. Uma execução que FALHA no meio (fase 3): os checkpoints das fases 1 e 2
     ficam salvos em disco, e um resumo de falha (o que terminou, onde
     parou, quais artefatos existem) é impresso.
  2. A MESMA execução, retomada pelo run_id: as fases 1 e 2 são carregadas
     do checkpoint (não rodam de novo) — só a fase 3 em diante roda de
     verdade, e a execução conclui normalmente. O resumo final cobre as
     QUATRO fases, e não só as que rodaram depois da falha.
  3. Uma retomada da execução JÁ concluída: nada é re-executado e nenhum
     artefato é regravado (os arquivos continuam byte a byte iguais).

Uso:
    python src/demos/demo_persistencia.py
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]  # .../src (as demos vivem em src/demos/)
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from persistencia import CheckpointManager  # noqa: E402
from pipeline import EditorVerdictPhase, _fase_medida, run_demo  # noqa: E402

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
    """Remove TUDO que uma rodada anterior desta demo deixou para trás.

    O run_id é fixo, então a demo precisa começar do zero toda vez (sempre falha
    e retoma, em vez de já achar tudo concluído). Apagar só os checkpoints não
    basta: o sidecar de estado sobreviveria, e a rodada nova restauraria as
    métricas da rodada passada por cima das suas — dobrando as contagens do
    resumo. É o mesmo motivo pelo qual ``--force`` descarta checkpoints e
    métricas juntos.
    """
    ckpt_raiz = SRC / "logs" / "checkpoints"
    ckpt_dir = ckpt_raiz / DEMO_RUN_ID
    if ckpt_dir.exists():
        shutil.rmtree(ckpt_dir)
    # Pega meta, estado e eventuais restos de .corrompido de uma vez.
    for sidecar in ckpt_raiz.glob(f"{DEMO_RUN_ID}.*"):
        sidecar.unlink()


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
        # Passa por _fase_medida() — o MESMO wrapper que o `.run()` original
        # usa — em vez de chamar coletor.fase() diretamente: sem isso, a
        # exceção não vira evento de métrica completo (nem o resumo mostraria
        # "status: falha" corretamente, nem "Falhas:" contaria a interrupção).
        coletor = context.config.get("_metrics_collector")
        with _fase_medida(coletor, self.name):
            raise RuntimeError("falha simulada pela demo (ex.: timeout do modelo)")

    # Monkey-patch temporário e proposital (restaurado no finally abaixo) —
    # não é descuido: é como a demo simula uma falha real passando pelo
    # caminho de execução de verdade.
    EditorVerdictPhase.run = _falha_simulada  # type: ignore[method-assign]
    try:
        run_demo(mode="mock", run_id=DEMO_RUN_ID)
    except RuntimeError as exc:
        print(f"\n>>> Falha capturada como esperado: {exc}")
        print(">>> Repare acima: 'Fases concluídas antes da falha' lista fase 1 e 2,")
        print(">>> com o caminho de cada checkpoint salvo em disco.")
    finally:
        # Restaura o comportamento normal da fase 3 antes do PASSO 2 —
        # simula o problema real (ex.: o timeout) ter sido resolvido.
        EditorVerdictPhase.run = original_run  # type: ignore[method-assign]

    # -------------------------------------------------------------------
    _sep("PASSO 2 — Retomando a MESMA execução (mesmo run_id)")
    print(
        "O problema que causou a falha foi resolvido (fase 3 voltou ao normal).\n"
        "Retomando com o mesmo run_id — fases 1 e 2 devem ser puladas."
    )
    print()

    report = run_demo(mode="mock", run_id=DEMO_RUN_ID)

    # -------------------------------------------------------------------
    _sep("O RESUMO DA RETOMADA COBRE A EXECUÇÃO INTEIRA?")
    ckpt = CheckpointManager(SRC / "logs" / "checkpoints", DEMO_RUN_ID)
    concluidas = ckpt.fases_concluidas()
    resumo = report.data.get("resumo_execucao", {})
    no_resumo = resumo.get("duracao_por_fase_s", {})
    retomada = report.data.get("retomada", {})

    print("  Fase                              checkpoint   no resumo final")
    for fase in concluidas:
        print(f"    {fase:<32} {'sim':<12} {'sim' if fase in no_resumo else 'NÃO'}")
    faltando = [f for f in concluidas if f not in no_resumo]
    print()
    print(f"  Restauradas do disco (não rodaram agora): {retomada.get('fases_restauradas')}")
    print(f"  Eventos de métrica restaurados:           {retomada.get('eventos_metricas_restaurados')}")
    print(f"  Validações no resumo final:               {resumo.get('quantidade_validacoes')}")
    print(f"  Status final:                             {resumo.get('status_final')}")
    print(
        "  >>> Nenhuma fase concluída ficou de fora do resumo."
        if not faltando
        else f"  >>> ATENÇÃO: fases sem métrica no resumo: {faltando}"
    )

    # -------------------------------------------------------------------
    _sep("PASSO 3 — Retomando uma execução JÁ CONCLUÍDA")
    print(
        "Retomar algo que já terminou não tem o que retomar. O risco aqui é o\n"
        "oposto do PASSO 2: re-executar e sobrescrever um relatório completo\n"
        "com o resultado de uma execução que não produziu nada."
    )
    print()

    relatorio_json = SRC / "outputs" / DEMO_RUN_ID / "final_report.json"
    antes = hashlib.sha256(relatorio_json.read_bytes()).hexdigest()

    run_demo(mode="mock", run_id=DEMO_RUN_ID)

    depois = hashlib.sha256(relatorio_json.read_bytes()).hexdigest()
    print()
    print(f"  sha256 do final_report.json antes:  {antes[:16]}…")
    print(f"  sha256 do final_report.json depois: {depois[:16]}…")
    print(
        "  >>> Arquivo PRESERVADO (nada foi regravado)."
        if antes == depois
        else "  >>> ATENÇÃO: o relatório foi regravado!"
    )
    print(f"  Para refazer de propósito: python main.py --resume {DEMO_RUN_ID} --force")

    # -------------------------------------------------------------------
    _sep("RESULTADO")
    ckpt_dir = SRC / "logs" / "checkpoints" / DEMO_RUN_ID
    arquivos = sorted(p.name for p in ckpt_dir.glob("*.json"))
    print(f"  Checkpoints salvos em {ckpt_dir}:")
    for nome in arquivos:
        print(f"    - {nome}")
    print(f"\n  Decisão final do relatório: {report.data.get('decisao')}")
    print("  A execução concluiu sem repetir as fases 1 e 2 (veja acima:")
    print("  '(checkpoint restaurado)' nas fases 1 e 2 do PASSO 2), com o resumo")
    print("  final descrevendo a execução inteira e sem regravar nada no PASSO 3.")
    print("-" * LARGURA)
    print("Demo concluída.")


if __name__ == "__main__":
    main()
