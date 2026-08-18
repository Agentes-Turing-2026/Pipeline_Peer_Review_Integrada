"""Coletor de eventos de execução — Grupo 2.

ExecutionCollector acumula ExecutionEvent (eventos.py) em memória durante uma
execução do pipeline. Não sabe nada sobre peer review, validação ou tools
específicas — é um acumulador genérico com dois context managers de
conveniência (fase(), tool()) que medem duração e registram sucesso/falha
automaticamente. Eventos de outros tipos (validacao, retry, falha, decisao_final)
são registrados via registrar() diretamente por quem tiver essa informação
(ex.: pipeline.py, ao inspecionar o resultado de validar_com_tentativas()).

restaurar() é o caminho inverso: semeia o coletor com eventos JÁ registrados em
uma tentativa anterior da mesma execução, lidos do checkpoint pelo Grupo 1. É o
que faz o resumo de uma retomada descrever a execução inteira, e não apenas as
fases que rodaram depois da falha.

Não há travamento (lock) — pensado para uso single-threaded/async cooperativo,
como o pipeline atual. Se o pipeline passar a rodar fases em paralelo com
threads reais, isso precisa ser revisitado.
"""

from __future__ import annotations

import contextvars
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

from .eventos import ExecutionEvent, StatusEvento, TipoEvento

# Fase da execução em curso, válida durante um `with coletor.fase(nome): ...`.
# Mesmo padrão de contexto global do tracer (Grupo 3): permite que código que
# não recebe `fase` por parâmetro (ex.: llm_fallback.py, chamado de dentro do
# modelo, não da fase) descubra em qual fase está rodando, sem acoplamento
# direto ao pipeline. Fora de qualquer fase medida, o valor é None.
_fase_atual: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "metrics_fase_atual", default=None
)


def obter_fase_atual() -> str | None:
    """Nome da fase em execução (dentro de um `coletor.fase(...)`), ou None."""
    return _fase_atual.get()


