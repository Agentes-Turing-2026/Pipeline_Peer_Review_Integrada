"""Comparativo PAREADO com/sem leitura cruzada — Grupo 2 (benchmark, custo e eficiência).

Responde à pergunta que motivou esta atividade: quanto a leitura cruzada
(Fase 2 — 3 chamadas LLM adicionais, uma por revisor) melhora a resposta do
pipeline frente ao custo e ao tempo extra que ela introduz? Roda o MESMO
documento, no MESMO modo e com o MESMO provedor/modelo, duas vezes — uma com
``cross_review=True`` (pipeline completo, 7 chamadas LLM: 3 revisores + 3
leituras cruzadas + 1 editor) e outra com ``cross_review=False`` (variante
experimental, 4 chamadas: 3 revisores + 1 editor, ver
``pipeline.CrossReviewPhase.run`` e ``pipeline.run_demo(cross_review=...)``) —
e registra as duas execuções lado a lado, com o delta entre elas.

Reaproveita ``executar.processar_documento`` (mesma execução, mesmo
diagnóstico, mesmos campos de configuração) duas vezes por documento — não
duplica a lógica de rodar o pipeline nem de extrair o resumo/veredito.

"Qualidade" aqui é um proxy OBJETIVO (número de críticas, quantas são
bloqueantes, se a decisão final mudou, se as notas por revisor mudaram) — não
uma nota de qualidade textual das críticas, que exigiria julgamento humano ou
um LLM-juiz e está fora do escopo desta ferramenta. A tabela/conclusão gerada
aqui é o material bruto para a conclusão QUALITATIVA que o grupo escreve à
mão a partir do texto das críticas em cada ``final_report.md``.

Uso:
    python -m src.benchmark.ablacao_cross_review --mode mock --docs exemplo_mock
    python -m src.benchmark.ablacao_cross_review --mode api --docs doc_1,doc_2

Sequencial por documento e por variante (nunca em paralelo) — mesmo motivo de
``executar.py``: não estourar rate limit e manter o custo do modo ``api``
previsível. Cada documento roda DUAS vezes em modo ``api``: o dobro do custo
de uma execução única de ``executar.py`` — pense no tamanho de ``--docs``
antes de rodar com ``--mode api``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent  # .../src/benchmark

from .corpus import carregar_corpus  # noqa: E402
from .executar import processar_documento  # noqa: E402

RESULTADOS_DIR = HERE / "resultados"
SAIDA_JSON = RESULTADOS_DIR / "ablacao_cross_review.json"
SAIDA_MD = RESULTADOS_DIR / "ablacao_cross_review.md"

#: Campos numéricos comparados par a par (com_cross_review vs sem_cross_review).
CAMPOS_DELTA_NUMERICO = ("duracao_total_s", "tokens_totais", "custo_estimado", "chamadas_llm")


def _variacao_percentual(com: float | None, sem: float | None) -> float | None:
    """Variação de 'sem' em relação a 'com', em %. None se algum lado for None/indisponível.

    Negativo = 'sem leitura cruzada' consumiu MENOS (o caso esperado ao
    remover 3 chamadas LLM). ``com == 0`` não é um caso real (toda execução
    bem-sucedida tem duração/chamadas > 0) — devolve None em vez de dividir
    por zero.
    """
    if com is None or sem is None or com == 0:
        return None
    return (sem - com) / com * 100.0


def comparar_par(registro_com: dict, registro_sem: dict) -> dict:
    """Monta o bloco ``delta`` entre as duas execuções do MESMO documento."""
    delta: dict = {}
    for campo in CAMPOS_DELTA_NUMERICO:
        delta[f"{campo}_variacao_pct"] = _variacao_percentual(
            registro_com.get(campo), registro_sem.get(campo)
        )

    ambos_sucesso = registro_com.get("resultado") == "sucesso" and registro_sem.get("resultado") == "sucesso"
    delta["comparavel"] = ambos_sucesso
    if not ambos_sucesso:
        return delta

    delta["decisao_mudou"] = registro_com.get("decisao_final") != registro_sem.get("decisao_final")
    delta["notas_por_revisor_mudaram"] = registro_com.get("notas_por_revisor") != registro_sem.get(
        "notas_por_revisor"
    )
    qc_com = registro_com.get("quantidade_criticas")
    qc_sem = registro_sem.get("quantidade_criticas")
    delta["quantidade_criticas_delta"] = (
        qc_sem - qc_com if qc_com is not None and qc_sem is not None else None
    )
    qcb_com = registro_com.get("quantidade_criticas_bloqueantes")
    qcb_sem = registro_sem.get("quantidade_criticas_bloqueantes")
    delta["quantidade_criticas_bloqueantes_delta"] = (
        qcb_sem - qcb_com if qcb_com is not None and qcb_sem is not None else None
    )
    delta["requer_revisao_humana_mudou"] = (
        registro_com.get("requer_revisao_humana") != registro_sem.get("requer_revisao_humana")
    )
    return delta


def rodar_par(doc, *, mode: str, cache_dir: Path) -> dict:
    """Roda o mesmo documento com e sem leitura cruzada e monta o par comparável."""
    print(f"\n=== {doc.id}: COM leitura cruzada (pipeline completo) ===")
    registro_com, _ = processar_documento(doc, mode=mode, cache_dir=cache_dir, cross_review=True)
    print(f"\n=== {doc.id}: SEM leitura cruzada (variante experimental) ===")
    registro_sem, _ = processar_documento(doc, mode=mode, cache_dir=cache_dir, cross_review=False)

    return {
        "doc_id": doc.id,
        "titulo": doc.titulo,
        "mode": mode,
        "provider": registro_com.get("provider"),
        "model": registro_com.get("model"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "com_cross_review": registro_com,
        "sem_cross_review": registro_sem,
        "delta": comparar_par(registro_com, registro_sem),
    }


# ---------------------------------------------------------------------------
# Conclusão agregada (texto), a partir dos pares comparáveis
# ---------------------------------------------------------------------------

def _media(valores: list[float]) -> float | None:
    return sum(valores) / len(valores) if valores else None


def gerar_conclusao(pares: dict) -> str:
    """Parágrafo objetivo, calculado (não redigido à mão) a partir dos deltas.

    Só usa pares onde as DUAS execuções tiveram resultado 'sucesso' — um
    documento bloqueado ou que falhou de um lado não tem delta significativo
    e é listado à parte, não silenciosamente ignorado.
    """
    comparaveis = {k: v for k, v in pares.items() if v["delta"].get("comparavel")}
    nao_comparaveis = sorted(set(pares) - set(comparaveis))

    if not comparaveis:
        linhas = ["Nenhum par com sucesso nos dois lados — sem base para uma conclusão numérica."]
        if nao_comparaveis:
            linhas.append(f"Documentos não comparáveis: {', '.join(nao_comparaveis)}.")
        return "\n".join(linhas)

    duracao_pct = [
        v["delta"]["duracao_total_s_variacao_pct"] for v in comparaveis.values()
        if v["delta"]["duracao_total_s_variacao_pct"] is not None
    ]
    tokens_pct = [
        v["delta"]["tokens_totais_variacao_pct"] for v in comparaveis.values()
        if v["delta"]["tokens_totais_variacao_pct"] is not None
    ]
    custo_pct = [
        v["delta"]["custo_estimado_variacao_pct"] for v in comparaveis.values()
        if v["delta"]["custo_estimado_variacao_pct"] is not None
    ]
    chamadas_pct = [
        v["delta"]["chamadas_llm_variacao_pct"] for v in comparaveis.values()
        if v["delta"]["chamadas_llm_variacao_pct"] is not None
    ]
    decisoes_mudaram = [doc_id for doc_id, v in comparaveis.items() if v["delta"]["decisao_mudou"]]
    criticas_deltas = [
        v["delta"]["quantidade_criticas_delta"] for v in comparaveis.values()
        if v["delta"]["quantidade_criticas_delta"] is not None
    ]

    linhas = [
        f"{len(comparaveis)} documento(s) comparável(is) (sucesso nos dois lados) "
        f"de {len(pares)} rodado(s).",
        (
            f"Chamadas LLM: {_media(chamadas_pct):+.1f}% em média sem leitura cruzada "
            f"(esperado -{3 / 7 * 100:.0f}% estrutural: 4 chamadas em vez de 7)."
            if chamadas_pct else "Chamadas LLM: sem dado (nenhuma chamada real medida — modo mock)."
        ),
        (
            f"Duração total: {_media(duracao_pct):+.1f}% em média sem leitura cruzada."
            if duracao_pct else "Duração total: sem dado."
        ),
        (
            f"Tokens totais: {_media(tokens_pct):+.1f}% em média sem leitura cruzada."
            if tokens_pct else "Tokens totais: sem dado (modo mock não mede tokens)."
        ),
        (
            f"Custo estimado: {_media(custo_pct):+.1f}% em média sem leitura cruzada."
            if custo_pct else "Custo estimado: sem dado (sem preço configurado/tokens medidos)."
        ),
        (
            f"Decisão final MUDOU em {len(decisoes_mudaram)}/{len(comparaveis)} documento(s)"
            + (f": {', '.join(decisoes_mudaram)}." if decisoes_mudaram else ".")
        ),
        (
            f"Quantidade de críticas: variação média de {_media(criticas_deltas):+.1f} "
            "crítica(s) ao desativar a leitura cruzada."
            if criticas_deltas else "Quantidade de críticas: sem dado."
        ),
    ]
    if nao_comparaveis:
        linhas.append(f"Documentos não comparáveis (falha/bloqueio em algum lado): {', '.join(nao_comparaveis)}.")

    linhas.append(
        "Leitura sugerida: se a decisão final e a quantidade de críticas NÃO mudam "
        "entre as duas variantes, a leitura cruzada está pagando custo/tempo "
        "adicionais sem alterar o resultado nestes documentos — o valor dela, "
        "se houver, está na qualidade argumentativa da resposta aos pares "
        "(texto de 'resposta_aos_pares' em cada final_report.md), não capturada "
        "numericamente aqui."
    )
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Persistência e exibição
# ---------------------------------------------------------------------------

def _carregar_pares_existentes(caminho: Path) -> dict:
    if not caminho.exists():
        return {}
    return json.loads(caminho.read_text(encoding="utf-8")).get("pares", {})


def _sanitizar(texto: str) -> str:
    return str(texto).replace("|", "\\|").replace("\n", " ")


def _fmt_pct(valor: float | None) -> str:
    return "n/d" if valor is None else f"{valor:+.1f}%"


def salvar(pares: dict, conclusao: str, destino_dir: Path = RESULTADOS_DIR) -> tuple[Path, Path]:
    destino_dir = Path(destino_dir)
    destino_dir.mkdir(parents=True, exist_ok=True)

    json_path = destino_dir / "ablacao_cross_review.json"
    json_path.write_text(
        json.dumps(
            {"gerado_em": datetime.now(timezone.utc).isoformat(), "conclusao": conclusao, "pares": pares},
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    linhas_tabela = [
        "| doc_id | provider:model | chamadas (com/sem) | duração_s (com/sem) | Δduração | "
        "tokens (com/sem) | Δtokens | custo USD (com/sem) | Δcusto | decisão (com/sem) | "
        "críticas (com/sem) |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for doc_id in sorted(pares):
        par = pares[doc_id]
        com, sem, delta = par["com_cross_review"], par["sem_cross_review"], par["delta"]
        linhas_tabela.append(
            "| " + " | ".join(
                _sanitizar(v) for v in (
                    doc_id,
                    f"{par.get('provider')}:{par.get('model')}",
                    f"{com.get('chamadas_llm')}/{sem.get('chamadas_llm')}",
                    f"{com.get('duracao_total_s')}/{sem.get('duracao_total_s')}",
                    _fmt_pct(delta.get("duracao_total_s_variacao_pct")),
                    f"{com.get('tokens_totais')}/{sem.get('tokens_totais')}",
                    _fmt_pct(delta.get("tokens_totais_variacao_pct")),
                    f"{com.get('custo_estimado')}/{sem.get('custo_estimado')}",
                    _fmt_pct(delta.get("custo_estimado_variacao_pct")),
                    f"{com.get('decisao_final')}/{sem.get('decisao_final')}",
                    f"{com.get('quantidade_criticas')}/{sem.get('quantidade_criticas')}",
                )
            ) + " |"
        )

    conteudo_md = (
        "# Ablação: pipeline completo vs. sem leitura cruzada (Grupo 2)\n\n"
        f"Gerado em: {datetime.now(timezone.utc).isoformat()}\n\n"
        "## Conclusão\n\n" + conclusao + "\n\n"
        "## Tabela\n\n" + "\n".join(linhas_tabela) + "\n"
    )
    md_path = destino_dir / "ablacao_cross_review.md"
    md_path.write_text(conteudo_md, encoding="utf-8")
    return json_path, md_path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Roda cada documento DUAS vezes (com e sem leitura cruzada) e "
            "compara tokens/duração/custo/decisão/críticas."
        ),
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
        help="ids do manifesto a rodar, separados por vírgula. Cada um roda DUAS "
        "vezes (com e sem leitura cruzada) — em modo api, o dobro do custo de "
        "executar.py para a mesma lista.",
    )
    parser.add_argument(
        "--cache-dir", dest="cache_dir", default=str(HERE / "cache_pdfs"), metavar="DIR",
        help="Diretório de cache dos PDFs baixados (default: %(default)s).",
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

    pares = _carregar_pares_existentes(SAIDA_JSON)
    for doc_id in ids_solicitados:
        pares[doc_id] = rodar_par(mapa[doc_id], mode=args.mode, cache_dir=cache_dir)

    conclusao = gerar_conclusao(pares)
    json_path, md_path = salvar(pares, conclusao)

    print("\n" + "=" * 74)
    print("  CONCLUSÃO — leitura cruzada: pipeline completo vs. variante sem ela")
    print("=" * 74)
    print(conclusao)
    print(f"\nComparativo salvo em: {json_path} e {md_path}")


if __name__ == "__main__":
    main()
