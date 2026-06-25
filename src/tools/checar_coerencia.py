"""Coerência semântica determinística no contrato oficial (parecer E veredito).

Evolução direta da skill ``checar-coerencia`` da Atividade 5 (Grupo 2), adaptada
ao contrato oficial do Grupo 3 (``ReviewSchema`` e ``EditorVerdictSchema``).
Mantém a **construção** original — helpers ``_checar_*`` que devolvem
``{"tipo", "detalhe"}`` ou ``None``, lista de ``avisos`` para checagens puladas e
o ``score_coerencia`` como fração de verificações que passaram — e troca apenas o
QUE é comparado, para casar com os novos schemas.

Como ``validar_completude`` checa a ESTRUTURA (campos presentes, notas na faixa) e
o Pydantic do Grupo 1/3 BLOQUEIA na primeira falha de tipo, esta tool faz o que
nenhum dos dois pega: detecta **contradições semânticas** internas que passam por
todas as validações de tipo. Tudo determinístico, sem LLM e sobre ``dict`` puro
(só biblioteca padrão), para rodar offline em um clone limpo.

Conversão a partir da Atividade 5 (detalhe em ``docs/tools_reference.md``)
------------------------------------------------------------------------

| Atividade 5 (escala 1-5)        | Contrato oficial (escala 1-4)                |
|---------------------------------|----------------------------------------------|
| ``recomendacao_vs_nota``        | **removida** — não há mais campo ``recomendacao`` |
|                                 | (absorvido por ``nota_geral``/``decisao``).  |
| ``criterios_vs_nota`` (lim 1.5) | ``nota_vs_criterios`` (lim **1.0**), modo *parecer*. |
| ``evidencia_sem_ancoragem`` /   | **removidas** — o contrato oficial não tem    |
| ``evidencia_secao_invalida``    | campo ``evidencias``.                        |
| —                               | ``decisao_vs_notas`` (novo, modo *veredito*). |
| —                               | ``aceite_com_critica_bloqueante`` (novo).     |
| —                               | ``critica_sem_revisor`` (novo).               |

A checagem de notas-vs-nota passou a comparar **notas com notas** (critérios↔geral,
decisão↔revisores), o que dispensa o campo de texto livre ``recomendacao`` e é mais
robusto. O modo *veredito* é inteiramente novo: o ``EditorVerdictSchema`` não
existia na Atividade 5.
"""

from __future__ import annotations

# Rótulos da escala 1-4 (decisão / nota_geral). Copiados de ``ESCALA_VEREDITO``
# em ``src/review_schema.py`` para manter esta tool 100% sem dependências —
# importar o schema puxaria o Pydantic.
ESCALA_VEREDITO: dict[int, str] = {
    4: "Aceitar",
    3: "Aceitar com ressalvas",
    2: "Rejeitar com ressalvas",
    1: "Rejeitar",
}

#: Os quatro critérios cuja média é comparada com a ``nota_geral`` (modo parecer).
CRITERIOS_PARECER = ("solidez_tecnica", "originalidade", "significancia", "clareza")

#: Diferença máxima tolerada entre média de notas e a nota de síntese, na escala
#: 1-4. Era ``1.5`` na Atividade 5 (escala 1-5); reescalado para ``1.0`` aqui.
LIMIAR_DIVERGENCIA = 1.0


# ---------------------------------------------------------------------------
# Helpers de leitura defensiva (a entrada pode ter vindo de um LLM)
# ---------------------------------------------------------------------------

def _nota_bloco(valor: object) -> float | None:
    """Extrai a ``nota`` de um bloco ``{nota, justificativa}`` se for numérica."""
    if not isinstance(valor, dict):
        return None
    nota = valor.get("nota")
    if isinstance(nota, bool) or not isinstance(nota, (int, float)):
        return None
    return float(nota)


