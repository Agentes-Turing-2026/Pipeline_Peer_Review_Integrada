"""Fallback de LLM: modelo principal + opção reserva para falhas TEMPORÁRIAS.

Este módulo implementa a troca automática de modelo quando o provedor
selecionado falha por um motivo passageiro — timeout, limite de requisições,
erro de rede ou indisponibilidade da API. Erros PERMANENTES (chave inválida,
pedido malformado, filtro de conteúdo) continuam estourando na hora, porque
trocar de modelo não os resolveria e só mascararia o problema real.

Como se encaixa no restante do pipeline:

- ``model_provider.build_model()`` continua sendo o único ponto que decide o
  modelo dos agentes. Quando a reserva está configurada (``LLM_FALLBACK_*``),
  ele passa a devolver o ``ModeloComFallback`` criado aqui — um ``BaseLlm``
  comum do ADK que embrulha os dois modelos reais. Os agentes não mudam.
- Cada troca emite eventos via ``observability.emit_event`` (no-op fora de uma
  execução com tracer): ``llm_fallback_acionado``, ``llm_fallback_respondeu`` e
  ``llm_fallback_esgotado``, sempre com provedor inicial, motivo da falha,
  opção reserva e qual modelo respondeu. Esses eventos são o gancho para o
  enriquecimento de traces e métricas (trabalho da Pessoa 2 do grupo).

Configuração (ver ``model_provider.resolver_config_fallback``):

    LLM_FALLBACK_PROVIDER          — gemini | maritaca | openai
    LLM_FALLBACK_MODEL             — id do modelo reserva
    LLM_FALLBACK_PROVIDER_<PAPEL>  — reserva específica de um papel (ex.: EDITOR)
    LLM_FALLBACK_MODEL_<PAPEL>     — idem, para o modelo

Sem nenhuma dessas variáveis, NADA muda: o pipeline se comporta exatamente
como antes (uma falha de API interrompe a execução).
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Classificação da falha: temporária (vale trocar de modelo) x permanente
# ---------------------------------------------------------------------------

#: Motivos de falha temporária, na linguagem da task do grupo.
MOTIVO_TIMEOUT = "timeout"
MOTIVO_LIMITE_REQUISICOES = "limite_de_requisicoes"
MOTIVO_ERRO_DE_REDE = "erro_de_rede"
MOTIVO_INDISPONIBILIDADE = "indisponibilidade_da_api"

#: Códigos HTTP considerados passageiros, mapeados para o motivo correspondente.
_CODIGOS_TEMPORARIOS: dict[int, str] = {
    408: MOTIVO_TIMEOUT,             # Request Timeout
    429: MOTIVO_LIMITE_REQUISICOES,  # Too Many Requests / quota
    500: MOTIVO_INDISPONIBILIDADE,   # Internal Server Error
    502: MOTIVO_INDISPONIBILIDADE,   # Bad Gateway
    503: MOTIVO_INDISPONIBILIDADE,   # Service Unavailable / overloaded
    504: MOTIVO_TIMEOUT,             # Gateway Timeout
}

#: Fragmentos de NOME de classe de exceção -> motivo. Cobre as hierarquias do
#: litellm (Timeout, RateLimitError, APIConnectionError, ServiceUnavailableError,
#: InternalServerError), do google-genai (ServerError) e do httpx
#: (ConnectTimeout, ReadTimeout, ConnectError, NetworkError), sem importar
#: nenhuma delas — assim a classificação funciona com qualquer provedor
#: e não obriga o modo mock a instalar as bibliotecas de API.
_NOMES_TEMPORARIOS: tuple[tuple[str, str], ...] = (
    ("timeout", MOTIVO_TIMEOUT),
    ("ratelimit", MOTIVO_LIMITE_REQUISICOES),
    ("toomanyrequests", MOTIVO_LIMITE_REQUISICOES),
    ("resourceexhausted", MOTIVO_LIMITE_REQUISICOES),
    ("connection", MOTIVO_ERRO_DE_REDE),
    ("network", MOTIVO_ERRO_DE_REDE),
    ("serviceunavailable", MOTIVO_INDISPONIBILIDADE),
    ("internalserver", MOTIVO_INDISPONIBILIDADE),
    ("servererror", MOTIVO_INDISPONIBILIDADE),
    ("unavailable", MOTIVO_INDISPONIBILIDADE),
)


def _codigo_http(exc: BaseException) -> int | None:
    """Extrai o status HTTP da exceção, quando o provedor o expõe."""
    for atributo in ("status_code", "code", "http_status"):
        valor = getattr(exc, atributo, None)
        if isinstance(valor, int):
            return valor
    return None


def classificar_falha_temporaria(exc: BaseException) -> str | None:
    """Devolve o MOTIVO da falha se ela for temporária, ou ``None`` se não for.

    A classificação é feita em três camadas, na exceção e nas suas causas
    encadeadas (``__cause__``/``__context__``), porque os SDKs costumam
    embrulhar o erro de transporte em uma exceção própria:

    1. tipos nativos do Python (``TimeoutError``, ``ConnectionError``);
    2. código HTTP exposto pela exceção (408/429/5xx);
    3. nome da classe (``RateLimitError``, ``ServiceUnavailableError``, ...).

    Tudo que não casar é tratado como PERMANENTE e o chamador deve re-levantar.
    """
    visitados: set[int] = set()
    atual: BaseException | None = exc
    while atual is not None and id(atual) not in visitados:
        visitados.add(id(atual))

        if isinstance(atual, TimeoutError):
            return MOTIVO_TIMEOUT
        if isinstance(atual, ConnectionError):
            return MOTIVO_ERRO_DE_REDE

        codigo = _codigo_http(atual)
        if codigo is not None and codigo in _CODIGOS_TEMPORARIOS:
            return _CODIGOS_TEMPORARIOS[codigo]
        if codigo is not None:
            # Havia um status HTTP e ele NÃO é passageiro (400, 401, 403...):
            # não deixa o nome da classe reclassificar um erro permanente.
            atual = atual.__cause__ or atual.__context__
            continue

        nome = type(atual).__name__.lower()
        for fragmento, motivo in _NOMES_TEMPORARIOS:
            if fragmento in nome:
                return motivo

        atual = atual.__cause__ or atual.__context__
    return None


# ---------------------------------------------------------------------------
# Eventos para traces/métricas (gancho para a Pessoa 2)
# ---------------------------------------------------------------------------

def _emit(name: str, status: str, attributes: dict[str, Any]) -> None:
    """Emite o evento na execução atual; sem tracer/observabilidade, é no-op."""
    try:
        from observability import emit_event
    except ImportError:  # observabilidade indisponível (ex.: uso avulso)
        return
    emit_event(name, author="grupo3_fallback", status=status, attributes=attributes)


def _resumo_erro(exc: BaseException) -> str:
    """Resumo curto da exceção para os traces (tipo + primeira linha)."""
    texto = str(exc).strip().splitlines()
    primeira = texto[0] if texto else ""
    return f"{type(exc).__name__}: {primeira}"[:300]


def emitir_fallback_acionado(
    *, rotulo_primario: str, rotulo_reserva: str, motivo: str,
    erro: BaseException, papel: str | None = None,
) -> None:
    """Registra que o principal falhou por motivo temporário e a reserva entrou."""
    _emit(
        "llm_fallback_acionado",
        status="alerta",
        attributes={
            "provedor_inicial": rotulo_primario,
            "motivo_da_falha": motivo,
            "erro": _resumo_erro(erro),
            "opcao_fallback": rotulo_reserva,
            "papel": papel,
        },
    )


def emitir_fallback_respondeu(
    *, rotulo_primario: str, rotulo_reserva: str, papel: str | None = None
) -> None:
    """Registra qual modelo conseguiu responder após a troca."""
    _emit(
        "llm_fallback_respondeu",
        status="ok",
        attributes={
            "provedor_inicial": rotulo_primario,
            "modelo_que_respondeu": rotulo_reserva,
            "papel": papel,
        },
    )


def emitir_fallback_esgotado(
    *, rotulo_primario: str, rotulo_reserva: str,
    erro: BaseException, papel: str | None = None,
) -> None:
    """Registra que principal E reserva falharam — a execução vai parar."""
    _emit(
        "llm_fallback_esgotado",
        status="erro",
        attributes={
            "provedor_inicial": rotulo_primario,
            "opcao_fallback": rotulo_reserva,
            "erro_da_reserva": _resumo_erro(erro),
            "papel": papel,
        },
    )


# ---------------------------------------------------------------------------
# O modelo com fallback (BaseLlm do ADK que embrulha principal + reserva)
# ---------------------------------------------------------------------------

#: Cache da classe (criada uma única vez, sob demanda — mesmo padrão lazy de
#: ``model_provider._classe_litellm``, para não exigir o ADK no modo offline).
_CLASSE_FALLBACK: list[Any] = []


def _classe_modelo_com_fallback():
    """Cria (uma vez) a classe ``ModeloComFallback``, importando o ADK só aqui."""
    if _CLASSE_FALLBACK:
        return _CLASSE_FALLBACK[0]

    from google.adk.models.base_llm import BaseLlm

    class ModeloComFallback(BaseLlm):  # type: ignore[misc, valid-type]
        """``BaseLlm`` que tenta o modelo principal e troca para a reserva.

        A resposta do principal é acumulada antes de ser repassada: se ele
        falhar no meio de um streaming, nenhum pedaço já emitido precisa ser
        "desfeito" — a reserva recomeça do zero e o chamador só vê UMA resposta
        coerente. O pipeline usa ``stream=False`` (uma resposta por turno),
        então o custo dessa acumulação é nulo na prática.
        """

        modelo_primario: BaseLlm
        modelo_reserva: BaseLlm
        rotulo_primario: str
        rotulo_reserva: str
        papel: str | None = None

        async def generate_content_async(self, llm_request, stream: bool = False):
            # O flow do ADK preenche llm_request.model com o `model` DESTE
            # wrapper; cada tentativa precisa apontar para o modelo interno
            # certo (o Gemini nativo lê llm_request.model, e o LiteLlm usa
            # llm_request.model or self.model).
            config_original = (
                llm_request.config.model_copy(deep=True)
                if getattr(llm_request, "config", None) is not None
                else None
            )

            try:
                respostas = []
                llm_request.model = self.modelo_primario.model
                async for resposta in self.modelo_primario.generate_content_async(
                    llm_request, stream
                ):
                    respostas.append(resposta)
            except Exception as exc:  # noqa: BLE001 — classificado logo abaixo
                motivo = classificar_falha_temporaria(exc)
                if motivo is None:
                    raise  # erro permanente: trocar de modelo não resolveria
                emitir_fallback_acionado(
                    rotulo_primario=self.rotulo_primario,
                    rotulo_reserva=self.rotulo_reserva,
                    motivo=motivo,
                    erro=exc,
                    papel=self.papel,
                )
            else:
                for resposta in respostas:
                    yield resposta
                return

            # Restaura a config: o adaptador do provedor principal pode tê-la
            # mutado (ex.: LiteLlmSemJsonSchema remove o response_schema).
            if config_original is not None:
                llm_request.config = config_original
            llm_request.model = self.modelo_reserva.model
            try:
                async for resposta in self.modelo_reserva.generate_content_async(
                    llm_request, stream
                ):
                    yield resposta
            except Exception as exc:  # noqa: BLE001 — só para registrar; re-levanta
                emitir_fallback_esgotado(
                    rotulo_primario=self.rotulo_primario,
                    rotulo_reserva=self.rotulo_reserva,
                    erro=exc,
                    papel=self.papel,
                )
                raise
            emitir_fallback_respondeu(
                rotulo_primario=self.rotulo_primario,
                rotulo_reserva=self.rotulo_reserva,
                papel=self.papel,
            )

    _CLASSE_FALLBACK.append(ModeloComFallback)
    return ModeloComFallback


def criar_modelo_com_fallback(
    *,
    modelo_primario: Any,
    modelo_reserva: Any,
    rotulo_primario: str,
    rotulo_reserva: str,
    papel: str | None = None,
) -> Any:
    """Monta o ``ModeloComFallback`` a partir dos dois modelos já instanciados.

    ``modelo_primario`` e ``modelo_reserva`` devem ser instâncias de ``BaseLlm``
    (o ``Gemini`` nativo do ADK ou o ``LiteLlm`` do pipeline) — quem os constrói
    é ``model_provider._instanciar_modelo``, mantendo a decisão de provedor
    centralizada lá.
    """
    Classe = _classe_modelo_com_fallback()
    return Classe(
        model=modelo_primario.model,
        modelo_primario=modelo_primario,
        modelo_reserva=modelo_reserva,
        rotulo_primario=rotulo_primario,
        rotulo_reserva=rotulo_reserva,
        papel=papel,
    )
