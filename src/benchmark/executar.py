"""Executa o pipeline sobre um lote selecionado do corpus e registra o diagnóstico — Grupo 2.

Este módulo só CONSOME o que ``pipeline.run_demo`` e ``metrics/resumo.py`` já
produzem (``report.data``, ``report.data["resumo_execucao"]``) — não recalcula
nenhuma métrica. A seleção de documentos é sempre explícita (``--docs``): não
existe um modo implícito "roda o corpus inteiro", porque isso poderia disparar
chamadas reais de API sem intenção clara de quem roda a ferramenta. Pelo mesmo
motivo, ``--mode`` não tem default silencioso.

Uso:
    python -m src.benchmark.executar --mode mock --docs exemplo_mock
    python -m src.benchmark.executar --mode api --docs doc_1,doc_2

Cada documento roda sequencialmente (nunca em paralelo) para não estourar rate
limit em modo ``api``. Um erro em um documento (PDF corrompido, exceção do
pipeline) não interrompe o lote — é registrado como resultado
``entrada_bloqueada``/``falha_execucao`` e o processamento segue para o
próximo documento; é exatamente esse tipo de caso que o benchmark existe para
capturar.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent  # .../src/benchmark
SRC = HERE.parent  # .../src

from .corpus import DocumentoCorpus, carregar_corpus, resolver_pdf_local  # noqa: E402

# Import guardado (mesmo padrão de main.py): src/ precisa entrar no sys.path
# ANTES de importar pipeline/validacao_entrada, porque esses módulos fazem
# imports flat entre si (ex.: pipeline.py faz `from validacao_entrada import
# ...`). Importar por um caminho diferente (`src.pipeline`) criaria uma cópia
# separada do módulo, e `except EntradaInvalidaError` deixaria de casar com a
# exceção de fato levantada por run_demo.
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    from pipeline import run_demo
    from validacao_entrada import EntradaInvalidaError
    from model_provider import resolver_config
except Exception as exc:  # noqa: BLE001
    raise RuntimeError(
        "Não foi possível importar 'pipeline'/'validacao_entrada'/'model_provider' "
        "de src/. Rode a partir da raiz do repositório com as dependências do "
        f"projeto instaladas (.venv). Erro original: {type(exc).__name__}: {exc}"
    ) from exc

RESULTADOS_DIR = HERE / "resultados"
EXECUCOES_DIR = RESULTADOS_DIR / "execucoes"
EXECUCOES_PATH = RESULTADOS_DIR / "execucoes.json"

_PADRAO_RUN_ID_FALHA = re.compile(r"FALHA \| run_id=(\S+) \|")


def _fmt_s(valor: float | None) -> str:
    if valor is None:
        return "n/d"
    if valor == 0.0:
        return "0 s"
    if valor < 0.01:
        return "< 0.01 s"
    return f"{valor:.2f} s"


def _fmt_tokens(valor: int | None) -> str:
    return "n/d" if valor is None else str(valor)


def _extrair_run_id_de_falha(saida: str) -> str | None:
    match = _PADRAO_RUN_ID_FALHA.search(saida)
    return match.group(1) if match else None


def _ler_resumo_parcial(run_id: str) -> dict | None:
    """Lê ``resumo_execucao_parcial.json`` que o pipeline já salva sozinho em falha."""
    caminho = SRC / "outputs" / run_id / "resumo_execucao_parcial.json"
    if not caminho.exists():
        return None
    return json.loads(caminho.read_text(encoding="utf-8"))


def _config_llm(mode: str) -> dict:
    """Provedor/modelo/structured_output CONFIGURADOS para esta execução.

    Registrar isso por execução é o que falta hoje para comparar resultados
    do benchmark entre si de forma confiável (ver docs/benchmark_reference.md
    §6 — 'modelo_usado' sozinho, no resumo, não basta: não diz o provedor nem
    se structured output nativo estava ligado). Em modo mock não há provedor
    real envolvido (nenhuma chamada LLM acontece) — os três campos vêm
    ``None`` em vez de anunciar uma configuração que não foi de fato usada.
    """
    if mode != "api":
        return {"provider": None, "model": None, "structured_output": None}
    escolha = resolver_config({})
    return {
        "provider": escolha.provider.value,
        "model": escolha.model,
        "structured_output": escolha.structured_output,
    }


def _registro_base(doc: DocumentoCorpus, *, mode: str, cross_review: bool) -> dict:
    return {
        "doc_id": doc.id,
        "titulo": doc.titulo,
        "area": doc.area,
        "caracteristicas": doc.caracteristicas,
        "mode": mode,
        **_config_llm(mode),
        "cross_review_enabled": cross_review,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _campos_do_resumo(resumo: dict | None) -> dict:
    """Extrai do dict de ResumoExecucao os campos que entram no registro do doc."""
    if resumo is None:
        return {
            "requer_revisao_humana": None,
            "duracao_total_s": None,
            "tokens_totais": None,
            "custo_estimado": None,
            "chamadas_llm": None,
            "modelo_usado": None,
            "quantidade_tools_chamadas": None,
            "quantidade_retries": None,
            "quantidade_falhas": None,
            "alertas": [],
        }
    return {
        "requer_revisao_humana": resumo.get("requer_revisao_humana"),
        "duracao_total_s": resumo.get("duracao_total_s"),
        "tokens_totais": resumo.get("tokens_totais"),
        "custo_estimado": resumo.get("custo_estimado"),
        "chamadas_llm": resumo.get("chamadas_llm"),
        "modelo_usado": resumo.get("modelo_usado"),
        "quantidade_tools_chamadas": resumo.get("quantidade_tools_chamadas"),
        "quantidade_retries": resumo.get("quantidade_retries"),
        "quantidade_falhas": resumo.get("quantidade_falhas"),
        "alertas": resumo.get("alertas", []),
    }


def _campos_de_qualidade(verdict: dict | None) -> dict:
    """Extrai do veredito do editor (``report.data["phase3_verdict"]``) os
    sinais de QUALIDADE que o resumo de métricas não carrega (ele só sabe a
    decisão final, não quantas críticas a sustentam) — usado para comparar a
    variante com/sem leitura cruzada (ver ``ablacao_cross_review.py``), mas
    fica disponível em todo registro do benchmark, não só na ablação.
    """
    if verdict is None:
        return {
            "notas_por_revisor": None,
            "quantidade_criticas": None,
            "quantidade_criticas_bloqueantes": None,
        }
    criticas = verdict.get("criticas") or []
    return {
        "notas_por_revisor": verdict.get("notas_por_revisor"),
        "quantidade_criticas": len(criticas),
        "quantidade_criticas_bloqueantes": sum(1 for c in criticas if c.get("tipo") == "critica"),
    }


def processar_documento(
    doc: DocumentoCorpus, *, mode: str, cache_dir: Path, cross_review: bool = True,
) -> tuple[dict, dict | None]:
    """Roda run_demo para um documento e monta (registro, resumo_para_persistir).

    ``cross_review`` (default ``True``) passa direto para
    ``pipeline.run_demo`` — ``False`` roda a variante experimental sem Fase 2
    (ver ``ablacao_cross_review.py`` para o comparativo dedicado a essa
    variante; aqui ela só precisa ficar registrada no ``registro`` para não
    misturar resultados de configurações diferentes sob o mesmo ``doc_id``).

    ``resumo_para_persistir`` é o dict de ResumoExecucao (completo ou parcial)
    quando existir, ou ``None`` quando não há resumo (entrada bloqueada, ou
    falha sem resumo parcial localizável).
    """
    base = _registro_base(doc, mode=mode, cross_review=cross_review)
    caminho_pdf = resolver_pdf_local(doc, cache_dir)

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            report = run_demo(mode=mode, pdf_path=caminho_pdf, cross_review=cross_review)
    except EntradaInvalidaError as exc:
        print(buffer.getvalue(), end="")
        registro = {
            **base,
            "run_id": None,
            "resultado": "entrada_bloqueada",
            "decisao_final": None,
            **_campos_do_resumo(None),
            **_campos_de_qualidade(None),
            "erro": {"tipo": type(exc).__name__, "mensagem": str(exc)},
        }
        return registro, None
    except Exception as exc:  # noqa: BLE001
        saida = buffer.getvalue()
        print(saida, end="")
        run_id_falha = _extrair_run_id_de_falha(saida)
        resumo_parcial = _ler_resumo_parcial(run_id_falha) if run_id_falha else None
        registro = {
            **base,
            "run_id": run_id_falha,
            "resultado": "falha_execucao",
            "decisao_final": resumo_parcial.get("decisao_final") if resumo_parcial else None,
            **_campos_do_resumo(resumo_parcial),
            # Uma falha no meio do pipeline não deixa veredito do editor
            # persistido no resumo parcial (só ResumoExecucao, sem
            # phase3_verdict) — sem sinal de qualidade para este caso.
            **_campos_de_qualidade(None),
            "erro": {"tipo": type(exc).__name__, "mensagem": str(exc)},
        }
        return registro, resumo_parcial
    else:
        print(buffer.getvalue(), end="")
        resumo = report.data.get("resumo_execucao")
        registro = {
            **base,
            "run_id": report.data.get("run_id"),
            "resultado": (resumo or {}).get("status_final", "sucesso"),
            "decisao_final": report.data.get("decisao"),
            **_campos_do_resumo(resumo),
            **_campos_de_qualidade(report.data.get("phase3_verdict")),
            "erro": None,
        }
        return registro, resumo


def imprimir_diagnostico(registro: dict) -> None:
    """Diagnóstico de uma execução, impresso assim que ela termina (não espera o lote)."""
    print("-" * 74)
    print(f"[{registro['doc_id']}] resultado={registro['resultado']}  (run_id={registro['run_id'] or 'n/d'})")
    print(f"  decisão:            {registro['decisao_final'] if registro['decisao_final'] is not None else '—'}")
    revisao = registro["requer_revisao_humana"]
    print(f"  revisão humana:     {'RECOMENDADA' if revisao else ('não recomendada' if revisao is not None else 'n/d')}")
    print(f"  duração total:      {_fmt_s(registro['duracao_total_s'])}")
    print(f"  tokens totais:      {_fmt_tokens(registro['tokens_totais'])}")
    if registro["alertas"]:
        print("  alertas:")
        for alerta in registro["alertas"]:
            print(f"    - {alerta}")
    else:
        print("  alertas:            (nenhum)")
    if registro["erro"]:
        print(f"  erro:               {registro['erro']['tipo']}: {registro['erro']['mensagem']}")
    print("-" * 74)


def _persistir_resumo(resumo: dict | None, registro: dict, execucoes_dir: Path) -> None:
    if resumo is None:
        return
    execucoes_dir.mkdir(parents=True, exist_ok=True)
    run_id = registro.get("run_id")
    if run_id:
        nome = f"{run_id}_resumo.json"
    else:
        timestamp_seguro = registro["timestamp"].replace(":", "-")
        nome = f"{registro['doc_id']}_{timestamp_seguro}_resumo.json"
    (execucoes_dir / nome).write_text(
        json.dumps(resumo, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _carregar_execucoes_existentes(caminho: Path) -> dict:
    if not caminho.exists():
        return {}
    return json.loads(caminho.read_text(encoding="utf-8")).get("execucoes", {})


def _salvar_execucoes(caminho: Path, execucoes: dict) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(
        json.dumps({"execucoes": execucoes}, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Roda o pipeline sobre um lote explícito de documentos do corpus.",
    )
    parser.add_argument(
        "--manifest", default=str(HERE / "corpus_manifest.json"), metavar="CAMINHO",
        help="Caminho do manifesto JSON do corpus (default: %(default)s).",
    )
    parser.add_argument(
        "--mode", required=True, choices=("mock", "api"),
        help="Modo de execução do pipeline. Obrigatório — sem default silencioso.",
    )
    parser.add_argument(
        "--docs", required=True, metavar="id1,id2,...",
        help="ids do manifesto a rodar, separados por vírgula. Obrigatório — "
        "não existe um modo implícito de rodar o corpus inteiro.",
    )
    parser.add_argument(
        "--cache-dir", dest="cache_dir", default=str(HERE / "cache_pdfs"), metavar="DIR",
        help="Diretório de cache dos PDFs baixados (default: %(default)s).",
    )
    parser.add_argument(
        "--cross-review", dest="cross_review", choices=("on", "off"), default="on",
        help="'on' (default) roda a Fase 2 normalmente; 'off' roda a variante "
        "experimental sem leitura cruzada (ver ablacao_cross_review.py para "
        "comparar as duas de forma pareada em vez de sobrescrever o registro "
        "'on' já existente do mesmo doc_id em execucoes.json).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    manifest_path = Path(args.manifest)
    cache_dir = Path(args.cache_dir)

    documentos = carregar_corpus(manifest_path)
    mapa = {doc.id: doc for doc in documentos}

    ids_solicitados = [item.strip() for item in args.docs.split(",") if item.strip()]
    ids_invalidos = [doc_id for doc_id in ids_solicitados if doc_id not in mapa]
    if ids_invalidos:
        disponiveis = ", ".join(sorted(mapa)) or "(nenhum documento no manifesto)"
        raise SystemExit(
            f"id(s) não encontrado(s) no manifesto '{manifest_path}': "
            f"{', '.join(ids_invalidos)}. Disponíveis: {disponiveis}"
        )

    cross_review = args.cross_review == "on"
    execucoes = _carregar_execucoes_existentes(EXECUCOES_PATH)

    for doc_id in ids_solicitados:
        doc = mapa[doc_id]
        registro, resumo = processar_documento(
            doc, mode=args.mode, cache_dir=cache_dir, cross_review=cross_review,
        )
        imprimir_diagnostico(registro)
        _persistir_resumo(resumo, registro, EXECUCOES_DIR)
        # execucoes.json guarda o resultado MAIS RECENTE por doc_id (upsert) —
        # rodar a variante 'off' sob a mesma chave apagaria silenciosamente o
        # registro 'on' do mesmo documento (e vice-versa). A chave só ganha o
        # sufixo quando a variante não é a default, para não mudar o formato
        # dos registros 'on' já commitados (comparar.py e o histórico atual
        # continuam lendo doc_id puro para eles).
        chave = doc_id if cross_review else f"{doc_id}__sem_cross_review"
        execucoes[chave] = registro

    _salvar_execucoes(EXECUCOES_PATH, execucoes)
    print(f"\n{len(ids_solicitados)} documento(s) processado(s). Registro atualizado em: {EXECUCOES_PATH}")


if __name__ == "__main__":
    main()
