"""Auditoria determinística do veredito do Editor-Chefe (Tool 3 — Grupo 2).

Esta tool produz um **log de auditoria** da decisão editorial final
(``EditorVerdictSchema``, Fase 3) — sem qualquer chamada a modelo de linguagem.
Ela não decide nada: apenas torna a decisão já tomada *auditável e rastreável*,
respondendo de forma objetiva "esta decisão é confiável ou precisa de um humano?".

Para isso ela:

- **chama** ``checar_coerencia`` (Tool 2, João Pedro) para herdar as inconsistências
  semânticas do veredito (ex.: ``decisao=4`` "Aceitar" com notas baixas, ou uma crítica
  bloqueante junto de uma aceitação);
- calcula ``media_notas`` e ``divergencia_notas`` (= max − min de ``notas_por_revisor``);
- conta as críticas por tipo (``fraqueza`` vs ``critica``);
- define ``requer_revisao_humana`` = divergência ≥ 2 **ou** há inconsistências;
- gera um ``resumo_auditoria`` em PT-BR e **preserva o veredito bruto** para rastreio.

Posição no pipeline: roda **após a Fase 3** (veredito do editor) e **antes da Fase 4**
(relatório final), como passo de auditoria.

Adaptação a partir da Atividade 5 (ver ``docs/tools_reference.md``): é a evolução do
antigo ``calcular_score_decisao``/``montar_parecer_final``, agora sobre a escala oficial
**1-4** e o ``EditorVerdictSchema`` (antes: escala 1-5 e formato livre).

Operação 100% offline, ``dict`` puro, apenas biblioteca padrão. A dependência de
``checar_coerencia`` é **guardada**: enquanto a Tool 2 não estiver integrada, a auditoria
roda mesmo assim (sem a checagem semântica), registrando isso no resumo.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Permite tanto `from tools.auditar_decisao_final import ...` (testes/demo, com src no
# path) quanto a execução direta `python src/tools/auditar_decisao_final.py`.
_SRC = Path(__file__).resolve().parents[1]  # .../src
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Importação GUARDADA da Tool 2 (João Pedro). Ausente → auditoria segue sem a
# checagem semântica, sinalizando no resumo (não quebra o clone limpo).
try:
    from tools.checar_coerencia import checar_coerencia  # type: ignore
except Exception:  # pragma: no cover - depende da integração da Tool 2
    checar_coerencia = None  # type: ignore[assignment]

# Rótulos da decisão editorial (1-4). Cópia local do ``ESCALA_VEREDITO`` de
# ``review_schema`` para manter a tool sem dependências (importar o schema puxa pydantic).
ESCALA_VEREDITO: dict[int, str] = {
    4: "Aceitar",
    3: "Aceitar com ressalvas",
    2: "Rejeitar com ressalvas",
    1: "Rejeitar",
}

#: A partir desta divergência entre notas de revisores, exige-se desempate humano.
LIMIAR_DIVERGENCIA_HUMANA = 2

TIPOS_CRITICA = ("fraqueza", "critica")


# ---------------------------------------------------------------------------
# Helpers determinísticos
# ---------------------------------------------------------------------------

def _erro(mensagem: str, veredito: object) -> dict:
    return {
        "status": "erro",
        "decisao": None,
        "decisao_rotulo": "desconhecida",
        "media_notas": 0.0,
        "divergencia_notas": 0,
        "criticas_por_tipo": {tipo: 0 for tipo in TIPOS_CRITICA},
        "requer_revisao_humana": True,  # na dúvida, manda para humano
        "inconsistencias": [],
        "resumo_auditoria": mensagem,
        "veredito": veredito,
    }


def _notas_validas(notas_por_revisor: object) -> list[int]:
    """Extrai apenas as notas inteiras 1-4 (ignora bool e valores malformados)."""
    if not isinstance(notas_por_revisor, dict):
        return []
    notas: list[int] = []
    for valor in notas_por_revisor.values():
        if isinstance(valor, bool) or not isinstance(valor, int):
            continue
        if 1 <= valor <= 4:
            notas.append(valor)
    return notas


def _contar_criticas(criticas: object) -> dict[str, int]:
    contagem = {tipo: 0 for tipo in TIPOS_CRITICA}
    if isinstance(criticas, list):
        for item in criticas:
            if isinstance(item, dict) and item.get("tipo") in contagem:
                contagem[item["tipo"]] += 1
    return contagem


def _coletar_inconsistencias(veredito: dict) -> tuple[list, str | None]:
    """Chama ``checar_coerencia`` se disponível; devolve (inconsistencias, aviso)."""
    if checar_coerencia is None:
        return [], "checar_coerencia ausente — auditoria sem checagem semântica"
    try:
        resultado = checar_coerencia(veredito)
    except Exception as exc:  # defensivo: a Tool 2 não deve derrubar a auditoria
        return [], f"checar_coerencia falhou ({exc.__class__.__name__}) — seguindo sem ela"
    if not isinstance(resultado, dict) or resultado.get("status") != "ok":
        return [], "checar_coerencia retornou resultado inesperado — seguindo sem ela"
    inconsistencias = resultado.get("inconsistencias") or []
    return list(inconsistencias), None


def _montar_resumo(
    decisao_rotulo: str,
    decisao: object,
    media: float,
    divergencia: int,
    notas: list[int],
    criticas: dict[str, int],
    inconsistencias: list,
    requer_humano: bool,
    aviso: str | None,
) -> str:
    if notas:
        faixa = f" (entre {min(notas)} e {max(notas)})" if divergencia else ""
        bloco_notas = f"Média das notas dos revisores: {media}{faixa}; divergência {divergencia}."
    else:
        bloco_notas = "Sem notas de revisores utilizáveis para calcular média/divergência."
    bloco_criticas = (
        f"Críticas: {criticas['fraqueza']} fraqueza(s) e "
        f"{criticas['critica']} crítica(s) bloqueante(s)."
    )
    if aviso:
        bloco_coerencia = f"Coerência: {aviso}."
    else:
        bloco_coerencia = f"Coerência: {len(inconsistencias)} inconsistência(s) detectada(s)."
    bloco_flag = (
        "Revisão humana RECOMENDADA."
        if requer_humano
        else "Revisão humana não recomendada."
    )
    return (
        f"Decisão editorial: {decisao} ({decisao_rotulo}). "
        f"{bloco_notas} {bloco_criticas} {bloco_coerencia} {bloco_flag}"
    )


# ---------------------------------------------------------------------------
# Função pública (contrato oficial)
# ---------------------------------------------------------------------------

def auditar_decisao_final(veredito: dict) -> dict:
    """Audita o veredito do Editor-Chefe e devolve um log de auditoria rastreável.

    Resume, de forma determinística e sem LLM, a confiabilidade da decisão editorial
    final: agrega as notas dos revisores, conta as críticas, herda as inconsistências
    semânticas de :func:`checar_coerencia` e decide se a decisão precisa de revisão
    humana. Não recalcula nem altera a decisão — apenas a torna auditável.

    Args:
        veredito: Dicionário no formato ``EditorVerdictSchema`` (``decisao`` 1-4,
            ``justificativa``, ``sintese``, ``notas_por_revisor``, ``criticas``,
            ``recomendacoes_aos_autores``). Pode vir do pipeline já validado; mesmo
            assim a função é defensiva contra campos ausentes/malformados.

    Returns:
        Dicionário com:

        - ``status`` (``"ok"`` | ``"erro"``);
        - ``decisao`` (int 1-4 ou ``None``) e ``decisao_rotulo`` (str, via ``ESCALA_VEREDITO``);
        - ``media_notas`` (float) e ``divergencia_notas`` (int = max−min das notas);
        - ``criticas_por_tipo`` (dict ``{"fraqueza": n, "critica": n}``);
        - ``requer_revisao_humana`` (bool): divergência ≥ 2 **ou** há inconsistências;
        - ``inconsistencias`` (list): repassadas de ``checar_coerencia`` (vazia se a
          Tool 2 ainda não está integrada);
        - ``resumo_auditoria`` (str, PT-BR);
        - ``veredito`` (dict bruto preservado para rastreabilidade).
    """
    if not isinstance(veredito, dict):
        return _erro(
            "entrada invalida: esperado um dict no formato EditorVerdictSchema",
            veredito,
        )

    decisao = veredito.get("decisao")
    decisao_rotulo = ESCALA_VEREDITO.get(decisao, "desconhecida") if isinstance(
        decisao, int
    ) and not isinstance(decisao, bool) else "desconhecida"

    notas = _notas_validas(veredito.get("notas_por_revisor"))
    media_notas = round(sum(notas) / len(notas), 4) if notas else 0.0
    divergencia_notas = (max(notas) - min(notas)) if notas else 0

    criticas_por_tipo = _contar_criticas(veredito.get("criticas"))
    inconsistencias, aviso = _coletar_inconsistencias(veredito)

    requer_revisao_humana = (
        divergencia_notas >= LIMIAR_DIVERGENCIA_HUMANA or bool(inconsistencias)
    )

    resumo = _montar_resumo(
        decisao_rotulo,
        decisao,
        media_notas,
        divergencia_notas,
        notas,
        criticas_por_tipo,
        inconsistencias,
        requer_revisao_humana,
        aviso,
    )

    return {
        "status": "ok",
        "decisao": decisao if isinstance(decisao, int) and not isinstance(decisao, bool) else None,
        "decisao_rotulo": decisao_rotulo,
        "media_notas": media_notas,
        "divergencia_notas": divergencia_notas,
        "criticas_por_tipo": criticas_por_tipo,
        "requer_revisao_humana": requer_revisao_humana,
        "inconsistencias": inconsistencias,
        "resumo_auditoria": resumo,
        "veredito": veredito,
    }


if __name__ == "__main__":
    import json

    # Veredito mock (coerente) embutido, para a tool rodar mesmo sem a Tool 2/exemplos.
    veredito_mock = {
        "decisao": 3,
        "justificativa": "Os pareceres convergem para aceitar com ressalvas.",
        "sintese": "Contribuição sólida e clara, validação ainda limitada.",
        "notas_por_revisor": {"statistician": 3, "domain_expert": 3, "copyeditor": 4},
        "criticas": [
            {"revisor": "statistician", "tipo": "fraqueza", "texto": "Amostra pequena."},
            {"revisor": "statistician", "tipo": "critica", "texto": "Sem validação externa."},
        ],
        "recomendacoes_aos_autores": ["Ampliar a amostra.", "Incluir validação externa."],
    }
    print(json.dumps(auditar_decisao_final(veredito_mock), indent=2, ensure_ascii=False))