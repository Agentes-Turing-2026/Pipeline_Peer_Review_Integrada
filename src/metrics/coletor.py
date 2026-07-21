"""Coletor de eventos de execução — Grupo 2.

ExecutionCollector acumula ExecutionEvent (eventos.py) em memória durante uma
execução do pipeline. Não sabe nada sobre peer review, validação ou tools
específicas — é um acumulador genérico com dois context managers de
conveniência (fase(), tool()) que medem duração e registram sucesso/falha
automaticamente. Eventos de outros tipos (validacao, retry, falha, decisao_final)
são registrados via registrar() diretamente por quem tiver essa informação
(ex.: pipeline.py, ao inspecionar o resultado de validar_com_tentativas()).

Não há travamento (lock) — pensado para uso single-threaded/async cooperativo,
como o pipeline atual. Se o pipeline passar a rodar fases em paralelo com
threads reais, isso precisa ser revisitado.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator
from uuid import uuid4

from .eventos import ExecutionEvent, StatusEvento, TipoEvento


class ExecutionCollector:
    """Acumula ExecutionEvent de uma única execução do pipeline."""

    def __init__(self, run_id: str | None = None) -> None:
        self.run_id: str = run_id or str(uuid4())
        self._eventos: list[ExecutionEvent] = []

    @property
    def eventos(self) -> list[ExecutionEvent]:
        """Cópia da lista de eventos registrados até agora (ordem de registro)."""
        return list(self._eventos)

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
        **detalhes: Any,
    ) -> ExecutionEvent:
        """Registra um evento genérico e devolve o ExecutionEvent criado."""
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
