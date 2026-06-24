"""Camada de retry sobre a validação de schemas do pipeline de peer review.

Adapta o padrão de validação/retry da Atividade 5 para o contrato oficial
(ReviewSchema, CrossReviewSchema, EditorVerdictSchema) definido pelo Grupo 3.

Comportamento de falha definido:
  - Retry: até MAX_TENTATIVAS vezes (padrão 3).
  - Registro de erro: cada tentativa gravada em ResultadoValidacao.historico.
  - Bloqueio: levanta PipelineValidationError ao esgotar — erros nunca passam
    silenciosamente para a próxima fase do pipeline.
  - Marcação para revisão humana: o chamador pode capturar PipelineValidationError
    e registrar o resultado como item que necessita intervenção humana.

Uso típico (integrado ao pipeline.py):

    from validacao_retry import validar_com_tentativas, PipelineValidationError
    from review_schema import validar_review
    from pipeline import RunMode

    resultado = validar_com_tentativas(
        dados_brutos=payload,
        schema_fn=validar_review,
        modo=RunMode.MOCK,
        nome_agente="statistician",
    )
    review = resultado.dados  # ReviewSchema validado
"""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import ValidationError

logger = logging.getLogger("pipeline.validacao")

# ---------------------------------------------------------------------------
# Constante pública
# ---------------------------------------------------------------------------

MAX_TENTATIVAS: int = 3

# ---------------------------------------------------------------------------
# Tipos de resultado
# ---------------------------------------------------------------------------


@dataclass
class ResultadoValidacao:
    """Resultado completo de uma rodada de validação com retry.

    Attributes
    ----------
    sucesso:
        True se ao menos uma tentativa passou.
    dados:
        O modelo Pydantic validado, ou None se todas as tentativas falharam.
    tentativas_usadas:
        Quantas tentativas foram feitas (1 em caso de sucesso imediato).
    historico:
        Uma entrada por tentativa: {tentativa, status, erro}.
    erro_final:
        Mensagem de erro da última tentativa malsucedida, ou None se sucesso.
    """

    sucesso: bool
    dados: Any | None
    tentativas_usadas: int
    historico: list[dict] = field(default_factory=list)
    erro_final: str | None = None


# ---------------------------------------------------------------------------
# Exceção de validação esgotada
# ---------------------------------------------------------------------------


class PipelineValidationError(RuntimeError):
    """Levantada quando todas as tentativas de retry se esgotam.

    Carrega o ResultadoValidacao completo para inspeção do histórico sem
    necessidade de re-parsear a mensagem de erro.
    """

    def __init__(self, mensagem: str, resultado: ResultadoValidacao) -> None:
        super().__init__(mensagem)
        self.resultado = resultado


# ---------------------------------------------------------------------------
# Corretor offline (mock) — determinístico, sem Gemini
# ---------------------------------------------------------------------------


def _clampear_notas(dados: dict, chave_pai: str = "") -> dict:
    """Percorre o dict recursivamente e corrige campos 'nota' fora do range."""
    resultado = {}
    for chave, valor in dados.items():
        if isinstance(valor, dict):
            resultado[chave] = _clampear_notas(valor, chave_pai=chave)
        elif isinstance(valor, list):
            resultado[chave] = [
                _clampear_notas(item, chave_pai=chave) if isinstance(item, dict) else item
                for item in valor
            ]
        elif chave == "nota" and isinstance(valor, (int, float)):
            limite = 3 if chave_pai == "confianca" else 4
            resultado[chave] = max(1, min(limite, int(valor)))
        else:
            resultado[chave] = valor
    return resultado


_CAMPOS_TEXTO = frozenset(
    {"justificativa", "argumento_decisivo", "texto", "resposta_aos_pares", "revisor",
     "sintese"}
)
_SENTINEL = "[corrigido automaticamente — revisar]"


def _corrigir_textos_vazios(dados: dict) -> dict:
    """Substitui strings vazias/só-espaços em campos obrigatórios por um sentinel
    e injeta 'justificativa' em blocos nota+justificativa onde ela está ausente."""
    resultado = {}
    for chave, valor in dados.items():
        if isinstance(valor, dict):
            sub = _corrigir_textos_vazios(valor)
            # Se o sub-dict tem 'nota' mas não tem 'justificativa', injeta sentinel.
            if "nota" in sub and "justificativa" not in sub:
                sub["justificativa"] = _SENTINEL
            resultado[chave] = sub
        elif isinstance(valor, list):
            itens = []
            for item in valor:
                if isinstance(item, dict):
                    sub = _corrigir_textos_vazios(item)
                    if "nota" in sub and "justificativa" not in sub:
                        sub["justificativa"] = _SENTINEL
                    itens.append(sub)
                elif isinstance(item, str) and not item.strip():
                    itens.append(_SENTINEL)
                else:
                    itens.append(item)
            resultado[chave] = itens
        elif chave in _CAMPOS_TEXTO and isinstance(valor, str) and not valor.strip():
            resultado[chave] = _SENTINEL
        else:
            resultado[chave] = valor
    return resultado


