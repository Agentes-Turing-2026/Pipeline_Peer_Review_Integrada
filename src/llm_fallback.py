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
- Cada TENTATIVA (principal e, se houver troca, reserva) vira um sub-span
  ``llm_tentativa_principal``/``llm_tentativa_reserva`` no trace (início, fim,
  duração, status). Cada TROCA de modelo emite, nas duas trilhas de
  observabilidade do pipeline, sempre com provedor inicial, motivo da falha,
  opção reserva e qual modelo respondeu:
    * traces (``observability.emit_event``, Grupo 3): ``llm_fallback_acionado``,
      ``llm_fallback_respondeu`` e ``llm_fallback_esgotado``;
    * métricas (``ExecutionCollector.registrar(tipo="fallback_llm")``, Grupo 2):
      mesmos três desfechos, agregados em ``ResumoExecucao.fallbacks_llm`` e
      nos contadores ``quantidade_fallbacks_*`` (ver
      ``docs/metricas_reference.md`` §6).
  As duas trilhas são no-op fora de uma execução instrumentada (sem tracer /
  sem coletor registrado).

Configuração (ver ``model_provider.resolver_config_fallback``):

    LLM_FALLBACK_PROVIDER          — gemini | maritaca | openai
    LLM_FALLBACK_MODEL             — id do modelo reserva
    LLM_FALLBACK_PROVIDER_<PAPEL>  — reserva específica de um papel (ex.: EDITOR)
    LLM_FALLBACK_MODEL_<PAPEL>     — idem, para o modelo