def _num(valor: object) -> float | None:
    """Converte um escalar numérico (não-bool) em float, senão None."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    return float(valor)


def _rotulo(nota: float) -> str:
    """Rótulo PT-BR da escala 1-4 para a nota inteira mais próxima."""
    return ESCALA_VEREDITO.get(int(round(nota)), "?")


# ---------------------------------------------------------------------------
# Checagens do modo PARECER (ReviewSchema) — devolvem {tipo, detalhe} | None
# ---------------------------------------------------------------------------

def _checar_nota_vs_criterios(notas_criterios: list[float], nota_geral: float) -> dict | None:
    """Média dos critérios diverge da ``nota_geral`` em mais que o limiar?

    Equivalente ao ``criterios_vs_nota`` da Atividade 5, reescalado para 1-4.
    """
    media = sum(notas_criterios) / len(notas_criterios)
    diferenca = abs(media - nota_geral)
    if diferenca > LIMIAR_DIVERGENCIA:
        return {
            "tipo": "nota_vs_criterios",
            "detalhe": (
                f"média dos critérios é {media:.2f} ({_rotulo(media)}), mas a "
                f"nota_geral é {nota_geral:.0f} ({_rotulo(nota_geral)}); "
                f"diferença {diferenca:.2f} > {LIMIAR_DIVERGENCIA:.1f}"
            ),
        }
    return None


# ---------------------------------------------------------------------------
# Checagens do modo VEREDITO (EditorVerdictSchema) — {tipo, detalhe} | None
# ---------------------------------------------------------------------------

def _checar_decisao_vs_notas(decisao: float, notas: dict[str, float]) -> dict | None:
    """Decisão editorial diverge da média das notas dos revisores?"""
    media = sum(notas.values()) / len(notas)
    diferenca = abs(decisao - media)
    if diferenca > LIMIAR_DIVERGENCIA:
        return {
            "tipo": "decisao_vs_notas",
            "detalhe": (
                f"decisão é {decisao:.0f} ({_rotulo(decisao)}), mas a média das notas "
                f"dos revisores é {media:.2f} ({_rotulo(media)}); "
                f"diferença {diferenca:.2f} > {LIMIAR_DIVERGENCIA:.1f}"
            ),
        }
    return None


def _checar_aceite_com_critica_bloqueante(decisao: float, criticas: list) -> dict | None:
    """Decisão 4 (Aceitar sem ressalvas) convivendo com crítica bloqueante?"""
    if int(round(decisao)) != 4:
        return None
    bloqueantes = [c for c in criticas if isinstance(c, dict) and c.get("tipo") == "critica"]
    if not bloqueantes:
        return None
    return {
        "tipo": "aceite_com_critica_bloqueante",
        "detalhe": (
            f"decisão 4 (Aceitar) apesar de {len(bloqueantes)} crítica(s) bloqueante(s) "
            "(tipo='critica'): "
            + "; ".join(
                (str(c.get("texto", "")).strip()[:80] or "(sem texto)") for c in bloqueantes
            )
        ),
    }


def _checar_critica_sem_revisor(criticas: list, notas: dict[str, float]) -> dict | None:
    """Crítica atribuída a revisor que não consta em ``notas_por_revisor``?"""
    orfas = sorted({
        c["revisor"]
        for c in criticas
        if isinstance(c, dict)
        and isinstance(c.get("revisor"), str)
        and c["revisor"].strip()
        and c["revisor"] not in notas
    })
    if not orfas:
        return None
    return {
        "tipo": "critica_sem_revisor",
        "detalhe": (
            "crítica(s) atribuída(s) a revisor(es) fora de notas_por_revisor: "
            + ", ".join(orfas)
            + f" (revisores conhecidos: {', '.join(sorted(notas))})"
        ),
    }


# ---------------------------------------------------------------------------
# Montagem do resultado (mesma fórmula de score da Atividade 5)
# ---------------------------------------------------------------------------

def _erro(mensagem: str) -> dict:
    return {
        "status": "erro",
        "tipo": None,
        "coerente": False,
        "inconsistencias": [],
        "avisos": [mensagem],
        "score_coerencia": 0.0,
    }


def _montar(tipo: str, inconsistencias: list[dict], avisos: list[str], checks_total: int) -> dict:
    """``score = (checks_total - checks_falhos) / checks_total`` — como na Atividade 5.

    ``checks_total`` é o número de checagens efetivamente avaliadas (as puladas
    viram ``avisos`` e não entram no denominador). Sem nenhuma checagem aplicável,
    o score é 1.0: nada incoerente foi encontrado.
    """
    checks_falhos = len(inconsistencias)
    if checks_total <= 0:
        score = 1.0
    else:
        score = round((checks_total - checks_falhos) / checks_total, 4)
    return {
        "status": "ok",
        "tipo": tipo,
        "coerente": checks_falhos == 0,
        "inconsistencias": inconsistencias,
        "avisos": avisos,
        "score_coerencia": score,
    }


# ---------------------------------------------------------------------------
# Orquestração por modo
# ---------------------------------------------------------------------------

def _checar_parecer(parecer: dict) -> dict:
    inconsistencias: list[dict] = []
    avisos: list[str] = []
    checks_total = 0

    notas, faltando = [], []
    for criterio in CRITERIOS_PARECER:
        nota = _nota_bloco(parecer.get(criterio))
        (notas.append(nota) if nota is not None else faltando.append(criterio))
    nota_geral = _nota_bloco(parecer.get("nota_geral"))

    if faltando:
        avisos.append("critérios sem nota numérica, ignorados na média: " + ", ".join(faltando))

    # Única checagem do modo parecer: média dos critérios vs nota_geral.
    if nota_geral is None:
        avisos.append("'nota_geral' ausente ou sem nota numérica: coerência não verificável")
    elif not notas:
        avisos.append("nenhum critério com nota numérica: coerência não verificável")
    else:
        checks_total += 1
        if (inc := _checar_nota_vs_criterios(notas, nota_geral)):
            inconsistencias.append(inc)

    return _montar("parecer", inconsistencias, avisos, checks_total)


def _notas_revisor(valor: object) -> dict[str, float]:
    """Mapa revisor -> nota numérica, ignorando entradas malformadas."""
    if not isinstance(valor, dict):
        return {}
    limpas: dict[str, float] = {}
    for revisor, nota in valor.items():
        if isinstance(revisor, str) and revisor.strip() and (n := _num(nota)) is not None:
            limpas[revisor] = n
    return limpas


def _checar_veredito(veredito: dict) -> dict:
    inconsistencias: list[dict] = []
    avisos: list[str] = []
    checks_total = 0

    decisao = _num(veredito.get("decisao"))
    notas = _notas_revisor(veredito.get("notas_por_revisor"))
    criticas = veredito.get("criticas")
    criticas = criticas if isinstance(criticas, list) else []

    # 1) decisão vs média das notas dos revisores.
    if decisao is None:
        avisos.append("'decisao' ausente ou não numérica: checagem decisao_vs_notas pulada")
    elif not notas:
        avisos.append("'notas_por_revisor' vazio/ausente: checagem decisao_vs_notas pulada")
    else:
        checks_total += 1
        if (inc := _checar_decisao_vs_notas(decisao, notas)):
            inconsistencias.append(inc)

    # 2) aceite pleno (decisão 4) com crítica bloqueante.
    if decisao is None:
        avisos.append("'decisao' ausente: checagem aceite_com_critica_bloqueante pulada")
    else:
        checks_total += 1
        if (inc := _checar_aceite_com_critica_bloqueante(decisao, criticas)):
            inconsistencias.append(inc)

    # 3) crítica sem revisor rastreável em notas_por_revisor.
    if not notas:
        avisos.append("'notas_por_revisor' vazio/ausente: checagem critica_sem_revisor pulada")
    else:
        checks_total += 1
        if (inc := _checar_critica_sem_revisor(criticas, notas)):
            inconsistencias.append(inc)

    return _montar("veredito", inconsistencias, avisos, checks_total)


# ---------------------------------------------------------------------------
# Resolução de tipo + API pública
# ---------------------------------------------------------------------------

def _resolver_tipo(dado: dict, tipo: str) -> str:
    if tipo in ("parecer", "veredito"):
        return tipo
    if tipo != "auto":
        raise ValueError("tipo deve ser 'parecer', 'veredito' ou 'auto'")
    # Mesma heurística de validar_completude: só o veredito tem 'decisao'.
    return "veredito" if "decisao" in dado else "parecer"


def checar_coerencia(dado: dict, tipo: str = "auto") -> dict:
    """Audita a coerência semântica de um parecer ou de um veredito do contrato oficial.

    Detecta contradições que passam pela validação de tipo (Pydantic) e por
    ``validar_completude``: uma nota geral que não reflete os critérios (parecer),
    uma decisão editorial desalinhada das notas dos revisores, um aceite pleno
    convivendo com uma crítica bloqueante, ou uma crítica sem revisor rastreável
    (veredito). Tudo determinístico, sem LLM, sobre ``dict`` puro.

    Diferente da versão da Atividade 5 (que retornava ``status="erro"`` quando
    faltavam ``nota_geral``/``recomendacao``), aqui a tool é **defensiva**: campos
    ausentes/malformados viram ``avisos`` (checagem pulada) em vez de erro, porque
    no pipeline integrado quem barra a entrada é o gate ``validar_completude``,
    chamado antes. ``status`` só é ``"erro"`` quando a entrada não é um ``dict``.

    Args:
        dado: Parecer (``ReviewSchema``) ou veredito (``EditorVerdictSchema``) já
            carregado como dicionário.
        tipo: ``"parecer"``, ``"veredito"`` ou ``"auto"`` (default). Em ``"auto"``,
            a presença do campo ``decisao`` identifica um veredito.

    Returns:
        Dicionário com ``status`` (``"ok"`` salvo entrada não-dict), ``tipo``
        (parecer/veredito), ``coerente`` (bool), ``inconsistencias``
        (list de ``{tipo, detalhe}``), ``avisos`` (list[str] de checagens puladas)
        e ``score_coerencia`` (float 0..1: fração das checagens aplicáveis que
        passaram).
    """
    if not isinstance(dado, dict):
        return _erro("entrada invalida: esperado um dict (parecer ou veredito)")

    if _resolver_tipo(dado, tipo) == "veredito":
        return _checar_veredito(dado)
    return _checar_parecer(dado)


if __name__ == "__main__":
    import json

    # Parecer coerente: média dos critérios (3,2,3,4 → 3.0) bate com nota_geral 3.
    parecer_ok = {
        "revisor": "statistician",
        "solidez_tecnica": {"nota": 3, "justificativa": "Métodos adequados."},
        "originalidade": {"nota": 2, "justificativa": "Incremental."},
        "significancia": {"nota": 3, "justificativa": "Relevância clínica."},
        "clareza": {"nota": 4, "justificativa": "Bem escrito."},
        "nota_geral": {"nota": 3, "justificativa": "Aceitar com ressalvas."},
        "confianca": {"nota": 3, "justificativa": "Dentro da especialidade."},
    }
    # Veredito incoerente: decisão 4 (Aceitar) com notas baixas + crítica bloqueante
    # + crítica de revisor fora de notas_por_revisor.
    veredito_incoerente = {
        "decisao": 4,
        "justificativa": "Aceito o artigo.",
        "sintese": "Resumo do artigo.",
        "notas_por_revisor": {"statistician": 2, "domain_expert": 2, "copyeditor": 3},
        "criticas": [
            {"revisor": "statistician", "tipo": "critica",
             "texto": "Sem validação externa nem intervalos de confiança."},
            {"revisor": "revisor_fantasma", "tipo": "fraqueza",
             "texto": "Crítica de um revisor que não tem nota."},
        ],
    }
    print("# parecer (coerente)")
    print(json.dumps(checar_coerencia(parecer_ok), indent=2, ensure_ascii=False))
    print("\n# veredito (incoerente)")
    print(json.dumps(checar_coerencia(veredito_incoerente), indent=2, ensure_ascii=False))