class ExecutionCollector:
    """Acumula ExecutionEvent de uma única execução do pipeline."""

    def __init__(self, run_id: str | None = None) -> None:
        self.run_id: str = run_id or str(uuid4())
        self._eventos: list[ExecutionEvent] = []
        self._inicio = time.perf_counter()
        self._chaves_dedup: set[str] = set()
        self._duracao_anterior_s: float = 0.0

    @property
    def eventos(self) -> list[ExecutionEvent]:
        """Cópia da lista de eventos registrados até agora (ordem de registro)."""
        return list(self._eventos)

    @property
    def duracao_execucao_s(self) -> float:
        """Tempo de parede da execução até agora, em segundos.

        Inclui o tempo já acumulado por tentativas anteriores da MESMA execução
        quando o coletor foi semeado por :meth:`restaurar` — sem isso, o resumo
        de uma retomada mostraria uma duração total MENOR que a soma das
        durações das fases (que inclui as fases restauradas), o que é
        aritmeticamente impossível e faria a auditoria desconfiar do número
        errado. Numa execução normal o acumulado é 0.0 e o valor é exatamente
        o tempo desde a criação do coletor, como antes.
        """
        return self._duracao_anterior_s + (time.perf_counter() - self._inicio)

    def restaurar(
        self,
        eventos: list[ExecutionEvent] | list[dict[str, Any]],
        *,
        duracao_anterior_s: float = 0.0,
    ) -> int:
        """Semeia o coletor com eventos de tentativas anteriores desta execução.

        Usado na retomada (Grupo 1, ``persistencia.py``): as fases restauradas de
        checkpoint não rodam de novo, logo não emitem evento algum. Sem trazer de
        volta os eventos que elas emitiram na tentativa original, ``gerar_resumo()``
        enxergaria só as fases re-executadas e o resumo final descreveria um
        pedaço da execução em vez dela inteira.

        Os eventos entram ANTES dos desta tentativa (é a ordem cronológica real) e
        preservam ``timestamp`` e ``duracao_s`` ORIGINAIS — a duração que vale para
        uma fase restaurada é a de quando ela de fato rodou, não a de agora. Cada
        evento restaurado é marcado com ``detalhes["restaurado_de_checkpoint"] =
        True``, para o resumo/relatório poder distinguir o que foi medido agora do
        que veio do disco.

        Aceita ``ExecutionEvent`` ou dicts (``ExecutionEvent.from_dict``), que é o
        formato gravado em disco. Devolve quantos eventos foram restaurados.
        """
        restaurados = [
            evento if isinstance(evento, ExecutionEvent) else ExecutionEvent.from_dict(evento)
            for evento in eventos
        ]
        for evento in restaurados:
            evento.detalhes["restaurado_de_checkpoint"] = True
        self._eventos = restaurados + self._eventos
        self._duracao_anterior_s = duracao_anterior_s
        return len(restaurados)

    def eventos_como_dict(self) -> list[dict[str, Any]]:
        """Todos os eventos serializados, na ordem em que foram registrados."""
        return [evento.to_dict() for evento in self._eventos]

    def registrar(
        self,
        *,
        fase: str,
        tipo: TipoEvento,
        nome: str,
        status: StatusEvento,
        duracao_s: float | None = None,
        chave_dedup: str | None = None,
        **detalhes: Any,
    ) -> ExecutionEvent | None:
        """Registra um evento genérico e devolve o ExecutionEvent criado.

        ``chave_dedup`` (opcional) previne contagem duplicada: quando informada
        e a mesma chave já foi registrada nesta execução, o evento é IGNORADO e
        a função devolve ``None``. Quem emite escolhe a chave — para eventos do
        ADK, a convenção é ``event.id`` quando existir, senão
        ``invocation_id + author`` (o mesmo evento pode reaparecer no streaming;
        parciais têm usage cumulativo e só o final deve contar). Eventos sem
        chave seguem o comportamento de sempre: todo registro entra.
        """
        if chave_dedup is not None:
            if chave_dedup in self._chaves_dedup:
                return None
            self._chaves_dedup.add(chave_dedup)
        evento = ExecutionEvent(
            run_id=self.run_id,
            fase=fase,
            tipo=tipo,
            nome=nome,
            status=status,
            duracao_s=duracao_s,
            detalhes=dict(detalhes),
        )
        self._eventos.append(evento)
        return evento

    @contextmanager
    def fase(self, nome: str) -> Iterator[None]:
        """Mede a duração de uma fase e registra sucesso ou falha ao sair.

        Uso: ``with coletor.fase("fase_1_revisao_independente"): ...``
        Em caso de exceção, registra status="falha" com o erro em detalhes e
        RE-LEVANTA a exceção (não engole erro do pipeline).
        """
        inicio = time.perf_counter()
        token = _fase_atual.set(nome)
        try:
            yield
        except Exception as exc:
            duracao_s = time.perf_counter() - inicio
            self.registrar(
                fase=nome, tipo="fase", nome=nome, status="falha",
                duracao_s=duracao_s, erro=str(exc), erro_tipo=type(exc).__name__,
            )
            raise
        else:
            duracao_s = time.perf_counter() - inicio
            self.registrar(fase=nome, tipo="fase", nome=nome, status="sucesso", duracao_s=duracao_s)
        finally:
            _fase_atual.reset(token)

    @contextmanager
    def tool(self, nome: str, *, fase: str) -> Iterator[None]:
        """Mede a duração de uma chamada de tool e registra sucesso ou falha.

        Uso: ``with coletor.tool("validar_completude", fase="fase_1"): ...``
        Mesma semântica de fase(): exceção é registrada e re-levantada.
        """
        inicio = time.perf_counter()
        try:
            yield
        except Exception as exc:
            duracao_s = time.perf_counter() - inicio
            self.registrar(
                fase=fase, tipo="tool", nome=nome, status="falha",
                duracao_s=duracao_s, erro=str(exc), erro_tipo=type(exc).__name__,
            )
            raise
        else:
            duracao_s = time.perf_counter() - inicio
            self.registrar(fase=fase, tipo="tool", nome=nome, status="sucesso", duracao_s=duracao_s)