Sem nenhuma dessas variáveis, NADA muda: o pipeline se comporta exatamente
como antes (uma falha de API interrompe a execução).
"""

from __future__ import annotations

from typing import Any, Literal

#: Mesmo domínio de metrics.eventos.StatusEvento — repetido aqui (sem importar
#: metrics, que é opcional) só para tipar o status repassado a
#: ExecutionCollector.registrar().
_StatusMetrica = Literal["sucesso", "falha", "aviso"]

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
# Eventos para traces/métricas
#
# Cada troca de modelo alimenta as DUAS trilhas de observabilidade do
# pipeline, a partir do MESMO conjunto de fatos (provedor inicial, motivo da
# falha, opção fallback, quem respondeu):
#
#   - traces  (src/observability/, Grupo 3): emit_event(...) ancorado no span
#     da execução/fase atual — aparece na linha do tempo reconstruída por
#     render_timeline().
#   - métricas (src/metrics/, Grupo 2): ExecutionCollector.registrar(...) com
#     tipo="fallback_llm" — aparece em ResumoExecucao (quantidade_fallbacks_*
#     e fallbacks_llm), ver docs/metricas_reference.md §6.
#
# Ambas são NO-OP fora de uma execução instrumentada (sem tracer / sem
# coletor registrado) — a troca de modelo em si nunca depende delas.
# ---------------------------------------------------------------------------

def _fase_atual() -> str | None:
    """Fase do pipeline em curso (via metrics.coletor), ou None se indisponível.

    Import tardio e guardado: ``metrics`` pode não estar instalado (uso avulso
    de ``llm_fallback.py`` fora do pipeline) e, mesmo instalado, só há uma fase
    "atual" dentro de um ``with coletor.fase(...)`` — o que cobre exatamente o
    trecho em que os agentes (e o corretor de retry) chamam o modelo.
    """
    try:
        from metrics.coletor import obter_fase_atual
    except ImportError:
        return None
    return obter_fase_atual()


def _emit(name: str, status: str, attributes: dict[str, Any], *, papel: str | None) -> None:
    """Emite o evento de TRACE na execução atual; sem tracer, é no-op."""
    try:
        from observability import emit_event
    except ImportError:  # observabilidade indisponível (ex.: uso avulso)
        return
    emit_event(
        name,
        author=papel or "llm_fallback",
        phase=_fase_atual(),
        status=status,
        attributes=attributes,
    )


def _registrar_metrica(
    *, evento: str, status: _StatusMetrica, papel: str | None, detalhes: dict[str, Any]
) -> None:
    """Registra a troca de modelo no ExecutionCollector da execução atual.

    Mesmo padrão de no-op de ``_emit``: sem ``metrics`` instalado ou sem
    coletor registrado para esta execução (``metrics.adk_usage.
    definir_coletor_adk``, ligado pelo pipeline), não faz nada.
    """
    try:
        from metrics.adk_usage import obter_coletor_adk
    except ImportError:
        return
    coletor = obter_coletor_adk()
    if coletor is None:
        return
    coletor.registrar(
        fase=_fase_atual() or "desconhecida",
        tipo="fallback_llm",
        nome=papel or "llm",
        status=status,
        evento=evento,
        **detalhes,
    )


def _span(name: str, *, papel: str | None, attributes: dict[str, Any]):
    """Sub-span de UMA tentativa de chamada ao modelo (no-op sem tracer).

    Cada tentativa (principal ou reserva) vira um span próprio, com início,
    fim, duração e status — é o que faz "cada tentativa" aparecer no trace,
    além dos eventos de desfecho (acionado/respondeu/esgotado).
    """
    try:
        from observability import trace_span
    except ImportError:
        from contextlib import nullcontext

        return nullcontext()
    return trace_span(name, kind="call", phase=_fase_atual(), author=papel or "llm_fallback", attributes=attributes)


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
    erro_resumido = _resumo_erro(erro)
    _emit(
        "llm_fallback_acionado",
        status="alerta",
        papel=papel,
        attributes={
            "provedor_inicial": rotulo_primario,
            "motivo_da_falha": motivo,
            "erro": erro_resumido,
            "opcao_fallback": rotulo_reserva,
            "papel": papel,
        },
    )
    _registrar_metrica(
        evento="acionado",
        status="aviso",
        papel=papel,
        detalhes={
            "provedor_inicial": rotulo_primario,
            "motivo_falha": motivo,
            "opcao_fallback": rotulo_reserva,
            "erro": erro_resumido,
            "mensagem": (
                f"fallback acionado: '{rotulo_primario}' falhou ({motivo}) — "
                f"tentando reserva '{rotulo_reserva}'"
            ),
        },
    )


def emitir_fallback_respondeu(
    *, rotulo_primario: str, rotulo_reserva: str, papel: str | None = None
) -> None:
    """Registra qual modelo conseguiu responder após a troca."""
    _emit(
        "llm_fallback_respondeu",
        status="ok",
        papel=papel,
        attributes={
            "provedor_inicial": rotulo_primario,
            "modelo_que_respondeu": rotulo_reserva,
            "papel": papel,
        },
    )
    _registrar_metrica(
        evento="respondeu",
        status="sucesso",
        papel=papel,
        detalhes={
            "provedor_inicial": rotulo_primario,
            "opcao_fallback": rotulo_reserva,
            "modelo_que_respondeu": rotulo_reserva,
        },
    )


def emitir_fallback_esgotado(
    *, rotulo_primario: str, rotulo_reserva: str,
    erro: BaseException, papel: str | None = None,
) -> None:
    """Registra que principal E reserva falharam — a execução vai parar."""
    erro_resumido = _resumo_erro(erro)
    _emit(
        "llm_fallback_esgotado",
        status="erro",
        papel=papel,
        attributes={
            "provedor_inicial": rotulo_primario,
            "opcao_fallback": rotulo_reserva,
            "erro_da_reserva": erro_resumido,
            "papel": papel,
        },
    )
    _registrar_metrica(
        evento="esgotado",
        status="falha",
        papel=papel,
        detalhes={
            "provedor_inicial": rotulo_primario,
            "opcao_fallback": rotulo_reserva,
            "erro_final": erro_resumido,
            "mensagem": (
                f"fallback esgotado: principal '{rotulo_primario}' e reserva "
                f"'{rotulo_reserva}' falharam"
            ),
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
                with _span(
                    "llm_tentativa_principal",
                    papel=self.papel,
                    attributes={"modelo": self.rotulo_primario, "papel": self.papel},
                ):
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
                with _span(
                    "llm_tentativa_reserva",
                    papel=self.papel,
                    attributes={"modelo": self.rotulo_reserva, "papel": self.papel},
                ):
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
