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
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent  # .../src/benchmark

from .corpus import carregar_corpus
from .executar import processar_documento

RESULTADOS_DIR = HERE / "resultados"
SAIDA_JSON = RESULTADOS_DIR / "ablacao_cross_review.json"
SAIDA_MD = RESULTADOS_DIR / "ablacao_cross_review.md"

#: Campos numéricos comparados par a par (com_cross_review vs sem_cross_review).
CAMPOS_DELTA_NUMERICO = ("duracao_total_s", "tokens_totais", "custo_estimado", "chamadas_llm")

#: Ressalva obrigatória em toda conclusão gerada. "Qualidade" aqui é medida por
#: INDICADORES AUTOMÁTICOS extraídos do veredito (quantas críticas, quantas
#: bloqueantes, se a decisão final mudou, se as notas por revisor mudaram).
#: NENHUM avaliador humano leu o conteúdo das críticas para dizer se elas são
#: pertinentes, corretas ou bem argumentadas — sem isso, "a leitura cruzada
#: melhorou a revisão?" continua sem resposta; o que está medido é só o
#: custo/tempo que ela adiciona e se ela muda os números do resultado.
LIMITE_QUALIDADE = (
    "LIMITE DESTA AVALIAÇÃO: 'qualidade' aqui é medida por indicadores "
    "automáticos (quantidade de críticas, quantas são bloqueantes, mudança na "
    "decisão final e nas notas por revisor). NÃO houve avaliação humana do "
    "conteúdo das críticas — nenhuma pessoa leu os pareceres para julgar se "
    "são pertinentes ou bem argumentados. Os números abaixo dizem quanto a "
    "leitura cruzada CUSTA e se ela MUDA o resultado, não se ela o MELHORA."
)


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
        "timestamp": datetime.now(UTC).isoformat(),
        "com_cross_review": registro_com,
        "sem_cross_review": registro_sem,
        "delta": comparar_par(registro_com, registro_sem),
    }


# ---------------------------------------------------------------------------
# Conclusão agregada (texto), a partir dos pares comparáveis
# ---------------------------------------------------------------------------

def _media(valores: list[float]) -> float | None:
    return sum(valores) / len(valores) if valores else None


def _linha_mock(mock: dict) -> str:
    """Linha que relata os pares em modo mock SEM misturá-los ao agregado real."""
    return (
        f"SMOKE TEST (modo mock, sem chamada LLM e sem custo): "
        f"{len(mock)} documento(s) — {', '.join(sorted(mock))}. "
        "Serve só para provar que a ferramenta roda de ponta a ponta de graça; "
        "não entra em nenhuma média acima."
    )


def separar_por_modo(pares: dict) -> tuple[dict, dict]:
    """Divide os pares em (execuções REAIS via api, execuções em modo mock).

    Misturar os dois num agregado só produz número errado: o modo mock lê
    pareceres pré-salvos, não faz nenhuma chamada LLM e não mede tokens nem
    custo, então ele entra na média de duração (medindo só o overhead de
    I/O do próprio pipeline) e infla a contagem de "documentos comparáveis"
    sem ter contribuído com nenhum dado de LLM. O smoke test em mock existe
    para provar que a ferramenta roda de ponta a ponta sem gastar API — não
    é evidência sobre o efeito da leitura cruzada, e é relatado à parte.
    """
    reais = {k: v for k, v in pares.items() if v.get("mode") == "api"}
    mock = {k: v for k, v in pares.items() if v.get("mode") != "api"}
    return reais, mock


