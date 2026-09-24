"""Comparativo de TRÊS topologias — agente único, sem leitura cruzada e completo.

Responde à pergunta do plano da Frente 1 (ver
``docs/protocolo_experimento_topologia.md`` e o plano da Atividade_7): quanto
cada camada de especialização contribui pro veredito, em dois saltos —

- **Salto 1** (C0 → C1): agente único (``single_agent=True``, 1 chamada LLM)
  → 3 revisores especializados SEM leitura cruzada (``cross_review=False``,
  4 chamadas: 3 revisores + 1 editor). O que os revisores especializados
  entregam sozinhos, antes de qualquer leitura cruzada.
- **Salto 2** (C1 → C2): sem leitura cruzada → pipeline completo
  (``cross_review=True``, o default, 7 chamadas: 3 revisores + 3 leituras
  cruzadas + 1 editor). O que a leitura cruzada agrega em cima disso.

mais o total (C0 → C2), como referência de ponta a ponta. Roda o MESMO
documento, no MESMO modo e com o MESMO provedor/modelo, TRÊS vezes — uma por
topologia — e registra as três execuções lado a lado, com os dois saltos e o
total calculados entre elas.

Reaproveita ``executar.processar_documento`` (mesma execução, mesmo
diagnóstico, mesmos campos de configuração) três vezes por documento — não
duplica a lógica de rodar o pipeline nem de extrair o resumo/veredito. A
variante ``cross_review=False`` (Salto 1→2) é a MESMA já usada e testada em
``ablacao_cross_review.py`` (comparativo dedicado do Grupo 2 pro eixo leitura
cruzada isoladamente) — aqui ela entra como o degrau do meio de uma
comparação de três pontas, não como lógica nova.

"Qualidade" aqui é um proxy OBJETIVO (decisão final, quantidade de críticas,
quantas são bloqueantes) — não uma nota de qualidade textual das críticas, que
exigiria julgamento humano ou um LLM-juiz e está fora do escopo desta
ferramenta. O sinal comum às três topologias é a ``decisao`` final (mesma
escala 1-4 nas três). ``notas_por_revisor`` só é comparável entre C1 e C2
(os mesmos três revisores), então entra só no Salto 2; o agente único tem uma
única nota sintética e fica de fora dessa comparação.

Uso:
    python -m src.benchmark.comparar_topologia --mode mock --docs exemplo_mock
    python -m src.benchmark.comparar_topologia --mode api --docs doc_1,doc_2

Sequencial por documento e por topologia (nunca em paralelo) — mesmo motivo de
``executar.py``: não estourar rate limit e manter o custo do modo ``api``
previsível. Cada documento roda TRÊS vezes em modo ``api``: o triplo do custo
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

#: Campos numéricos comparados par a par entre duas execuções quaisquer.
CAMPOS_DELTA_NUMERICO = ("duracao_total_s", "tokens_totais", "custo_estimado", "chamadas_llm")

#: Ressalva obrigatória em toda conclusão gerada. Ver docstring do módulo.
LIMITE_QUALIDADE = (
    "LIMITE DESTA AVALIAÇÃO: 'qualidade' aqui é medida por indicadores "
    "automáticos (decisão final na escala 1-4, quantidade de críticas, "
    "quantas são bloqueantes). NÃO houve avaliação humana do conteúdo das "
    "críticas — nenhuma pessoa leu os pareceres para julgar se são "
    "pertinentes ou bem argumentados, nem se uma topologia mais especializada "
    "produz críticas mais específicas ou melhor fundamentadas que uma mais "
    "simples. 'notas_por_revisor' só é comparável entre C1 e C2 (os mesmos "
    "três revisores) e por isso aparece só no Salto 2; com o agente único "
    "(C0) os formatos diferem e ela fica de fora. Os números abaixo "
    "dizem quanto cada salto de especialização CUSTA a mais e se ele MUDA o "
    "resultado, não se ele o MELHORA."
)


def _variacao_percentual(base: float | None, comparado: float | None) -> float | None:
    """Variação de 'comparado' em relação a 'base', em %.

    ``None`` se algum lado for ``None``/indisponível. Negativo = 'comparado'
    consumiu MENOS que 'base'. ``base == 0`` não é um caso real (toda
    execução bem-sucedida tem duração/chamadas > 0) — devolve ``None`` em vez
    de dividir por zero.
    """
    if base is None or comparado is None or base == 0:
        return None
    return (comparado - base) / base * 100.0


def comparar_par(registro_base: dict, registro_comparado: dict) -> dict:
    """Monta o bloco ``delta`` entre duas execuções do MESMO documento.

    Genérico o bastante pra qualquer par de topologias — usado tanto pros
    saltos individuais (C0→C1, C1→C2) quanto pro total (C0→C2), só trocando
    quais dois registros entram como ``registro_base``/``registro_comparado``.
    """
    delta: dict = {}
    for campo in CAMPOS_DELTA_NUMERICO:
        delta[f"{campo}_variacao_pct"] = _variacao_percentual(
            registro_base.get(campo), registro_comparado.get(campo)
        )

    ambos_sucesso = (
        registro_base.get("resultado") == "sucesso"
        and registro_comparado.get("resultado") == "sucesso"
    )
    delta["comparavel"] = ambos_sucesso
    if not ambos_sucesso:
        return delta

    delta["decisao_mudou"] = (
        registro_base.get("decisao_final") != registro_comparado.get("decisao_final")
    )
    qc_base = registro_base.get("quantidade_criticas")
    qc_comp = registro_comparado.get("quantidade_criticas")
    delta["quantidade_criticas_delta"] = (
        qc_comp - qc_base if qc_base is not None and qc_comp is not None else None
    )
    qcb_base = registro_base.get("quantidade_criticas_bloqueantes")
    qcb_comp = registro_comparado.get("quantidade_criticas_bloqueantes")
    delta["quantidade_criticas_bloqueantes_delta"] = (
        qcb_comp - qcb_base if qcb_base is not None and qcb_comp is not None else None
    )
    delta["requer_revisao_humana_mudou"] = (
        registro_base.get("requer_revisao_humana")
        != registro_comparado.get("requer_revisao_humana")
    )
    # Só comparável com os MESMOS revisores dos dois lados (C1 vs C2). Com o
    # agente único (C0), as chaves diferem e a comparação não teria sentido.
    notas_base = registro_base.get("notas_por_revisor")
    notas_comp = registro_comparado.get("notas_por_revisor")
    if notas_base and notas_comp and set(notas_base) == set(notas_comp):
        delta["notas_por_revisor_mudaram"] = notas_base != notas_comp
    return delta


def _todas_sucesso(execucoes: dict) -> bool:
    return all(registro.get("resultado") == "sucesso" for registro in execucoes.values())


def rodar_trio(doc, *, mode: str, cache_dir: Path) -> dict:
    """Roda o mesmo documento nas três topologias e monta o registro comparável.

    Ordem C0 → C1 → C2. A execução C1 (``cross_review=False``) usa a MESMA
    chamada já validada em
    ``ablacao_cross_review.py`` — não é lógica nova, só reaproveitada aqui
    como o degrau do meio.
    """
    print(f"\n=== {doc.id}: AGENTE ÚNICO (C0 — baseline experimental) ===")
    registro_agente_unico, _ = processar_documento(
        doc, mode=mode, cache_dir=cache_dir, single_agent=True,
    )
    print(f"\n=== {doc.id}: SEM LEITURA CRUZADA (C1) ===")
    registro_sem_leitura_cruzada, _ = processar_documento(
        doc, mode=mode, cache_dir=cache_dir, cross_review=False, single_agent=False,
    )
    print(f"\n=== {doc.id}: COMPLETO (C2 — peer review com leitura cruzada) ===")
    registro_completo, _ = processar_documento(
        doc, mode=mode, cache_dir=cache_dir, cross_review=True, single_agent=False,
    )

    execucoes = {
        "agente_unico": registro_agente_unico,
        "sem_leitura_cruzada": registro_sem_leitura_cruzada,
        "completo": registro_completo,
    }

    return {
        "doc_id": doc.id,
        "titulo": doc.titulo,
        "mode": mode,
        "provider": registro_completo.get("provider"),
        "model": registro_completo.get("model"),
        "timestamp": datetime.now(UTC).isoformat(),
        "execucoes": execucoes,
        "comparavel": _todas_sucesso(execucoes),
        "saltos": {
            "agente_unico_para_sem_leitura_cruzada": comparar_par(
                registro_agente_unico, registro_sem_leitura_cruzada
            ),
            "sem_leitura_cruzada_para_completo": comparar_par(
                registro_sem_leitura_cruzada, registro_completo
            ),
        },
        "delta_total": comparar_par(registro_agente_unico, registro_completo),
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


def _resumir_bloco(deltas_por_doc: dict[str, dict], *, titulo: str, nota_chamadas: str = "") -> list[str]:
    """Linhas de resumo (chamadas/duração/tokens/custo/decisão/críticas) pra
    um conjunto de deltas comparáveis — reaproveitado pro Salto 1, Salto 2 e
    pro total, cada chamada só troca QUAL dicionário de deltas está sendo
    agregado (a agregação em si é idêntica nos três casos).
    """
    n = len(deltas_por_doc)
    chamadas_pct = [
        d["chamadas_llm_variacao_pct"] for d in deltas_por_doc.values()
        if d["chamadas_llm_variacao_pct"] is not None
    ]
    duracao_pct = [
        d["duracao_total_s_variacao_pct"] for d in deltas_por_doc.values()
        if d["duracao_total_s_variacao_pct"] is not None
    ]
    tokens_pct = [
        d["tokens_totais_variacao_pct"] for d in deltas_por_doc.values()
        if d["tokens_totais_variacao_pct"] is not None
    ]
    custo_pct = [
        d["custo_estimado_variacao_pct"] for d in deltas_por_doc.values()
        if d["custo_estimado_variacao_pct"] is not None
    ]
    decisoes_mudaram = [doc_id for doc_id, d in deltas_por_doc.items() if d["decisao_mudou"]]
    criticas_deltas = [
        d["quantidade_criticas_delta"] for d in deltas_por_doc.values()
        if d["quantidade_criticas_delta"] is not None
    ]
    notas_comparadas = {
        doc_id: d["notas_por_revisor_mudaram"] for doc_id, d in deltas_por_doc.items()
        if "notas_por_revisor_mudaram" in d
    }

    linhas = [
        f"### {titulo}",
        (
            f"Chamadas LLM: {_media(chamadas_pct):+.1f}% em média.{nota_chamadas}"
            if chamadas_pct else "Chamadas LLM: sem dado."
        ),
        (
            f"Duração total: {_media(duracao_pct):+.1f}% em média."
            if duracao_pct else "Duração total: sem dado."
        ),
        (
            f"Tokens totais: {_media(tokens_pct):+.1f}% em média."
            if tokens_pct else "Tokens totais: sem dado."
        ),
        (
            f"Custo estimado: {_media(custo_pct):+.1f}% em média."
            if custo_pct else "Custo estimado: sem dado (sem preço configurado/tokens medidos)."
        ),
        (
            f"Decisão final mudou em {len(decisoes_mudaram)}/{n} documento(s)"
            + (f": {', '.join(decisoes_mudaram)}." if decisoes_mudaram else ".")
        ),
        (
            f"Quantidade de críticas: variação média de {_media(criticas_deltas):+.1f} crítica(s)."
            if criticas_deltas else "Quantidade de críticas: sem dado."
        ),
    ]
    if notas_comparadas:
        notas_mudaram = sorted(doc_id for doc_id, mudou in notas_comparadas.items() if mudou)
        linhas.append(
            f"Notas por revisor mudaram em {len(notas_mudaram)}/{len(notas_comparadas)} documento(s)"
            + (f": {', '.join(notas_mudaram)}." if notas_mudaram else ".")
        )
    return linhas


def gerar_conclusao(pares: dict) -> str:
    """Parágrafo objetivo, calculado (não redigido à mão) a partir dos deltas.

    Agrega SOMENTE as execuções reais (``mode == "api"``) — ver
    ``separar_por_modo``. Dentro delas, só entram no agregado os pares onde as
    TRÊS execuções tiveram resultado 'sucesso'. Reporta Salto 1 (C0→C1) e
    Salto 2 (C1→C2) separados — é isso que testa a hipótese do plano (qual
    salto tem mais Δqualidade por Δcusto) — mais o total (C0→C2) como
    referência de ponta a ponta.
    """
    reais, mock = separar_por_modo(pares)
    comparaveis = {k: v for k, v in reais.items() if v.get("comparavel")}
    nao_comparaveis = sorted(set(reais) - set(comparaveis))

    if not comparaveis:
        linhas = [
            (
                "Nenhuma execução REAL (modo api) com sucesso nas três topologias — "
                "sem base para uma conclusão numérica."
            )
        ]
        if nao_comparaveis:
            linhas.append(f"Documentos não comparáveis: {', '.join(nao_comparaveis)}.")
        if mock:
            linhas.append(_linha_mock(mock))
        return "\n".join(linhas)

    linhas = [
        (
            f"EXECUÇÕES REAIS (modo api): {len(comparaveis)} documento(s) "
            f"comparável(is) (sucesso nas três topologias) de {len(reais)} rodado(s) — "
            f"{len(comparaveis) * 3} execuções reais de pipeline no total."
        ),
        "",
    ]
    linhas += _resumir_bloco(
        {k: v["saltos"]["agente_unico_para_sem_leitura_cruzada"] for k, v in comparaveis.items()},
        titulo="Salto 1 — agente único → sem leitura cruzada (C0→C1)",
    )
    linhas.append("")
    linhas += _resumir_bloco(
        {k: v["saltos"]["sem_leitura_cruzada_para_completo"] for k, v in comparaveis.items()},
        titulo="Salto 2 — sem leitura cruzada → completo (C1→C2)",
    )
    linhas.append("")
    linhas += _resumir_bloco(
        {k: v["delta_total"] for k, v in comparaveis.items()},
        titulo="Total — agente único → completo (C0→C2)",
        nota_chamadas=" (esperado +600% estrutural: 7 chamadas em vez de 1.)",
    )
    linhas.append("")

    if nao_comparaveis:
        linhas.append(
            "Documentos não comparáveis (falha/bloqueio em alguma topologia): "
            f"{', '.join(nao_comparaveis)}."
        )
    if mock:
        linhas.append(_linha_mock(mock))

    linhas.append(
        "Leitura sugerida: compare o Δqualidade/Δcusto de cada salto (decisão "
        "mudou? quantidade de críticas mudou? a que custo em tempo/tokens?). Se "
        "um salto NÃO muda decisão nem críticas, aquela camada está pagando "
        "custo/tempo adicionais sem alterar o resultado nestes documentos — o "
        "valor dela, se houver, está na qualidade argumentativa e na "
        "especialização das críticas (texto de cada crítica em cada "
        "final_report.md), não capturada numericamente aqui. Note também que "
        "o Salto 1 tem base pequena (1 chamada LLM) — a variação percentual "
        "amplifica, então vale conferir os números absolutos na tabela."
    )
    linhas.append(LIMITE_QUALIDADE)
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Persistência e exibição
# ---------------------------------------------------------------------------

def _arquivar_pares_legado(pares_legado: dict, caminho: Path) -> Path:
    """Preserva pares no formato PAREADO antigo num arquivo separado, sem perder o dado.

    Mescla com um arquivamento anterior em vez de sobrescrevê-lo. Se o mesmo
    ``doc_id`` já estiver arquivado com conteúdo DIFERENTE (ex.: um JSON
    antigo restaurado de outra branch), o novo entra sob uma chave com o
    timestamp do par — nenhum dos dois é descartado.
    """
    destino = caminho.with_name(f"{caminho.stem}_legado{caminho.suffix}")
    agora = datetime.now(UTC).isoformat()
    arquivados = (
        json.loads(destino.read_text(encoding="utf-8")).get("pares", {})
        if destino.exists() else {}
    )
    for doc_id, par in pares_legado.items():
        if doc_id not in arquivados:
            arquivados[doc_id] = par
        elif arquivados[doc_id] != par:
            arquivados[f"{doc_id}@{par.get('timestamp', agora)}"] = par

    destino.write_text(
        json.dumps(
            {
                "aviso": (
                    "Pares no formato PAREADO antigo (multiagente/agente_unico/"
                    "delta), de antes deste comparativo virar três vias. Preservado "
                    "aqui só pra não perder dado de execução real já pago em API — "
                    "não é lido nem agregado pelo comparativo de três vias."
                ),
                "atualizado_em": agora,
                "pares": arquivados,
            },
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return destino


def _carregar_pares_existentes(caminho: Path) -> dict:
    """Carrega os pares já gravados, arquivando (sem descartar) o formato PAREADO antigo.

    Antes desta ferramenta virar três vias, ``pares[doc_id]`` guardava
    ``"multiagente"``/``"agente_unico"``/``"delta"`` em vez de
    ``"execucoes"``/``"saltos"``/``"comparavel"``. Uma entrada nesse formato
    antigo não tem como virar três vias sem rodar a topologia C1 de novo (o
    dado dela nunca existiu) — em vez de simplesmente excluir esses pares
    (o próximo ``salvar()`` reescreveria o arquivo sem eles, perdendo pra
    sempre uma execução real que já custou API), eles são movidos pro arquivo
    de legado ANTES de sumirem do arquivo principal.
    """
    if not caminho.exists():
        return {}
    pares = json.loads(caminho.read_text(encoding="utf-8")).get("pares", {})
    validos = {k: v for k, v in pares.items() if "execucoes" in v}
    legado = {k: v for k, v in pares.items() if k not in validos}
    if legado:
        destino = _arquivar_pares_legado(legado, caminho)
        print(
            f"[aviso] {len(legado)} par(es) no formato antigo (pareado, sem C1) "
            f"movido(s) para '{destino.name}': {', '.join(sorted(legado))}. "
            "Rode-os de novo com --docs para entrarem no comparativo de três vias."
        )
    return validos


def _sanitizar(texto: str) -> str:
    return str(texto).replace("|", "\\|").replace("\n", " ")


def _fmt_num(valor, casas: int) -> str:
    """Arredonda para a tabela — o dado exato continua no .json ao lado."""
    return "n/d" if valor is None else f"{valor:.{casas}f}"


def _linhas_tabela(pares: dict) -> list[str]:
    """Uma linha por documento, com os valores brutos das três topologias lado a lado.

    Os deltas (Salto 1, Salto 2, total) já ficam na conclusão em texto — com
    três topologias, uma coluna de delta por métrica por salto deixaria a
    tabela larga demais (2 saltos x 4 métricas). A tabela aqui é só o dado
    bruto, na ordem C0/C1/C2; o .json ao lado tem os deltas completos.
    """
    linhas = [
        (
            "| doc_id | provider:model | chamadas (C0/C1/C2) | duração_s (C0/C1/C2) | "
            "tokens (C0/C1/C2) | custo USD (C0/C1/C2) | decisão (C0/C1/C2) | "
            "críticas (C0/C1/C2) |"
        ),
        "|---|---|---|---|---|---|---|---|",
    ]
    for doc_id in sorted(pares):
        par = pares[doc_id]
        execucoes = par["execucoes"]
        c0, c1, c2 = (
            execucoes["agente_unico"], execucoes["sem_leitura_cruzada"], execucoes["completo"],
        )
        linhas.append(
            "| " + " | ".join(
                _sanitizar(v) for v in (
                    doc_id,
                    f"{par.get('provider')}:{par.get('model')}",
                    f"{c0.get('chamadas_llm')}/{c1.get('chamadas_llm')}/{c2.get('chamadas_llm')}",
                    (
                        f"{_fmt_num(c0.get('duracao_total_s'), 2)}/"
                        f"{_fmt_num(c1.get('duracao_total_s'), 2)}/"
                        f"{_fmt_num(c2.get('duracao_total_s'), 2)}"
                    ),
                    f"{c0.get('tokens_totais')}/{c1.get('tokens_totais')}/{c2.get('tokens_totais')}",
                    (
                        f"{_fmt_num(c0.get('custo_estimado'), 4)}/"
                        f"{_fmt_num(c1.get('custo_estimado'), 4)}/"
                        f"{_fmt_num(c2.get('custo_estimado'), 4)}"
                    ),
                    f"{c0.get('decisao_final')}/{c1.get('decisao_final')}/{c2.get('decisao_final')}",
                    (
                        f"{c0.get('quantidade_criticas')}/{c1.get('quantidade_criticas')}/"
                        f"{c2.get('quantidade_criticas')}"
                    ),
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
        "# Comparativo de topologia: agente único vs. sem leitura cruzada vs. completo",
        "",
        f"Gerado em: {datetime.now(UTC).isoformat()}",
        "",
        "C0 = agente único · C1 = multiagente sem leitura cruzada · "
        "C2 = multiagente completo (com leitura cruzada).",
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
                f"{len(reais)} documento(s), cada um rodado 3x (C0, C1 e C2) = "
                f"{len(reais) * 3} execuções reais de pipeline, com chamadas LLM, "
                "tokens e custo medidos."
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
            "Roda cada documento TRÊS vezes (agente único, sem leitura cruzada e "
            "completo) e compara tokens/duração/custo/decisão/críticas nos dois "
            "saltos entre elas."
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
        help="ids do manifesto a rodar, separados por vírgula. Cada um roda TRÊS "
        "vezes (agente único, sem leitura cruzada e completo) — em modo api, o "
        "triplo do custo de executar.py para a mesma lista. Dispensado com "
        "--regerar.",
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
        pares[doc_id] = rodar_trio(mapa[doc_id], mode=args.mode, cache_dir=cache_dir)

    conclusao = gerar_conclusao(pares)
    json_path, md_path = salvar(pares, conclusao)

    print("\n" + "=" * 74)
    print("  CONCLUSÃO — topologia: agente único vs. sem leitura cruzada vs. completo")
    print("=" * 74)
    print(conclusao)
    print(f"\nComparativo salvo em: {json_path} e {md_path}")


if __name__ == "__main__":
    main()
