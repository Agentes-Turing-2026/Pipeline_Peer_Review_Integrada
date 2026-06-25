"""Validação determinística de completude no contrato oficial (parecer E veredito).

Esta tool audita a ESTRUTURA de duas saídas do pipeline, sem qualquer chamada a
modelo de linguagem:

- um **parecer de revisor** no formato ``ReviewSchema`` (Fase 1/2); e
- o **veredito do editor** no formato ``EditorVerdictSchema`` (Fase 3).

Diferente da validação Pydantic do Grupo 1 — que levanta na primeira falha e BLOQUEIA
o pipeline —, aqui o objetivo é AUDITAR: percorrer o dicionário bruto, reportar *todos*
os problemas de uma vez e devolver um ``score_completude``. Por isso opera sobre
``dict`` puro (pré-validação), usando só a biblioteca padrão.

Adaptação a partir da Atividade 5 (ver ``docs/tools_reference.md``):
- escala de notas mudou de 1-5 para **1-4** (e a confiança usa **1-3**);
- os critérios passaram a ser ``solidez_tecnica``, ``originalidade``, ``significancia``
  e ``clareza`` (antes: originalidade/metodologia/clareza/relevancia);
- cada critério agora é um BLOCO ``{nota, justificativa}`` (antes a nota era um int
  solto e havia campos ``recomendacao``/``evidencias``, que não existem no contrato
  oficial).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Parecer (ReviewSchema)
# ---------------------------------------------------------------------------

#: Campo de identificação obrigatório do parecer (string não vazia).
CAMPO_REVISOR = "revisor"

#: Blocos {nota, justificativa} do parecer cuja nota vai de 1 a 4.
BLOCOS_NOTA_1_4 = (
    "solidez_tecnica",
    "originalidade",
    "significancia",
    "clareza",
    "nota_geral",
)

#: Bloco {nota, justificativa} cuja nota vai de 1 a 3 (confiança do revisor).
BLOCO_NOTA_1_3 = "confianca"

#: Campos obrigatórios do parecer, em ordem canônica.
CAMPOS_PARECER = (CAMPO_REVISOR, *BLOCOS_NOTA_1_4, BLOCO_NOTA_1_3)

# ---------------------------------------------------------------------------
# Veredito (EditorVerdictSchema)
# ---------------------------------------------------------------------------

#: Campos obrigatórios do veredito (``criticas`` e ``recomendacoes_aos_autores`` são
#: opcionais no schema — têm default ``[]`` —, então não entram aqui; mas, se presentes,
#: têm seus itens validados e reportados como inválidos quando malformados).
CAMPOS_VEREDITO = ("decisao", "justificativa", "sintese", "notas_por_revisor")

TIPOS_CRITICA_VALIDOS = ("fraqueza", "critica")


# ---------------------------------------------------------------------------
# Validadores compartilhados
# ---------------------------------------------------------------------------

def _validar_texto(valor: object) -> str | None:
    if not isinstance(valor, str) or not valor.strip():
        return "esperado string nao vazia"
    return None


def _validar_inteiro(valor: object, nota_min: int, nota_max: int) -> str | None:
    if isinstance(valor, bool) or not isinstance(valor, int):
        return "esperado inteiro"
    if not nota_min <= valor <= nota_max:
        return f"fora do intervalo {nota_min}..{nota_max}"
    return None


def _validar_bloco(valor: object, nota_min: int, nota_max: int) -> str | None:
    """Valida um bloco {nota, justificativa} (critério/síntese do parecer)."""
    if not isinstance(valor, dict):
        return "esperado objeto com 'nota' e 'justificativa'"

    erros: list[str] = []
    if "nota" not in valor:
        erros.append("'nota' ausente")
    else:
        motivo = _validar_inteiro(valor["nota"], nota_min, nota_max)
        if motivo:
            erros.append(f"'nota' {motivo}")

    if "justificativa" not in valor:
        erros.append("'justificativa' ausente")
    elif _validar_texto(valor["justificativa"]):
        erros.append("'justificativa' vazia ou nao e string")

    return "; ".join(erros) if erros else None


def _validar_notas_por_revisor(valor: object) -> str | None:
    if not isinstance(valor, dict):
        return "esperado dict revisor -> nota (1-4)"
    if not valor:
        return "dict vazio: a decisao precisa estar ancorada nas notas dos revisores"
    for revisor, nota in valor.items():
        if not isinstance(revisor, str) or not revisor.strip():
            return "ha um identificador de revisor vazio"
        motivo = _validar_inteiro(nota, 1, 4)
        if motivo:
            return f"nota de '{revisor}' {motivo}"
    return None


def _validar_criticas(valor: object) -> str | None:
    if not isinstance(valor, list):
        return "esperado lista de criticas"
    for i, item in enumerate(valor):
        if not isinstance(item, dict):
            return f"critica no indice {i} nao e um objeto"
        if _validar_texto(item.get("revisor")):
            return f"critica no indice {i} sem 'revisor' valido"
        if item.get("tipo") not in TIPOS_CRITICA_VALIDOS:
            return f"critica no indice {i} com 'tipo' invalido (use fraqueza/critica)"
        if _validar_texto(item.get("texto")):
            return f"critica no indice {i} sem 'texto' valido"
    return None


def _validar_recomendacoes(valor: object) -> str | None:
    if not isinstance(valor, list):
        return "esperado lista de recomendacoes"
    for i, item in enumerate(valor):
        if _validar_texto(item):
            return f"recomendacao no indice {i} vazia ou nao e string"
    return None


# ---------------------------------------------------------------------------
# Montagem do resultado
# ---------------------------------------------------------------------------

def _erro(mensagem: str) -> dict:
    return {
        "status": "erro",
        "tipo": None,
        "completo": False,
        "campos_faltando": [],
        "campos_invalidos": [],
        "score_completude": 0.0,
        "mensagem": mensagem,
    }


def _montar(tipo: str, faltando: list[str], invalidos: list[dict], validos: int, total: int) -> dict:
    score = round(validos / total, 4) if total else 0.0
    completo = not faltando and not invalidos
    if completo:
        mensagem = f"{tipo} completo: todos os campos obrigatorios presentes e validos"
    else:
        partes = []
        if faltando:
            partes.append(f"{len(faltando)} campo(s) faltando")
        if invalidos:
            partes.append(f"{len(invalidos)} campo(s) invalido(s)")
        mensagem = f"{tipo} incompleto: " + " e ".join(partes)
    return {
        "status": "ok",
        "tipo": tipo,
        "completo": completo,
        "campos_faltando": faltando,
        "campos_invalidos": invalidos,
        "score_completude": score,
        "mensagem": mensagem,
    }


def _motivo_parecer(campo: str, valor: object) -> str | None:
    if campo == CAMPO_REVISOR:
        return _validar_texto(valor)
    if campo == BLOCO_NOTA_1_3:
        return _validar_bloco(valor, 1, 3)
    return _validar_bloco(valor, 1, 4)


def _validar_parecer(parecer: dict) -> dict:
    faltando: list[str] = []
    invalidos: list[dict] = []
    validos = 0
    for campo in CAMPOS_PARECER:
        if campo not in parecer:
            faltando.append(campo)
            continue
        motivo = _motivo_parecer(campo, parecer[campo])
        if motivo is not None:
            invalidos.append({"campo": campo, "motivo": motivo})
        else:
            validos += 1
    return _montar("parecer", faltando, invalidos, validos, len(CAMPOS_PARECER))


def _validar_veredito(veredito: dict) -> dict:
    faltando: list[str] = []
    invalidos: list[dict] = []
    validos = 0

    validadores = {
        "decisao": lambda v: _validar_inteiro(v, 1, 4),
        "justificativa": _validar_texto,
        "sintese": _validar_texto,
        "notas_por_revisor": _validar_notas_por_revisor,
    }
    for campo in CAMPOS_VEREDITO:
        if campo not in veredito:
            faltando.append(campo)
            continue
        motivo = validadores[campo](veredito[campo])
        if motivo is not None:
            invalidos.append({"campo": campo, "motivo": motivo})
        else:
            validos += 1

    # Campos opcionais: só validam o conteúdo se estiverem presentes.
    for campo, validador in (
        ("criticas", _validar_criticas),
        ("recomendacoes_aos_autores", _validar_recomendacoes),
    ):
        if campo in veredito:
            motivo = validador(veredito[campo])
            if motivo is not None:
                invalidos.append({"campo": campo, "motivo": motivo})

    return _montar("veredito", faltando, invalidos, validos, len(CAMPOS_VEREDITO))


def _resolver_tipo(dado: dict, tipo: str) -> str:
    if tipo in ("parecer", "veredito"):
        return tipo
    if tipo != "auto":
        raise ValueError("tipo deve ser 'parecer', 'veredito' ou 'auto'")
    # Heurística: só o veredito do editor tem o campo 'decisao'.
    return "veredito" if "decisao" in dado else "parecer"


def validar_completude(dado: dict, tipo: str = "auto") -> dict:
    """Audita a completude estrutural de um parecer ou de um veredito do contrato oficial.

    Verifica, de forma determinística e sem LLM, a presença e a validade de cada campo
    obrigatório:

    - **parecer** (``ReviewSchema``): ``revisor`` (string não vazia); os critérios
      ``solidez_tecnica``, ``originalidade``, ``significancia``, ``clareza`` e a síntese
      ``nota_geral``, cada um como bloco ``{nota (1-4), justificativa}``; e ``confianca``
      como bloco ``{nota (1-3), justificativa}``.
    - **veredito** (``EditorVerdictSchema``): ``decisao`` (1-4), ``justificativa`` e
      ``sintese`` (não vazias) e ``notas_por_revisor`` (dict não vazio, cada nota 1-4).
      ``criticas`` e ``recomendacoes_aos_autores`` são opcionais, mas, quando presentes,
      têm seus itens validados.

    Não avalia mérito nem conteúdo, apenas estrutura.

    Args:
        dado: Dicionário com o parecer ou o veredito a auditar. Pode estar
            incompleto/malformado — a função reporta os problemas em vez de levantar.
        tipo: ``"parecer"``, ``"veredito"`` ou ``"auto"`` (default). Em ``"auto"``, a
            presença do campo ``decisao`` identifica um veredito; caso contrário, parecer.

    Returns:
        Dicionário com ``status``, ``tipo`` (parecer/veredito), ``completo`` (bool),
        ``campos_faltando`` (list[str]), ``campos_invalidos`` (list[{campo, motivo}]),
        ``score_completude`` (float 0..1) e ``mensagem`` (str). ``status`` é ``"erro"``
        apenas quando a entrada não é um dict.
    """
    if not isinstance(dado, dict):
        return _erro("entrada invalida: esperado um dict (parecer ou veredito)")

    if _resolver_tipo(dado, tipo) == "veredito":
        return _validar_veredito(dado)
    return _validar_parecer(dado)


if __name__ == "__main__":
    import json

    parecer = {
        "revisor": "statistician",
        "solidez_tecnica": {"nota": 3, "justificativa": "Métodos adequados, amostra pequena."},
        "originalidade": {"nota": 2, "justificativa": "Contribuição incremental."},
        "significancia": {"nota": 3, "justificativa": "Relevância clínica clara."},
        "clareza": {"nota": 4, "justificativa": "Texto bem organizado e reprodutível."},
        "nota_geral": {"nota": 3, "justificativa": "Aceitar com ressalvas."},
        "confianca": {"nota": 3, "justificativa": "Dentro da minha especialidade."},
    }
    veredito = {
        "decisao": 3,
        "justificativa": "Os pareceres convergem para aceitar com ressalvas.",
        "sintese": "Contribuição sólida e clara, validação ainda limitada.",
        "notas_por_revisor": {"statistician": 3, "domain_expert": 3, "copyeditor": 4},
    }
    print("# parecer")
    print(json.dumps(validar_completude(parecer), indent=2, ensure_ascii=False))
    print("\n# veredito")
    print(json.dumps(validar_completude(veredito), indent=2, ensure_ascii=False))