def gerar_conclusao(pares: dict) -> str:
    """Parágrafo objetivo, calculado (não redigido à mão) a partir dos deltas.

    Agrega SOMENTE as execuções reais (``mode == "api"``) — ver
    ``separar_por_modo``. As execuções em modo mock aparecem numa linha
    própria, identificadas como smoke test.

    Dentro das reais, só entram no agregado os pares onde as DUAS execuções
    tiveram resultado 'sucesso' — um documento bloqueado ou que falhou de um
    lado não tem delta significativo e é listado à parte, não silenciosamente
    ignorado.
    """
    reais, mock = separar_por_modo(pares)
    comparaveis = {k: v for k, v in reais.items() if v["delta"].get("comparavel")}
    nao_comparaveis = sorted(set(reais) - set(comparaveis))

    if not comparaveis:
        linhas = [
            (
                "Nenhuma execução REAL (modo api) com sucesso nos dois lados — "
                "sem base para uma conclusão numérica."
            )
        ]
        if nao_comparaveis:
            linhas.append(f"Documentos não comparáveis: {', '.join(nao_comparaveis)}.")
        if mock:
            linhas.append(_linha_mock(mock))
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
        (
            f"EXECUÇÕES REAIS (modo api): {len(comparaveis)} documento(s) "
            f"comparável(is) (sucesso nos dois lados) de {len(reais)} rodado(s) — "
            f"{len(comparaveis) * 2} execuções reais de pipeline no total."
        ),
        (
            f"Chamadas LLM: {_media(chamadas_pct):+.1f}% em média sem leitura cruzada "
            f"(esperado -{3 / 7 * 100:.0f}% estrutural: 4 chamadas em vez de 7)."
            if chamadas_pct else "Chamadas LLM: sem dado."
        ),
        (
            f"Duração total: {_media(duracao_pct):+.1f}% em média sem leitura cruzada."
            if duracao_pct else "Duração total: sem dado."
        ),
        (
            f"Tokens totais: {_media(tokens_pct):+.1f}% em média sem leitura cruzada."
            if tokens_pct else "Tokens totais: sem dado."
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
        linhas.append(
            "Documentos não comparáveis (falha/bloqueio em algum lado): "
            f"{', '.join(nao_comparaveis)}."
        )
    if mock:
        linhas.append(_linha_mock(mock))

    linhas.append(
        "Leitura sugerida: se a decisão final e a quantidade de críticas NÃO mudam "
        "entre as duas variantes, a leitura cruzada está pagando custo/tempo "
        "adicionais sem alterar o resultado nestes documentos — o valor dela, "
        "se houver, está na qualidade argumentativa da resposta aos pares "
        "(texto de 'resposta_aos_pares' em cada final_report.md), não capturada "
        "numericamente aqui."
    )
    linhas.append(LIMITE_QUALIDADE)
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


def _fmt_num(valor, casas: int) -> str:
    """Arredonda para a tabela. Sem isso o markdown mostra o float cru
    (``0.019246399999999997``, ``36.0511250999989``), que é ruído de ponto
    flutuante — ilegível num relatório e sem nenhuma precisão real por trás.
    O dado exato continua no .json ao lado.
    """
    return "n/d" if valor is None else f"{valor:.{casas}f}"


def _linhas_tabela(pares: dict) -> list[str]:
    linhas = [
        (
            "| doc_id | provider:model | chamadas (com/sem) | duração_s (com/sem) | Δduração | "
            "tokens (com/sem) | Δtokens | custo USD (com/sem) | Δcusto | decisão (com/sem) | "
            "críticas (com/sem) |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for doc_id in sorted(pares):
        par = pares[doc_id]
        com, sem, delta = par["com_cross_review"], par["sem_cross_review"], par["delta"]
        linhas.append(
            "| " + " | ".join(
                _sanitizar(v) for v in (
                    doc_id,
                    f"{par.get('provider')}:{par.get('model')}",
                    f"{com.get('chamadas_llm')}/{sem.get('chamadas_llm')}",
                    (
                        f"{_fmt_num(com.get('duracao_total_s'), 2)}/"
                        f"{_fmt_num(sem.get('duracao_total_s'), 2)}"
                    ),
                    _fmt_pct(delta.get("duracao_total_s_variacao_pct")),
                    f"{com.get('tokens_totais')}/{sem.get('tokens_totais')}",
                    _fmt_pct(delta.get("tokens_totais_variacao_pct")),
                    (
                        f"{_fmt_num(com.get('custo_estimado'), 4)}/"
                        f"{_fmt_num(sem.get('custo_estimado'), 4)}"
                    ),
                    _fmt_pct(delta.get("custo_estimado_variacao_pct")),
                    f"{com.get('decisao_final')}/{sem.get('decisao_final')}",
                    f"{com.get('quantidade_criticas')}/{sem.get('quantidade_criticas')}",
                )
            ) + " |"
        )
    return linhas


def salvar(pares: dict, conclusao: str, destino_dir: Path = RESULTADOS_DIR) -> tuple[Path, Path]:
    destino_dir = Path(destino_dir)
    destino_dir.mkdir(parents=True, exist_ok=True)

    json_path = destino_dir / "ablacao_cross_review.json"
    json_path.write_text(
        json.dumps(
            {"gerado_em": datetime.now(UTC).isoformat(), "conclusao": conclusao, "pares": pares},
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    reais, mock = separar_por_modo(pares)

    secoes = [
        "# Ablação: pipeline completo vs. sem leitura cruzada (Grupo 2)",
        "",
        f"Gerado em: {datetime.now(UTC).isoformat()}",
        "",
        "## Conclusão",
        "",
        conclusao,
        "",
        "## Execuções reais (modo api)",
        "",
    ]
    if reais:
        secoes += [
            (
                f"{len(reais)} documento(s), cada um rodado 2x (com e sem leitura "
                f"cruzada) = {len(reais) * 2} execuções reais de pipeline, com "
                "chamadas LLM, tokens e custo medidos."
            ),
            "",
            *_linhas_tabela(reais),
        ]
    else:
        secoes.append("Nenhuma execução real registrada até agora.")

    secoes += ["", "## Smoke test (modo mock — sem chamada LLM, sem custo)", ""]
    if mock:
        secoes += [
            (
                "Não é evidência sobre o efeito da leitura cruzada: em modo mock os "
                "pareceres vêm de um JSON pré-salvo, nenhuma chamada LLM acontece e "
                "não há tokens nem custo para medir. Serve para provar que a "
                "ferramenta roda de ponta a ponta de graça. **Estes números não "
                "entram em nenhuma média da seção anterior.**"
            ),
            "",
            *_linhas_tabela(mock),
        ]
    else:
        secoes.append("Nenhuma execução em modo mock registrada.")

    conteudo_md = "\n".join(secoes) + "\n"
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
        "--mode", choices=("mock", "api"),
        help="Modo de execução do pipeline. Obrigatório — sem default silencioso "
        "(dispensado apenas com --regerar, que não executa nada).",
    )
    parser.add_argument(
        "--docs", metavar="id1,id2,...",
        help="ids do manifesto a rodar, separados por vírgula. Cada um roda DUAS "
        "vezes (com e sem leitura cruzada) — em modo api, o dobro do custo de "
        "executar.py para a mesma lista. Dispensado com --regerar.",
    )
    parser.add_argument(
        "--regerar", action="store_true",
        help="NÃO executa nada: recalcula a conclusão e reescreve o .json/.md a "
        "partir dos pares JÁ gravados em resultados/ablacao_cross_review.json. "
        "Use depois de mudar a lógica de agregação/relatório para não pagar de "
        "novo por execuções reais que já foram feitas.",
    )
    parser.add_argument(
        "--cache-dir", dest="cache_dir", default=str(HERE / "cache_pdfs"), metavar="DIR",
        help="Diretório de cache dos PDFs baixados (default: %(default)s).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    if args.regerar:
        pares = _carregar_pares_existentes(SAIDA_JSON)
        if not pares:
            raise SystemExit(
                f"Nada para regerar: '{SAIDA_JSON}' não existe ou não tem pares."
            )
        conclusao = gerar_conclusao(pares)
        json_path, md_path = salvar(pares, conclusao)
        print(conclusao)
        print(f"\nRegerado (sem executar o pipeline): {json_path} e {md_path}")
        return

    faltando = [nome for nome in ("mode", "docs") if not getattr(args, nome)]
    if faltando:
        raise SystemExit(
            "argumento(s) obrigatório(s) ausente(s): "
            + ", ".join(f"--{nome}" for nome in faltando)
        )

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
