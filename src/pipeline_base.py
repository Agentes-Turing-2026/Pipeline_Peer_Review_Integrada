"""Camada de orquestração GENÉRICA de pipelines multiagentes.

Este módulo NÃO sabe nada sobre peer review. Ele define apenas o "esqueleto"
reutilizável de um pipeline em fases sequenciais, no qual a saída de uma fase
alimenta a próxima. Qualquer domínio multiagente (triagem de documentos, geração
de relatórios, moderação, etc.) pode reaproveitar esta camada, bastando
implementar suas próprias fases (``PipelinePhase``) e montá-las em um
``Pipeline``.

Princípios de projeto:

1. **Separação orquestração × domínio.** Aqui mora só a mecânica de encadear
   fases, propagar dados, acumular artefatos e registrar progresso. A lógica
   específica (quais agentes rodar, quais schemas validar) vive nas fases
   concretas do domínio — por exemplo, em ``pipeline.py`` (peer review).
2. **Encadeamento estrito + contexto compartilhado.** Cada fase recebe a saída
   da fase anterior (``data``) e um ``PipelineContext`` com a entrada original e
   os artefatos já produzidos. Assim uma fase tardia (ex.: relatório) pode
   consultar saídas de fases anteriores sem quebrar o encadeamento.
3. **Tipagem.** ``PipelinePhase`` é genérica em ``(entrada, saída)`` para que o
   contrato de cada fase fique explícito e verificável por ferramentas de tipo.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

TIn = TypeVar("TIn")
TOut = TypeVar("TOut")


class PipelineContext:
    """Estado compartilhado e agnóstico de domínio, vivo durante uma execução.

    Carrega a entrada original do pipeline (``initial_input``), uma configuração
    livre (``config``) e o acúmulo de artefatos produzidos por cada fase
    (``artifacts``: ``nome_da_fase -> saída``). Fases podem ler artefatos de
    fases anteriores por aqui, sem depender da ordem de chamada.
    """

    def __init__(self, initial_input: Any, config: dict[str, Any] | None = None):
        self.initial_input = initial_input
        self.config: dict[str, Any] = dict(config or {})
        self.artifacts: dict[str, Any] = {}

    def record(self, phase_name: str, output: Any) -> None:
        """Registra a saída de uma fase sob o seu nome."""
        self.artifacts[phase_name] = output

    def get(self, phase_name: str, default: Any = None) -> Any:
        """Recupera a saída de uma fase já executada (ou ``default``)."""
        return self.artifacts.get(phase_name, default)


class PipelinePhase(ABC, Generic[TIn, TOut]):
    """Uma fase do pipeline: transforma a saída anterior na sua própria saída.

    Subclasses concretas (do domínio) definem ``name`` e implementam ``run``.
    O contrato de tipos ``(TIn -> TOut)`` torna explícito o que a fase consome e
    o que produz — no caso do peer review, sempre os schemas oficiais.
    """

    #: Nome estável e único da fase (usado como chave de artefato e em logs).
    name: str = "phase"

    @abstractmethod
    def run(self, data: TIn, context: PipelineContext) -> TOut:
        """Executa a fase a partir de ``data`` (saída anterior) e do ``context``."""
        raise NotImplementedError


@dataclass
class PipelineResult:
    """Resultado de uma execução completa do pipeline."""

    #: Saída de cada fase, por nome (``nome_da_fase -> saída``).
    outputs: dict[str, Any] = field(default_factory=dict)
    #: Saída da última fase (o produto final do pipeline).
    final: Any = None


class Pipeline:
    """Orquestrador genérico: encadeia ``PipelinePhase`` em sequência estrita.

    A saída de cada fase vira a entrada da próxima; em paralelo, todas as saídas
    ficam disponíveis no ``PipelineContext`` para fases posteriores consultarem.
    """

    def __init__(
        self,
        phases: list[PipelinePhase],
        name: str = "pipeline",
        logger: logging.Logger | None = None,
    ):
        if not phases:
            raise ValueError("Um pipeline precisa de ao menos uma fase.")
        nomes = [p.name for p in phases]
        if len(nomes) != len(set(nomes)):
            raise ValueError(f"Há fases com nomes duplicados: {nomes}.")
        self.phases = list(phases)
        self.name = name
        self._logger = logger

    @property
    def phase_names(self) -> list[str]:
        """Nomes das fases na ordem de execução (útil para documentação/demo)."""
        return [p.name for p in self.phases]

    def _log(self, message: str) -> None:
        if self._logger is not None:
            self._logger.info(message)

    def run(
        self,
        initial_input: Any,
        config: dict[str, Any] | None = None,
        verbose: bool = True,
    ) -> PipelineResult:
        """Roda todas as fases em ordem e devolve as saídas acumuladas.

        Parameters
        ----------
        initial_input:
            Entrada de domínio da primeira fase (ex.: o texto do artigo).
        config:
            Configuração livre disponível às fases via ``context.config``.
        verbose:
            Se ``True``, imprime o progresso fase a fase.
        """
        context = PipelineContext(initial_input, config)
        data: Any = initial_input

        for indice, phase in enumerate(self.phases, start=1):
            cabecalho = f"[{self.name}] Fase {indice}/{len(self.phases)}: {phase.name}"
            if verbose:
                print(cabecalho + " ...")
            self._log(cabecalho)

            data = phase.run(data, context)
            context.record(phase.name, data)

        return PipelineResult(outputs=dict(context.artifacts), final=data)
