"""Comparativo PAREADO de topologia — multiagente (peer review) vs. agente único.

Responde à pergunta que motiva a baseline experimental de agente único
(``src/single_agent_baseline.py``): o que a arquitetura multiagente (3
revisores especializados + leitura cruzada + editor-chefe, 7 chamadas LLM)
entrega a mais frente a UM único agente que lê o artigo e produz o veredito
final diretamente (1 chamada LLM)? Roda o MESMO documento, no MESMO modo e com
o MESMO provedor/modelo, duas vezes — uma com a topologia ``multiagente``
(``pipeline.run_demo(single_agent=False)``) e outra com ``agente_unico``
(``pipeline.run_demo(single_agent=True)``) — e registra as duas execuções lado
a lado, com o delta entre elas. Ver
``docs/protocolo_experimento_topologia.md`` para hipótese, condições e
procedimento completos.

Reaproveita ``executar.processar_documento`` (mesma execução, mesmo
diagnóstico, mesmos campos de configuração) duas vezes por documento — não
duplica a lógica de rodar o pipeline nem de extrair o resumo/veredito. Segue
o mesmo padrão de ``ablacao_cross_review.py`` (mesma estrutura de comparação
pareada), só trocando o eixo comparado.

"Qualidade" aqui é um proxy OBJETIVO (decisão final, quantidade de críticas,
quantas são bloqueantes) — não uma nota de qualidade textual das críticas, que
exigiria julgamento humano ou um LLM-juiz e está fora do escopo desta
ferramenta. ``notas_por_revisor`` não é comparável diretamente entre as duas
topologias (a multiagente tem uma nota por revisor especializado; o agente
único tem uma única nota própria) — por isso o comparativo usa a ``decisao``
final (mesma escala 1-4 nas duas topologias) como o sinal comum.

Uso:
    python -m src.benchmark.comparar_topologia --mode mock --docs exemplo_mock
    python -m src.benchmark.comparar_topologia --mode api --docs doc_1,doc_2

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
SAIDA_JSON = RESULTADOS_DIR / "comparativo_topologia.json"
SAIDA_MD = RESULTADOS_DIR / "comparativo_topologia.md"

#: Campos numéricos comparados par a par (multiagente vs agente_unico).
CAMPOS_DELTA_NUMERICO = ("duracao_total_s", "tokens_totais", "custo_estimado", "chamadas_llm")

#: Ressalva obrigatória em toda conclusão gerada. Ver docstring do módulo.
LIMITE_QUALIDADE = (
    "LIMITE DESTA AVALIAÇÃO: 'qualidade' aqui é medida por indicadores "
    "automáticos (decisão final na escala 1-4, quantidade de críticas, "
    "quantas são bloqueantes). NÃO houve avaliação humana do conteúdo das "
    "críticas — nenhuma pessoa leu os pareceres para julgar se são "
    "pertinentes ou bem argumentados, nem se a topologia multiagente produz "
    "críticas mais específicas ou melhor fundamentadas que o agente único. "
    "'notas_por_revisor' não é comparável entre as topologias (formatos "
    "diferentes) e não entra no comparativo. Os números abaixo dizem quanto a "
    "topologia multiagente CUSTA a mais e se ela MUDA o resultado, não se ela "
    "o MELHORA."
)


def _variacao_percentual(multiagente: float | None, agente_unico: float | None) -> float | None:
    """Variação do 'agente_unico' em relação ao 'multiagente', em %.

    ``None`` se algum lado for ``None``/indisponível. Negativo = 'agente_unico'
    consumiu MENOS (o caso esperado: 1 chamada LLM em vez de 7).
    ``multiagente == 0`` não é um caso real (toda execução bem-sucedida tem
    duração/chamadas > 0) — devolve ``None`` em vez de dividir por zero.
    """
    if multiagente is None or agente_unico is None or multiagente == 0:
        return None
    return (agente_unico - multiagente) / multiagente * 100.0


def comparar_par(registro_multiagente: dict, registro_agente_unico: dict) -> dict:
    """Monta o bloco ``delta`` entre as duas execuções do MESMO documento."""
    delta: dict = {}
    for campo in CAMPOS_DELTA_NUMERICO:
        delta[f"{campo}_variacao_pct"] = _variacao_percentual(
            registro_multiagente.get(campo), registro_agente_unico.get(campo)
        )

    ambos_sucesso = (
        registro_multiagente.get("resultado") == "sucesso"
        and registro_agente_unico.get("resultado") == "sucesso"
    )
    delta["comparavel"] = ambos_sucesso
    if not ambos_sucesso:
        return delta

    delta["decisao_mudou"] = (
        registro_multiagente.get("decisao_final") != registro_agente_unico.get("decisao_final")
    )
    qc_multi = registro_multiagente.get("quantidade_criticas")
    qc_unico = registro_agente_unico.get("quantidade_criticas")
    delta["quantidade_criticas_delta"] = (
        qc_unico - qc_multi if qc_multi is not None and qc_unico is not None else None
    )
    qcb_multi = registro_multiagente.get("quantidade_criticas_bloqueantes")
    qcb_unico = registro_agente_unico.get("quantidade_criticas_bloqueantes")
    delta["quantidade_criticas_bloqueantes_delta"] = (
        qcb_unico - qcb_multi if qcb_multi is not None and qcb_unico is not None else None
    )
    delta["requer_revisao_humana_mudou"] = (
        registro_multiagente.get("requer_revisao_humana")
        != registro_agente_unico.get("requer_revisao_humana")
    )
    return delta


def rodar_par(doc, *, mode: str, cache_dir: Path) -> dict:
    """Roda o mesmo documento nas duas topologias e monta o par comparável."""
    print(f"\n=== {doc.id}: MULTIAGENTE (peer review completo) ===")
    registro_multi, _ = processar_documento(
        doc, mode=mode, cache_dir=cache_dir, cross_review=True, single_agent=False,
    )
    print(f"\n=== {doc.id}: AGENTE ÚNICO (baseline experimental) ===")
    registro_unico, _ = processar_documento(
        doc, mode=mode, cache_dir=cache_dir, single_agent=True,
    )

    return {
        "doc_id": doc.id,
        "titulo": doc.titulo,
        "mode": mode,
        "provider": registro_multi.get("provider"),
        "model": registro_multi.get("model"),
        "timestamp": datetime.now(UTC).isoformat(),
        "multiagente": registro_multi,
        "agente_unico": registro_unico,
        "delta": comparar_par(registro_multi, registro_unico),
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

    Mesmo motivo de ``ablacao_cross_review.separar_por_modo``: o modo mock não
    faz chamada LLM nem mede tokens/custo — misturá-lo ao agregado real
    produziria número errado.
    """
    reais = {k: v for k, v in pares.items() if v.get("mode") == "api"}
    mock = {k: v for k, v in pares.items() if v.get("mode") != "api"}
    return reais, mock


