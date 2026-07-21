"""Adaptadores explícitos de formatos LEGADOS para os schemas oficiais.

O repositório integrado adota como contrato oficial os três schemas de
``review_schema.py`` (``ReviewSchema``, ``CrossReviewSchema`` e
``EditorVerdictSchema``). A ``Atividade_2`` produzia formatos PARALELOS, com
outra escala de nota e outro vocabulário de recomendação:

* Revisores antigos emitiam ``{"score": <1-10>, "recommendation":
  "accept|minor_revision|major_revision|reject", ...}``.
* O Editor-Chefe antigo emitia ``{"verdict": "Accept|Minor Revision|Major
  Revision|Reject", "weighted_score": <0-10>, "individual_scores": {...},
  "all_criticisms": [...], "recommendations_to_authors": [...]}``.

Para cumprir a regra "não manter formatos paralelos de nota/recomendação sem
adaptador claro", este módulo é o ÚNICO ponto onde esses formatos antigos são
convertidos para o contrato oficial. Toda conversão é documentada e a saída é
sempre validada contra o schema correspondente.

Limites honestos da conversão (documentados de propósito):

* O parecer legado de um revisor NÃO contém as quatro dimensões do
  ``ReviewSchema`` (solidez técnica, originalidade, significância, clareza) com
  nota + justificativa. Esses dados não existem no formato antigo e este módulo
  NÃO os inventa. Por isso não há ``legacy_review_to_schema``: só é possível
  mapear a *recomendação* legada para a escala oficial (ver
  ``recomendacao_legada_para_nota``); a reconstrução de um ``ReviewSchema``
  completo exige uma nova revisão, não uma adaptação.
* O ``weighted_score`` 0-10 do editor legado é convertido para a escala 1-4
  usando os MESMOS limiares de decisão do editor antigo (ver
  ``score_legado_para_decisao``), preservando a semântica original.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from review_schema import (  # noqa: E402
    EditorVerdictSchema,
    validar_editor_verdict,
)

# ---------------------------------------------------------------------------
# Tabelas de conversão (única fonte de verdade das equivalências legadas)
# ---------------------------------------------------------------------------

#: Recomendação textual do revisor legado -> nota oficial 1-4 (ESCALA_NOTA_GERAL).
#: As chaves são normalizadas (minúsculas, sem espaços/hífens) antes da consulta.
RECOMENDACAO_LEGADA_PARA_NOTA: dict[str, int] = {
    "accept": 4,           # Aceitar
    "minorrevision": 3,    # Aceitar com ressalvas
    "majorrevision": 2,    # Rejeitar com ressalvas
    "reject": 1,           # Rejeitar
}

#: Veredito textual do editor legado -> decisão oficial 1-4. Mesmo mapeamento
#: semântico da recomendação do revisor (apenas a rotulagem do campo difere).
VEREDITO_LEGADO_PARA_DECISAO: dict[str, int] = dict(RECOMENDACAO_LEGADA_PARA_NOTA)

#: Tipo de crítica legado -> tipo oficial de ``EditorCriticism``.
TIPO_CRITICA_LEGADO_PARA_OFICIAL: dict[str, str] = {
    "weakness": "fraqueza",
    "critical": "critica",
    "critical_issue": "critica",
}


def _normalizar_chave(valor: str) -> str:
    """Normaliza rótulos legados: minúsculas, sem espaços, hífens ou underscores."""
    return (
        valor.strip()
        .lower()
        .replace(" ", "")
        .replace("-", "")
        .replace("_", "")
    )


def recomendacao_legada_para_nota(recomendacao: str) -> int:
    """Converte a recomendação textual legada para a nota oficial 1-4.

    Aceita as variações usadas na ``Atividade_2`` ("accept", "minor_revision",
    "Major Revision", "reject", etc.). Levanta ``ValueError`` para valores
    desconhecidos, em vez de silenciar a incompatibilidade.
    """
    chave = _normalizar_chave(recomendacao)
    if chave not in RECOMENDACAO_LEGADA_PARA_NOTA:
        raise ValueError(
            f"Recomendação legada desconhecida: {recomendacao!r}. "
            f"Esperado um de: accept, minor_revision, major_revision, reject."
        )
    return RECOMENDACAO_LEGADA_PARA_NOTA[chave]


def score_legado_para_decisao(weighted_score: float) -> int:
    """Converte um ``weighted_score`` legado (0-10) na decisão oficial 1-4.

    Usa os MESMOS limiares do Editor-Chefe da ``Atividade_2`` para preservar a
    semântica original da decisão:

    ===================  =====================  ==========================
    weighted_score (0-10)  decisão oficial (1-4)  rótulo
    ===================  =====================  ==========================
    ``>= 8.0``           ``4``                  Aceitar
    ``6.0 <= s < 8.0``   ``3``                  Aceitar com ressalvas
    ``4.0 <= s < 6.0``   ``2``                  Rejeitar com ressalvas
    ``< 4.0``            ``1``                  Rejeitar
    ===================  =====================  ==========================
    """
    if not 0.0 <= weighted_score <= 10.0:
        raise ValueError(
            f"weighted_score legado fora da faixa 0-10: {weighted_score!r}."
        )
    if weighted_score >= 8.0:
        return 4
    if weighted_score >= 6.0:
        return 3
    if weighted_score >= 4.0:
        return 2
    return 1


class LegacyEditorVerdictAdapter:
    """Adapta o veredito do Editor-Chefe legado para ``EditorVerdictSchema``.

    Ponto único e explícito de conversão. A saída é sempre validada contra o
    contrato oficial, então qualquer dado legado inconsistente falha aqui — e não
    silenciosamente mais adiante no pipeline.
    """

    @staticmethod
    def _decisao(legacy: dict) -> int:
        """Deriva a decisão 1-4 do veredito legado.

        Prioriza o campo textual ``verdict`` (intenção explícita do editor). Se
        ausente, cai para a conversão do ``weighted_score`` pelos limiares
        originais. A divergência entre os dois não é silenciada: se ambos
        existirem e discordarem, o ``verdict`` textual prevalece (decisão do
        editor), pois o score é apenas o número que a embasou.
        """
        verdict = legacy.get("verdict")
        if verdict is not None:
            chave = _normalizar_chave(str(verdict))
            if chave not in VEREDITO_LEGADO_PARA_DECISAO:
                raise ValueError(
                    f"Veredito legado desconhecido: {verdict!r}. "
                    f"Esperado: Accept, Minor Revision, Major Revision ou Reject."
                )
            return VEREDITO_LEGADO_PARA_DECISAO[chave]

        weighted = legacy.get("weighted_score")
        if weighted is None:
            raise ValueError(
                "Veredito legado sem 'verdict' nem 'weighted_score': "
                "impossível derivar a decisão oficial."
            )
        return score_legado_para_decisao(float(weighted))

    @staticmethod
    def _criticas(legacy: dict) -> list[dict]:
        """Converte ``all_criticisms`` legado para o formato de ``EditorCriticism``."""
        criticas: list[dict] = []
        for item in legacy.get("all_criticisms", []):
            tipo_legado = _normalizar_chave(str(item.get("type", "")))
            tipo = TIPO_CRITICA_LEGADO_PARA_OFICIAL.get(tipo_legado)
            if tipo is None:
                raise ValueError(
                    f"Tipo de crítica legado desconhecido: {item.get('type')!r}. "
                    f"Esperado 'weakness' ou 'critical'."
                )
            criticas.append(
                {
                    "revisor": item.get("source", ""),
                    "tipo": tipo,
                    "texto": item.get("text", ""),
                }
            )
        return criticas

    @staticmethod
    def _notas_por_revisor(legacy: dict) -> dict[str, int]:
        """Converte ``individual_scores`` (1-10) em notas oficiais 1-4.

        Reaproveita ``score_legado_para_decisao`` para manter UMA ÚNICA regra de
        conversão de escala 0/1-10 -> 1-4 em todo o módulo.
        """
        notas: dict[str, int] = {}
        for revisor, score in legacy.get("individual_scores", {}).items():
            notas[revisor] = score_legado_para_decisao(float(score))
        return notas

    @classmethod
    def to_schema(cls, legacy: dict) -> EditorVerdictSchema:
        """Converte um veredito do editor legado e VALIDA contra o schema oficial.

        Parameters
        ----------
        legacy:
            Dicionário no formato do Editor-Chefe da ``Atividade_2``
            (``verdict``, ``weighted_score``, ``individual_scores``,
            ``summary``, ``all_criticisms``, ``recommendations_to_authors``).

        Returns
        -------
        EditorVerdictSchema
            Veredito no contrato oficial, já validado.
        """
        convertido = {
            "decisao": cls._decisao(legacy),
            "justificativa": legacy.get("summary")
            or "Convertido de veredito legado (Atividade_2) sem justificativa textual.",
            "sintese": legacy.get("summary")
            or "Convertido de veredito legado (Atividade_2) sem síntese textual.",
            "notas_por_revisor": cls._notas_por_revisor(legacy),
            "criticas": cls._criticas(legacy),
            "recomendacoes_aos_autores": list(
                legacy.get("recommendations_to_authors", [])
            ),
        }
        return validar_editor_verdict(convertido)


def legacy_verdict_to_schema(legacy: dict) -> EditorVerdictSchema:
    """Atalho funcional para ``LegacyEditorVerdictAdapter.to_schema``."""
    return LegacyEditorVerdictAdapter.to_schema(legacy)