def _corrigir_coerencia_cross_review(dados: dict) -> dict:
    """Corrige incoerências do CrossReviewSchema: mudou_posicao vs mudancas."""
    dados = dict(dados)
    mudou = dados.get("mudou_posicao")
    mudancas = dados.get("mudancas", [])
    if mudou is True and not mudancas:
        dados["mudou_posicao"] = False
    if mudou is False and mudancas:
        dados["mudancas"] = []
    return dados


def _corrigir_notas_por_revisor(dados: dict) -> dict:
    """Se notas_por_revisor estiver vazio, insere placeholder para não bloquear."""
    dados = dict(dados)
    if "notas_por_revisor" in dados and not dados["notas_por_revisor"]:
        dados["notas_por_revisor"] = {"revisor_placeholder": 2}
    return dados


def corrigir_saida_mock(dados: dict, erro: str) -> dict:  # noqa: ARG001
    """Correção determinística offline — sem Gemini.

    Aplica reparos estruturais baseados nos tipos de erro mais comuns:
      1. Notas inteiras fora do range → clamp para [1,4] (ou [1,3] em confianca).
      2. Strings obrigatórias vazias → substitui por sentinel legível.
      3. Incoerência mudou_posicao/mudancas (CrossReviewSchema) → corrige flag.
      4. notas_por_revisor vazio (EditorVerdictSchema) → insere placeholder.

    O parâmetro ``erro`` é aceito por assinatura (interface consistente com
    ``corrigir_saida_api``) mas não é usado: os reparos são aplicados
    incondicionalmente em cada campo suspeito.
    """
    corrigido = copy.deepcopy(dados)
    corrigido = _clampear_notas(corrigido)
    corrigido = _corrigir_textos_vazios(corrigido)
    corrigido = _corrigir_coerencia_cross_review(corrigido)
    corrigido = _corrigir_notas_por_revisor(corrigido)
    return corrigido


# ---------------------------------------------------------------------------
# Corretor via API Gemini — importação lazy para não quebrar modo offline
# ---------------------------------------------------------------------------


def corrigir_saida_api(dados: dict, erro: str, schema_fn: Callable) -> dict:
    """Chama o Gemini para corrigir uma saída inválida.

    Importa ``google.genai`` de forma lazy para que o módulo possa ser importado
    mesmo sem a biblioteca instalada (modo MOCK / testes offline).

    Parameters
    ----------
    dados:
        O dict com a saída inválida que precisa ser corrigida.
    erro:
        Mensagem de ValidationError da tentativa anterior.
    schema_fn:
        A função de validação cujo modelo Pydantic será usado para extrair o
        JSON Schema e incluir no prompt.
    """
    try:
        import os

        import google.genai as genai  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "google-genai não está instalado. No modo API, instale-o com "
            "'pip install google-genai'."
        ) from exc

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY não encontrada. Configure a variável de ambiente."
        )

    model_id = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    # Extrai o JSON Schema do modelo Pydantic associado à função de validação.
    # Convenção: o módulo review_schema expõe as funções validar_* cujo
    # modelo retornado tem model_json_schema().
    try:
        # Chama com dict vazio para capturar o tipo de retorno esperado pelo schema.
        # Na prática, usa __annotations__ ou o modelo Pydantic direto.
        schema_json = _extrair_json_schema(schema_fn)
    except Exception:
        schema_json = "{}"

    prompt = (
        "Você é um assistente que corrige JSONs inválidos para um pipeline de peer review.\n\n"
        f"O JSON a seguir falhou na validação:\n```json\n{json.dumps(dados, ensure_ascii=False, indent=2)}\n```\n\n"
        f"Erro de validação:\n{erro}\n\n"
        f"Schema esperado (JSON Schema):\n```json\n{schema_json}\n```\n\n"
        "Retorne APENAS o JSON corrigido, sem texto adicional, sem blocos de código markdown."
    )

    client = genai.Client(api_key=api_key)
    resposta = client.models.generate_content(
        model=model_id,
        contents=prompt,
    )
    texto = resposta.text.strip()
    return _extrair_json_da_resposta(texto)


def _extrair_json_schema(schema_fn: Callable) -> str:
    """Tenta extrair o JSON Schema do modelo Pydantic retornado por schema_fn."""
    import inspect

    hints = {}
    try:
        hints = schema_fn.__annotations__
    except AttributeError:
        pass

    # Tenta via tipo de retorno da assinatura
    try:
        sig = inspect.signature(schema_fn)
        ret = sig.return_annotation
        if hasattr(ret, "model_json_schema"):
            return json.dumps(ret.model_json_schema(), ensure_ascii=False, indent=2)
    except Exception:
        pass

    return json.dumps(hints, ensure_ascii=False)


