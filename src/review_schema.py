"""Schema de avaliação reutilizável pelos agentes revisores.

Este módulo define, de forma única e validável, o formato de saída que TODO
revisor do sistema de peer review deve produzir. Ele substitui o JSON livre
(definido apenas no prompt) usado na atividade anterior, onde nada garantia que
os campos existissem, que as notas estivessem na faixa correta ou que as
justificativas não fossem vazias.

O schema é um modelo Pydantic v2, o que o torna diretamente compatível com o
Google ADK: basta passar ``output_schema=ReviewSchema`` ao ``LlmAgent`` (em
conjunto com o ``output_key`` já usado no projeto) para que o ADK force o
modelo a responder exatamente nessa estrutura e a valide automaticamente.

Exemplo mínimo de uso por um agente revisor::

    from google.adk.agents import LlmAgent
    from review_schema import ReviewSchema

    statistician_agent = LlmAgent(
        name="statistician_reviewer",
        model="gemini-2.0-flash",
        output_key="statistician_review",   # padrão já usado no projeto
        output_schema=ReviewSchema,          # força e valida a estrutura
        instruction=STATISTICIAN_PROMPT,
    )
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Escalas (documentação legível e reutilizável pelas demais partes do sistema)
# ---------------------------------------------------------------------------

#: Escala usada em cada um dos quatro critérios de avaliação (1 a 4).
ESCALA_CRITERIOS: dict[int, str] = {
    4: "Excelente",
    3: "Bom",
    2: "Regular",
    1: "Fraco",
}

#: Escala da nota geral / recomendação editorial (1 a 4).
ESCALA_NOTA_GERAL: dict[int, str] = {
    4: "Aceitar",
    3: "Aceitar com ressalvas",
    2: "Rejeitar com ressalvas",
    1: "Rejeitar",
}

#: Escala do nível de confiança do revisor (1 a 3).
ESCALA_CONFIANCA: dict[int, str] = {
    3: "Confiante na avaliação",
    2: "Moderadamente confiante",
    1: "Pouco confiante",
}


# ---------------------------------------------------------------------------
# Validador compartilhado
# ---------------------------------------------------------------------------

def _justificativa_nao_vazia(valor: str) -> str:
    """Garante que uma justificativa não seja vazia ou apenas espaços."""
    if valor is None or not valor.strip():
        raise ValueError("A justificativa é obrigatória e não pode ser vazia.")
    return valor.strip()


# ---------------------------------------------------------------------------
# Blocos de avaliação (nota + justificativa)
# ---------------------------------------------------------------------------

class CriterionEvaluation(BaseModel):
    """Avaliação de um único critério: nota (1-4) + justificativa obrigatória."""

    nota: int = Field(
        ...,
        ge=1,
        le=4,
        description="Nota do critério na escala 1-4 (4=Excelente, 1=Fraco).",
    )
    justificativa: str = Field(
        ...,
        min_length=1,
        description="Texto explicando a nota atribuída ao critério. Não pode ser vazio.",
    )

    _valida_justificativa = field_validator("justificativa")(_justificativa_nao_vazia)


class OverallEvaluation(BaseModel):
    """Nota geral (1-4) + justificativa considerando os quatro critérios."""

    nota: int = Field(
        ...,
        ge=1,
        le=4,
        description="Recomendação editorial 1-4 (4=Aceitar, 1=Rejeitar).",
    )
    justificativa: str = Field(
        ...,
        min_length=1,
        description=(
            "Explicação da decisão geral, considerando os quatro critérios. "
            "Obrigatória e não vazia."
        ),
    )

    _valida_justificativa = field_validator("justificativa")(_justificativa_nao_vazia)


class ConfidenceEvaluation(BaseModel):
    """Confiança do revisor (1-3) + justificativa do nível atribuído."""

    nota: int = Field(
        ...,
        ge=1,
        le=3,
        description="Nível de confiança 1-3 (3=Confiante, 1=Pouco confiante).",
    )
    justificativa: str = Field(
        ...,
        min_length=1,
        description=(
            "Explicação do porquê deste nível de confiança. Obrigatória e não vazia."
        ),
    )

    _valida_justificativa = field_validator("justificativa")(_justificativa_nao_vazia)


# ---------------------------------------------------------------------------
# Schema principal do parecer de um revisor
# ---------------------------------------------------------------------------

class ReviewSchema(BaseModel):
    """Parecer estruturado e validado de um único agente revisor.

    Cada critério carrega nota + justificativa. A nota geral e a confiança
    seguem o mesmo padrão (nota + justificativa), de modo que toda decisão
    fique sempre acompanhada de uma explicação textual obrigatória.
    """

    revisor: str = Field(
        ...,
        min_length=1,
        description=(
            "Identificador do revisor que emitiu o parecer "
            "(ex.: 'statistician', 'domain_expert', 'copyeditor')."
        ),
    )

    # Quatro critérios obrigatórios, cada um com nota (1-4) + justificativa.
    solidez_tecnica: CriterionEvaluation = Field(
        ...,
        description="Validade dos métodos, rigor científico e suporte empírico/teórico.",
    )
    originalidade: CriterionEvaluation = Field(
        ...,
        description="Novidade da contribuição e diferenciação do estado da arte.",
    )
    significancia: CriterionEvaluation = Field(
        ...,
        description="Impacto potencial, relevância para a comunidade e avanço do campo.",
    )
    clareza: CriterionEvaluation = Field(
        ...,
        description="Qualidade da escrita, organização e reprodutibilidade.",
    )

    # Síntese: nota geral (1-4) e confiança (1-3), ambas com justificativa.
    nota_geral: OverallEvaluation = Field(
        ...,
        description="Recomendação editorial considerando os quatro critérios.",
    )
    confianca: ConfidenceEvaluation = Field(
        ...,
        description="Quão confiante o revisor está na avaliação emitida.",
    )

    _valida_revisor = field_validator("revisor")(_justificativa_nao_vazia)


# ---------------------------------------------------------------------------
# Helpers de conveniência para as demais partes (editor, debate, pipeline)
# ---------------------------------------------------------------------------

def validar_review(data: dict) -> ReviewSchema:
    """Valida um dicionário contra o schema, levantando ``ValidationError``.

    Útil para a parte de pipeline/editor consumir saídas de revisores e
    garantir que estão bem-formadas antes de prosseguir.
    """
    return ReviewSchema.model_validate(data)


def json_schema() -> dict:
    """Retorna o JSON Schema do parecer (útil para documentação/prompts)."""
    return ReviewSchema.model_json_schema()


# ===========================================================================
# Fase 2 — Leitura cruzada entre revisores
# ===========================================================================
# Depois da avaliação independente (ReviewSchema), cada revisor lê os ARGUMENTOS
# (não as notas) dos colegas e decide, critério a critério, se MANTÉM ou REVISA
# a sua posição. O schema abaixo registra essa decisão de forma rastreável:
#   - o parecer FINAL (revisado), ainda no formato ReviewSchema;
#   - se houve mudança de posição (`mudou_posicao`);
#   - cada mudança individual (`mudancas`), com a nota anterior, a nova e qual
#     argumento do colega foi decisivo;
#   - a resposta textual obrigatória aos pares (`resposta_aos_pares`).
#
# Manter o parecer final como um ReviewSchema embutido garante que o Editor-Chefe
# consuma a saída da fase 2 exatamente como consumiria a da fase 1 — basta ler
# `parecer_revisado` e validá-lo com `validar_review`.

#: Critérios/sínteses que podem ter a nota revisada na leitura cruzada.
CRITERIOS_REVISAVEIS = (
    "solidez_tecnica",
    "originalidade",
    "significancia",
    "clareza",
    "nota_geral",
    "confianca",
)

CriterioRevisavel = Literal[
    "solidez_tecnica",
    "originalidade",
    "significancia",
    "clareza",
    "nota_geral",
    "confianca",
]


class CriterionRevision(BaseModel):
    """Uma mudança de nota motivada pela leitura do argumento de um colega.

    Só deve aparecer em ``mudancas`` quando a nota de fato mudou. Registra de
    onde veio a mudança (qual argumento foi decisivo), tornando rastreável cada
    revisão de posição.
    """

    model_config = {"extra": "forbid"}

    criterio: CriterioRevisavel = Field(
        ...,
        description="Qual critério (ou síntese) teve a nota revisada.",
    )
    nota_anterior: int = Field(
        ...,
        ge=1,
        le=4,
        description="Nota que o revisor havia atribuído na fase independente.",
    )
    nota_nova: int = Field(
        ...,
        ge=1,
        le=4,
        description="Nota após considerar os argumentos dos colegas.",
    )
    argumento_decisivo: str = Field(
        ...,
        min_length=1,
        description=(
            "Qual argumento de qual colega convenceu a mudança. Deve referenciar "
            "o conteúdo do argumento, não apenas o nome do revisor."
        ),
    )
    justificativa: str = Field(
        ...,
        min_length=1,
        description="Por que esse argumento mudou a posição (obrigatória, não vazia).",
    )

    _valida_arg = field_validator("argumento_decisivo")(_justificativa_nao_vazia)
    _valida_just = field_validator("justificativa")(_justificativa_nao_vazia)

    @model_validator(mode="after")
    def _notas_devem_diferir(self) -> "CriterionRevision":
        if self.nota_anterior == self.nota_nova:
            raise ValueError(
                "Uma mudança registrada em 'mudancas' precisa ter "
                "nota_anterior != nota_nova. Critérios mantidos não entram aqui."
            )
        return self


class CrossReviewSchema(BaseModel):
    """Parecer de um revisor APÓS a leitura cruzada (segunda fase).

    Embute o parecer final no formato ``ReviewSchema`` (``parecer_revisado``) e
    documenta, de forma rastreável, se e por que o revisor mudou de posição.
    """

    model_config = {"extra": "forbid"}

    revisor: str = Field(
        ...,
        min_length=1,
        description="Identificador do revisor (deve casar com o parecer original).",
    )
    parecer_revisado: ReviewSchema = Field(
        ...,
        description=(
            "Parecer FINAL após a leitura cruzada, no mesmo formato da fase 1. "
            "Nos critérios mantidos, as notas são iguais às originais."
        ),
    )
    mudou_posicao: bool = Field(
        ...,
        description="True se ao menos uma nota foi revisada após ler os colegas.",
    )
    mudancas: list[CriterionRevision] = Field(
        default_factory=list,
        description=(
            "Lista das mudanças de nota. Vazia quando o revisor manteve todas as "
            "posições; uma entrada por critério revisado."
        ),
    )
    resposta_aos_pares: str = Field(
        ...,
        min_length=1,
        description=(
            "Resposta textual obrigatória aos argumentos dos colegas, explicando "
            "o que foi acatado e o que foi rejeitado (com resistência controlada)."
        ),
    )

    _valida_revisor = field_validator("revisor")(_justificativa_nao_vazia)
    _valida_resposta = field_validator("resposta_aos_pares")(_justificativa_nao_vazia)

    @model_validator(mode="after")
    def _coerencia_mudancas(self) -> "CrossReviewSchema":
        # mudou_posicao precisa ser consistente com a lista de mudanças.
        if self.mudou_posicao and not self.mudancas:
            raise ValueError(
                "mudou_posicao=True exige ao menos uma entrada em 'mudancas'."
            )
        if not self.mudou_posicao and self.mudancas:
            raise ValueError(
                "mudou_posicao=False não pode vir acompanhado de 'mudancas'."
            )

        # O revisor do parecer revisado deve ser o mesmo do topo.
        if self.parecer_revisado.revisor != self.revisor:
            raise ValueError(
                "O 'revisor' do parecer_revisado deve ser igual ao 'revisor' do topo."
            )

        # Não pode haver duas mudanças para o mesmo critério.
        criterios = [m.criterio for m in self.mudancas]
        if len(criterios) != len(set(criterios)):
            raise ValueError("Há mudanças duplicadas para um mesmo critério.")

        # A nota_nova de cada mudança tem de bater com a nota do parecer revisado.
        for mudanca in self.mudancas:
            bloco = getattr(self.parecer_revisado, mudanca.criterio)
            if bloco.nota != mudanca.nota_nova:
                raise ValueError(
                    f"Inconsistência em '{mudanca.criterio}': mudanca.nota_nova="
                    f"{mudanca.nota_nova} mas parecer_revisado tem nota={bloco.nota}."
                )
        return self


def validar_cross_review(data: dict) -> CrossReviewSchema:
    """Valida um dicionário contra ``CrossReviewSchema`` (fase de leitura cruzada).

    Levanta ``ValidationError`` se a saída da segunda fase estiver malformada
    (mudança sem argumento, incoerência entre ``mudou_posicao`` e ``mudancas``,
    nota revisada que não bate com o parecer final, etc.).
    """
    return CrossReviewSchema.model_validate(data)


def cross_review_json_schema() -> dict:
    """JSON Schema da leitura cruzada (útil para documentação/prompts)."""
    return CrossReviewSchema.model_json_schema()


# ===========================================================================
# Fase 3 — Veredito do Editor-Chefe
# ===========================================================================
# Depois da avaliação independente (ReviewSchema) e da leitura cruzada
# (CrossReviewSchema), o Editor-Chefe SINTETIZA os pareceres revisados em uma
# decisão editorial única. Este schema é o contrato oficial dessa terceira fase.
#
# Princípio central — UMA ÚNICA ESCALA. A decisão editorial reutiliza
# EXATAMENTE a mesma escala 1-4 da ``nota_geral`` dos revisores
# (``ESCALA_NOTA_GERAL``). Não há um segundo formato de nota (ex.: 0-10) nem um
# vocabulário paralelo de recomendação (ex.: "Accept"/"Minor Revision"). Formatos
# legados que usem outra escala precisam passar pelo adaptador documentado em
# ``legacy_adapter.py`` antes de virarem um ``EditorVerdictSchema``.

#: Escala da decisão editorial. É a MESMA de ``nota_geral`` — propositalmente
#: reutilizada para que exista uma única noção de "recomendação" no sistema.
ESCALA_VEREDITO: dict[int, str] = ESCALA_NOTA_GERAL

#: Tipos de crítica que o editor agrega a partir dos pareceres dos revisores.
TipoCritica = Literal["fraqueza", "critica"]


class EditorCriticism(BaseModel):
    """Uma crítica individual atribuída a um revisor, preservada pelo editor.

    O Editor-Chefe NÃO resume nem agrupa críticas: cada fraqueza ou problema
    crítico levantado por um revisor entra aqui como uma entrada própria, com a
    fonte (``revisor``) e o ``tipo`` padronizados.
    """

    model_config = {"extra": "forbid"}

    revisor: str = Field(
        ...,
        min_length=1,
        description="Identificador do revisor que levantou a crítica.",
    )
    tipo: TipoCritica = Field(
        ...,
        description="Natureza da crítica: 'fraqueza' (menor) ou 'critica' (bloqueante).",
    )
    texto: str = Field(
        ...,
        min_length=1,
        description="Descrição específica e acionável da crítica. Não pode ser vazia.",
    )

    _valida_revisor = field_validator("revisor")(_justificativa_nao_vazia)
    _valida_texto = field_validator("texto")(_justificativa_nao_vazia)


class EditorVerdictSchema(BaseModel):
    """Veredito editorial final, sintetizando os pareceres dos revisores.

    Contrato oficial da terceira fase do pipeline. A ``decisao`` usa a mesma
    escala 1-4 da ``nota_geral`` (``ESCALA_VEREDITO``), de modo que exista uma
    única noção de recomendação em todo o sistema.
    """

    model_config = {"extra": "forbid"}

    decisao: int = Field(
        ...,
        ge=1,
        le=4,
        description=(
            "Decisão editorial na escala 1-4 (4=Aceitar, 3=Aceitar com ressalvas, "
            "2=Rejeitar com ressalvas, 1=Rejeitar) — a MESMA escala de 'nota_geral'."
        ),
    )
    justificativa: str = Field(
        ...,
        min_length=1,
        description=(
            "Explicação de como a decisão foi derivada a partir dos pareceres dos "
            "revisores. Obrigatória e não vazia."
        ),
    )
    sintese: str = Field(
        ...,
        min_length=1,
        description="Resumo geral do artigo e do parecer agregado (2-3 frases).",
    )
    notas_por_revisor: dict[str, int] = Field(
        ...,
        description=(
            "Mapa revisor -> nota_geral (1-4) considerada na síntese. Garante "
            "rastreabilidade entre a decisão e as notas que a embasaram."
        ),
    )
    criticas: list[EditorCriticism] = Field(
        default_factory=list,
        description=(
            "TODAS as críticas (fraquezas + problemas críticos) levantadas pelos "
            "revisores, preservadas individualmente, sem resumo nem agrupamento."
        ),
    )
    recomendacoes_aos_autores: list[str] = Field(
        default_factory=list,
        description="Recomendações acionáveis para os autores revisarem o trabalho.",
    )

    _valida_justificativa = field_validator("justificativa")(_justificativa_nao_vazia)
    _valida_sintese = field_validator("sintese")(_justificativa_nao_vazia)

    @field_validator("notas_por_revisor")
    @classmethod
    def _notas_validas(cls, valor: dict[str, int]) -> dict[str, int]:
        if not valor:
            raise ValueError(
                "notas_por_revisor não pode ser vazio: a decisão precisa estar "
                "ancorada nas notas dos revisores."
            )
        for revisor, nota in valor.items():
            if not revisor or not revisor.strip():
                raise ValueError("Há um identificador de revisor vazio em notas_por_revisor.")
            if not isinstance(nota, int) or isinstance(nota, bool):
                raise ValueError(f"A nota de '{revisor}' deve ser um inteiro 1-4.")
            if not 1 <= nota <= 4:
                raise ValueError(
                    f"A nota de '{revisor}' ({nota}) está fora da escala 1-4."
                )
        return valor

    @field_validator("recomendacoes_aos_autores")
    @classmethod
    def _recomendacoes_nao_vazias(cls, valor: list[str]) -> list[str]:
        limpas = [item.strip() for item in valor]
        if any(not item for item in limpas):
            raise ValueError(
                "Nenhuma recomendação aos autores pode ser vazia ou só espaços."
            )
        return limpas


def validar_editor_verdict(data: dict) -> EditorVerdictSchema:
    """Valida um dicionário contra ``EditorVerdictSchema`` (veredito do editor).

    Levanta ``ValidationError`` se o veredito estiver malformado (decisão fora da
    escala 1-4, justificativa/síntese vazias, notas de revisor inválidas, crítica
    sem fonte/texto, recomendação vazia, etc.).
    """
    return EditorVerdictSchema.model_validate(data)


def editor_verdict_json_schema() -> dict:
    """JSON Schema do veredito do editor (útil para documentação/prompts)."""
    return EditorVerdictSchema.model_json_schema()
