"""Eventos estruturados da camada de validação, retry e confiabilidade (Grupo 1).

Este módulo NÃO substitui `validacao_retry.py` — ele só observa o que já
acontece lá e grava um rastro estruturado, em JSON Lines, de cada tentativa de
validação: se passou de primeira, se foi corrigida, se falhou (e por quê), se
foi bloqueada, e se o caso parece exigir revisão humana.

Decisão de projeto (ver README §4 para o motivo resumido):

- **Arquivo próprio** (`src/logs/validacao_events.jsonl`), separado de
  `src/logs/pipeline.log`. O pipeline e as tools do Grupo 2 já escrevem nesse
  arquivo de texto; em vez de disputar o mesmo log, os eventos de
  validação/retry ganham um arquivo dedicado e estruturado (uma linha = um
  evento = um JSON válido), fácil de abrir e também fácil de "grepar" sem
  interferir no que já existe.
- **`run_id` unificado com o Grupo 3.** Quando o pipeline roda com o tracer de
  observabilidade (`src/observability/`), `Pipeline.run()` (em
  `pipeline_base.py`) sincroniza `context.run_id` com `tracer.run_id` antes de
  qualquer fase rodar — então o `run_id` usado aqui é o MESMO da execução no
  trace do Grupo 3. Sem tracer (uso isolado do Grupo 1: demos, testes, ou o
  pipeline rodando sem o pacote `observability`), um `uuid4` próprio continua
  sendo gerado como antes — nada quebra por falta dele.
- **Espelhamento no `emit_event` do Grupo 3.** Além de gravar em
  `validacao_events.jsonl`, cada evento também é repassado (de forma
  guardada/no-op se o pacote não existir) para `observability.emit_event()`,
  para aparecer na MESMA linha do tempo reconstruída pelo Grupo 3 — sem que
  este módulo dependa do pacote deles para funcionar sozinho.
- **Schema do evento fica simples de propósito.** `EventoValidacao` é um
  dataclass -> dict plano, sem dependência de nenhuma biblioteca de
  observabilidade. Isso deixa margem para, no futuro, plugar um exportador
  (ex.: OpenTelemetry/Langfuse) que leia esses mesmos eventos sem precisar
  redesenhar nada aqui.

Uso típico (dentro de `validacao_retry.py`):

    from eventos_validacao import EventoValidacao, emitir_evento, gerar_run_id

    emitir_evento(EventoValidacao(
        run_id=run_id, fase=fase, agente=nome_agente, schema="validar_review",
        tentativa=1, max_tentativas=3, categoria="passou_de_primeira",
        status="sucesso", erro=None, correcao_aplicada=None,
        requer_revisao_humana=False,
    ))
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("pipeline.validacao")

HERE = Path(__file__).resolve().parent
EVENTOS_DIR = HERE / "logs"
EVENTOS_PATH = EVENTOS_DIR / "validacao_events.jsonl"

# ---------------------------------------------------------------------------
# Ponte opcional com a observabilidade do Grupo 3 (src/observability/).
# Importação guardada: se o pacote não existir (ex.: este módulo usado sozinho,
# fora do pipeline integrado), `_emit_event_grupo3` vira um no-op silencioso —
# igual ao próprio `observability.emit_event` já faz quando não há tracer ativo.
# ---------------------------------------------------------------------------

try:
    from observability import emit_event as _emit_event_grupo3  # type: ignore[import]
except Exception:  # noqa: BLE001 - pacote do Grupo 3 pode não estar presente
    def _emit_event_grupo3(*_args: Any, **_kwargs: Any) -> None:
        return None

# ---------------------------------------------------------------------------
# Categorias estruturadas de evento (respondem "passou, falhou ou foi
# corrigido por quê?", como pede a task do Grupo 1).
# ---------------------------------------------------------------------------

CATEGORIA_PASSOU_DE_PRIMEIRA = "passou_de_primeira"
CATEGORIA_PASSOU_APOS_CORRECAO = "passou_apos_correcao"
CATEGORIA_FALHOU_RECUPERAVEL = "falhou_recuperavel"
CATEGORIA_CORRIGIDO = "corrigido"
CATEGORIA_BLOQUEADO = "bloqueado"

CATEGORIAS_VALIDAS = frozenset(
    {
        CATEGORIA_PASSOU_DE_PRIMEIRA,
        CATEGORIA_PASSOU_APOS_CORRECAO,
        CATEGORIA_FALHOU_RECUPERAVEL,
        CATEGORIA_CORRIGIDO,
        CATEGORIA_BLOQUEADO,
    }
)

# Tradução da nossa categoria para o vocabulário de status do Grupo 3
# (Status: "ok" | "erro" | "alerta" | "em_andamento", em observability/events.py).
_STATUS_OBSERVABILIDADE_GRUPO3 = {
    CATEGORIA_PASSOU_DE_PRIMEIRA: "ok",
    CATEGORIA_PASSOU_APOS_CORRECAO: "ok",
    CATEGORIA_FALHOU_RECUPERAVEL: "alerta",
    CATEGORIA_CORRIGIDO: "alerta",
    CATEGORIA_BLOQUEADO: "erro",
}


def gerar_run_id() -> str:
    """Gera um identificador de execução local (uuid4) — apenas fallback.

    O identificador comum do pipeline JÁ EXISTE desde a integração dos
    grupos: ``Pipeline.run()`` (em ``pipeline_base.py``) sincroniza
    ``context.run_id`` com o ``tracer.run_id`` do Grupo 3, e as fases
    repassam esse valor a ``validar_com_tentativas()`` — é ele que deve ser
    propagado sempre que houver uma execução do pipeline em andamento.

    Este uuid4 local só é usado em contextos isolados, onde não existe um
    ``run_id`` de execução para herdar: ``demo_eventos.py``, testes, ou uma
    chamada avulsa de ``validar_com_tentativas()`` sem ``run_id``.
    """
    return str(uuid.uuid4())


@dataclass
class EventoValidacao:
    """Um evento estruturado de validação/retry, pronto para virar uma linha JSON.

    Attributes
    ----------
    run_id:
        Identificador da execução do pipeline à qual este evento pertence.
    fase:
        Nome da fase do pipeline (ex.: ``fase_1_revisao_independente``) ou
        ``None`` quando o evento vem de um uso isolado (ex.: demos/testes).
    agente:
        Identificador do agente/revisor associado à tentativa.
    schema:
        Nome da função de validação usada (ex.: ``validar_review``).
    tentativa / max_tentativas:
        Posição da tentativa atual dentro do limite configurado.
    categoria:
        Uma de ``CATEGORIAS_VALIDAS`` — a resposta estruturada para "passou,
        falhou ou foi corrigido por quê?".
    status:
        Rótulo curto e legível (``sucesso`` | ``falha`` | ``correcao_aplicada``
        | ``bloqueado``).
    erro:
        Mensagem de validação da tentativa, quando houver falha.
    correcao_aplicada:
        Diff ``{campo: {"antes": ..., "depois": ...}}`` dos campos alterados
        pelo corrector, quando ``categoria == "corrigido"``.
    requer_revisao_humana:
        ``True`` quando o evento representa um bloqueio (esgotamento de
        tentativas) — candidato natural a triagem manual.
    timestamp:
        Data/hora UTC ISO-8601 de geração do evento (preenchido automaticamente).
    """

    run_id: str
    agente: str
    schema: str
    tentativa: int
    max_tentativas: int
    categoria: str
    status: str
    erro: str | None = None
    correcao_aplicada: dict[str, Any] | None = None
    requer_revisao_humana: bool = False
    fase: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        if self.categoria not in CATEGORIAS_VALIDAS:
            raise ValueError(
                f"categoria de evento desconhecida: {self.categoria!r}. "
                f"Use uma de {sorted(CATEGORIAS_VALIDAS)}."
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def emitir_evento(evento: EventoValidacao) -> None:
    """Grava um evento como uma linha JSON em ``EVENTOS_PATH`` (append-only) e o
    espelha (best-effort) na observabilidade do Grupo 3.

    Cria o diretório de logs se necessário. A gravação própria (JSONL) nunca
    levanta exceção silenciosamente perdida: se a escrita falhar, a exceção
    sobe, porque um evento de confiabilidade que se perde sem aviso contraria o
    próprio objetivo desta camada. Já o espelhamento para
    ``observability.emit_event()`` é best-effort: se o pacote não existir ou a
    chamada falhar por qualquer razão, isso NUNCA deve derrubar a validação do
    pipeline — só perdemos a cópia na timeline do Grupo 3, não o evento em si
    (que já foi gravado no arquivo próprio antes desta chamada).
    """
    EVENTOS_DIR.mkdir(exist_ok=True)
    linha = json.dumps(evento.to_dict(), ensure_ascii=False)
    with EVENTOS_PATH.open("a", encoding="utf-8") as f:
        f.write(linha + "\n")

    try:
        _emit_event_grupo3(
            f"validacao_{evento.categoria}",
            author=evento.agente,
            phase=evento.fase,
            status=_STATUS_OBSERVABILIDADE_GRUPO3.get(evento.categoria, "ok"),
            kind="agent",
            attributes={
                "schema": evento.schema,
                "tentativa": evento.tentativa,
                "max_tentativas": evento.max_tentativas,
                "categoria": evento.categoria,
                "run_id_grupo1": evento.run_id,
                **({"erro": evento.erro} if evento.erro else {}),
                **({"correcao_aplicada": evento.correcao_aplicada} if evento.correcao_aplicada else {}),
                **({"requer_revisao_humana": True} if evento.requer_revisao_humana else {}),
            },
        )
    except Exception as _exc:  # noqa: BLE001 - observabilidade nunca pode quebrar a validação
        logger.debug(
            "emitir_evento: espelhamento para observability falhou: %s", _exc
        )


# ---------------------------------------------------------------------------
# Diff de correção — para responder "o que exatamente foi corrigido".
# ---------------------------------------------------------------------------


def diff_correcao(antes: dict, depois: dict) -> dict[str, dict[str, Any]]:
    """Compara recursivamente ``antes``/``depois`` e devolve só os campos que mudaram.

    Devolve um dict achatado por caminho de campo (ex.: ``"confianca.nota"``)
    -> ``{"antes": valor_antigo, "depois": valor_novo}``. Usado para anexar ao
    evento de categoria ``"corrigido"`` o que o corrector (mock ou API) de fato
    alterou nos dados.
    """
    mudancas: dict[str, dict[str, Any]] = {}
    _diff_recursivo(antes or {}, depois or {}, "", mudancas)
    return mudancas


def _diff_recursivo(antes: Any, depois: Any, caminho: str, acumulador: dict) -> None:
    if isinstance(antes, dict) and isinstance(depois, dict):
        chaves = set(antes.keys()) | set(depois.keys())
        for chave in chaves:
            sub_caminho = f"{caminho}.{chave}" if caminho else str(chave)
            _diff_recursivo(antes.get(chave), depois.get(chave), sub_caminho, acumulador)
        return
    if isinstance(antes, list) and isinstance(depois, list):
        if antes != depois:
            acumulador[caminho] = {"antes": antes, "depois": depois}
        return
    if antes != depois:
        acumulador[caminho] = {"antes": antes, "depois": depois}


# ---------------------------------------------------------------------------
# Leitura e formatação — "abrir o arquivo de eventos e entender o histórico".
# ---------------------------------------------------------------------------


def ler_eventos(run_id: str | None = None) -> list[dict[str, Any]]:
    """Lê ``EVENTOS_PATH`` e devolve os eventos (opcionalmente filtrados por run_id)."""
    if not EVENTOS_PATH.exists():
        return []
    eventos = []
    for linha in EVENTOS_PATH.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha:
            continue
        evento = json.loads(linha)
        if run_id is None or evento.get("run_id") == run_id:
            eventos.append(evento)
    return eventos


_ROTULOS_CATEGORIA = {
    CATEGORIA_PASSOU_DE_PRIMEIRA: "PASSOU (de primeira)",
    CATEGORIA_PASSOU_APOS_CORRECAO: "PASSOU (após correção)",
    CATEGORIA_FALHOU_RECUPERAVEL: "FALHOU (recuperável, retry a seguir)",
    CATEGORIA_CORRIGIDO: "CORRIGIDO (corrector aplicado)",
    CATEGORIA_BLOQUEADO: "BLOQUEADO (esgotou tentativas)",
}


def formatar_linha_do_tempo(eventos: list[dict[str, Any]]) -> str:
    """Formata uma lista de eventos como uma linha do tempo legível (texto)."""
    linhas = []
    for evento in eventos:
        rotulo = _ROTULOS_CATEGORIA.get(evento["categoria"], evento["categoria"])
        cabecalho = (
            f"[{evento['timestamp']}] run_id={evento['run_id'][:8]}… "
            f"fase={evento.get('fase') or '—'} agente={evento['agente']} "
            f"schema={evento['schema']} tentativa={evento['tentativa']}/{evento['max_tentativas']} "
            f"-> {rotulo}"
        )
        linhas.append(cabecalho)
        if evento.get("erro"):
            linhas.append(f"    erro: {evento['erro'].splitlines()[0][:120]}")
        if evento.get("correcao_aplicada"):
            for campo, mudanca in evento["correcao_aplicada"].items():
                linhas.append(f"    corrigido: {campo}: {mudanca['antes']!r} -> {mudanca['depois']!r}")
        if evento.get("requer_revisao_humana"):
            linhas.append("    ATENÇÃO: candidato a revisão humana.")
    return "\n".join(linhas)