def _extrair_json_da_resposta(texto: str) -> dict:
    """Extrai um objeto JSON de uma resposta de texto do modelo."""
    # Tenta parsear diretamente.
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass

    # Remove blocos markdown ```json ... ```.
    limpo = texto
    for marcador in ("```json", "```"):
        limpo = limpo.replace(marcador, "")
    limpo = limpo.strip()
    try:
        return json.loads(limpo)
    except json.JSONDecodeError:
        pass

    # Extrai entre primeira { e última }.
    inicio = texto.find("{")
    fim = texto.rfind("}")
    if inicio != -1 and fim != -1 and fim > inicio:
        try:
            return json.loads(texto[inicio : fim + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Não foi possível extrair JSON da resposta do modelo: {texto[:200]!r}")


# ---------------------------------------------------------------------------
# Função principal: validar com retry
# ---------------------------------------------------------------------------


def validar_com_tentativas(
    dados_brutos: dict,
    schema_fn: Callable[[dict], Any],
    modo: Any,
    nome_agente: str,
    max_tentativas: int = MAX_TENTATIVAS,
) -> ResultadoValidacao:
    """Valida dados_brutos contra schema_fn, com retry e correção automática.

    Parameters
    ----------
    dados_brutos:
        Dicionário com a saída bruta do agente a ser validada.
    schema_fn:
        Função de validação (ex.: validar_review, validar_cross_review,
        validar_editor_verdict). Deve levantar ValidationError ou ValueError
        em caso de dados inválidos.
    modo:
        RunMode.API ou RunMode.MOCK (aceita qualquer objeto com .value == "mock"
        ou .value == "api").
    nome_agente:
        Identificador do agente (para logs e mensagens de erro).
    max_tentativas:
        Número máximo de tentativas (padrão MAX_TENTATIVAS = 3).

    Returns
    -------
    ResultadoValidacao
        Com sucesso=True e dados preenchidos.

    Raises
    ------
    PipelineValidationError
        Se todas as tentativas falharem. Carrega o ResultadoValidacao completo.
    """
    historico: list[dict] = []
    dados_atuais = copy.deepcopy(dados_brutos)
    erro_atual: str | None = None

    # Determina qual corrector usar com base no modo.
    usar_mock = True
    try:
        usar_mock = str(getattr(modo, "value", modo)).lower() in (
            "mock", "local", "offline", "json"
        )
    except Exception:
        usar_mock = True

    for tentativa in range(1, max_tentativas + 1):
        try:
            validado = schema_fn(dados_atuais)
            historico.append({"tentativa": tentativa, "status": "PASSOU", "erro": None})
            logger.info(
                "[validacao] '%s' PASSOU na tentativa %d/%d.",
                nome_agente, tentativa, max_tentativas,
            )
            return ResultadoValidacao(
                sucesso=True,
                dados=validado,
                tentativas_usadas=tentativa,
                historico=historico,
                erro_final=None,
            )
        except (ValidationError, ValueError) as exc:
            erro_atual = str(exc)
            historico.append({"tentativa": tentativa, "status": "FALHOU", "erro": erro_atual})
            logger.warning(
                "[validacao] '%s' FALHOU na tentativa %d/%d: %s",
                nome_agente, tentativa, max_tentativas, erro_atual,
            )

            if tentativa == max_tentativas:
                break

            # Aplica correção antes da próxima tentativa.
            try:
                if usar_mock:
                    dados_atuais = corrigir_saida_mock(dados_atuais, erro_atual)
                    logger.info(
                        "[validacao] '%s' — correção mock aplicada (tentativa %d -> %d).",
                        nome_agente, tentativa, tentativa + 1,
                    )
                else:
                    dados_atuais = corrigir_saida_api(dados_atuais, erro_atual, schema_fn)
                    logger.info(
                        "[validacao] '%s' — correção via API aplicada (tentativa %d -> %d).",
                        nome_agente, tentativa, tentativa + 1,
                    )
            except Exception as exc_correcao:
                logger.error(
                    "[validacao] '%s' — falha no corrector na tentativa %d: %s",
                    nome_agente, tentativa, exc_correcao,
                )
                break

    resultado = ResultadoValidacao(
        sucesso=False,
        dados=None,
        tentativas_usadas=len(historico),
        historico=historico,
        erro_final=erro_atual,
    )
    mensagem = (
        f"[validacao] '{nome_agente}' falhou em todas as {resultado.tentativas_usadas} "
        f"tentativa(s). Último erro: {erro_atual}"
    )
    raise PipelineValidationError(mensagem, resultado)