def gerar_conclusao(pares: dict) -> str:
    """Parágrafo objetivo, calculado (não redigido à mão) a partir dos deltas.

    Agrega SOMENTE as execuções reais (``mode == "api"``) — ver
    ``separar_por_modo``. Dentro delas, só entram no agregado os pares onde as
    DUAS execuções tiveram resultado 'sucesso'.
    """
    reais, mock = separar_por_modo(pares)
    comparaveis = {k: v for k, v in reais.items() if v["delta"].get("comparavel")}
    nao_comparaveis = sorted(set(reais) - set(comparaveis))

    if not comparaveis:
        linhas = [
            (
                "Nenhuma execução REAL (modo api) com sucesso nas duas topologias — "
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
            f"comparável(is) (sucesso nas duas topologias) de {len(reais)} rodado(s) — "
            f"{len(comparaveis) * 2} execuções reais de pipeline no total."
        ),
        (
            f"Chamadas LLM: {_media(chamadas_pct):+.1f}% em média no agente único "
            f"(esperado -{6 / 7 * 100:.0f}% estrutural: 1 chamada em vez de 7)."
            if chamadas_pct else "Chamadas LLM: sem dado."
        ),
        (
            f"Duração total: {_media(duracao_pct):+.1f}% em média no agente único."
            if duracao_pct else "Duração total: sem dado."
        ),
        (
            f"Tokens totais: {_media(tokens_pct):+.1f}% em média no agente único."
            if tokens_pct else "Tokens totais: sem dado."
        ),
        (
            f"Custo estimado: {_media(custo_pct):+.1f}% em média no agente único."
            if custo_pct else "Custo estimado: sem dado (sem preço configurado/tokens medidos)."
        ),
        (
            f"Decisão final MUDOU em {len(decisoes_mudaram)}/{len(comparaveis)} documento(s)"
            + (f": {', '.join(decisoes_mudaram)}." if decisoes_mudaram else ".")
        ),
        (
            f"Quantidade de críticas: variação média de {_media(criticas_deltas):+.1f} "
            "crítica(s) no agente único frente ao multiagente."
            if criticas_deltas else "Quantidade de críticas: sem dado."
        ),
    ]
    if nao_comparaveis:
        linhas.append(
            "Documentos não comparáveis (falha/bloqueio em alguma topologia): "
            f"{', '.join(nao_comparaveis)}."
        )
    if mock:
        linhas.append(_linha_mock(mock))

    linhas.append(
        "Leitura sugerida: se a decisão final e a quantidade de críticas NÃO mudam "
        "entre as duas topologias, a estrutura multiagente está pagando custo/tempo "
        "adicionais sem alterar o resultado nestes documentos — o valor dela, se "
        "houver, está na qualidade argumentativa e na especialização das críticas "
        "(texto de cada crítica em cada final_report.md), não capturada "
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
    """Arredonda para a tabela — o dado exato continua no .json ao lado."""
    return "n/d" if valor is None else f"{valor:.{casas}f}"


def _linhas_tabela(pares: dict) -> list[str]:
    linhas = [
        (
            "| doc_id | provider:model | chamadas (multi/único) | duração_s (multi/único) | "
            "Δduração | tokens (multi/único) | Δtokens | custo USD (multi/único) | Δcusto | "
            "decisão (multi/único) | críticas (multi/único) |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for doc_id in sorted(pares):
        par = pares[doc_id]
        multi, unico, delta = par["multiagente"], par["agente_unico"], par["delta"]
        linhas.append(
            "| " + " | ".join(
                _sanitizar(v) for v in (
                    doc_id,
                    f"{par.get('provider')}:{par.get('model')}",
                    f"{multi.get('chamadas_llm')}/{unico.get('chamadas_llm')}",
                    (
                        f"{_fmt_num(multi.get('duracao_total_s'), 2)}/"
                        f"{_fmt_num(unico.get('duracao_total_s'), 2)}"
                    ),
                    _fmt_pct(delta.get("duracao_total_s_variacao_pct")),
                    f"{multi.get('tokens_totais')}/{unico.get('tokens_totais')}",
                    _fmt_pct(delta.get("tokens_totais_variacao_pct")),
                    (
                        f"{_fmt_num(multi.get('custo_estimado'), 4)}/"
                        f"{_fmt_num(unico.get('custo_estimado'), 4)}"
                    ),
                    _fmt_pct(delta.get("custo_estimado_variacao_pct")),
                    f"{multi.get('decisao_final')}/{unico.get('decisao_final')}",
                    f"{multi.get('quantidade_criticas')}/{unico.get('quantidade_criticas')}",
                )
            ) + " |"
        )
    return linhas


def salvar(pares: dict, conclusao: str, destino_dir: Path = RESULTADOS_DIR) -> tuple[Path, Path]:
    destino_dir = Path(destino_dir)
    destino_dir.mkdir(parents=True, exist_ok=True)

    json_path = destino_dir / "comparativo_topologia.json"
    json_path.write_text(
        json.dumps(
            {"gerado_em": datetime.now(UTC).isoformat(), "conclusao": conclusao, "pares": pares},
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    reais, mock = separar_por_modo(pares)

    secoes = [
        "# Comparativo de topologia: multiagente (peer review) vs. agente único",
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
                f"{len(reais)} documento(s), cada um rodado 2x (multiagente e agente "
                f"único) = {len(reais) * 2} execuções reais de pipeline, com chamadas "
                "LLM, tokens e custo medidos."
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
                "Não é evidência sobre o efeito da topologia: em modo mock os "
                "pareceres/veredito vêm de um JSON pré-salvo, nenhuma chamada LLM "
                "acontece e não há tokens nem custo para medir. Serve para provar "
                "que a ferramenta roda de ponta a ponta de graça. **Estes números "
                "não entram em nenhuma média da seção anterior.**"
            ),
            "",
            *_linhas_tabela(mock),
        ]
    else:
        secoes.append("Nenhuma execução em modo mock registrada.")

    conteudo_md = "\n".join(secoes) + "\n"
    md_path = destino_dir / "comparativo_topologia.md"
    md_path.write_text(conteudo_md, encoding="utf-8")
    return json_path, md_path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Roda cada documento DUAS vezes (multiagente e agente único) e "
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
        "vezes (multiagente e agente único) — em modo api, o dobro do custo de "
        "executar.py para a mesma lista. Dispensado com --regerar.",
    )
    parser.add_argument(
        "--regerar", action="store_true",
        help="NÃO executa nada: recalcula a conclusão e reescreve o .json/.md a "
        "partir dos pares JÁ gravados em resultados/comparativo_topologia.json. "
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
    print("  CONCLUSÃO — topologia: multiagente (peer review) vs. agente único")
    print("=" * 74)
    print(conclusao)
    print(f"\nComparativo salvo em: {json_path} e {md_path}")


if __name__ == "__main__":
    main()
