"""Métricas e resumo auditável de execução — Grupo 2.

Submódulos (importados diretamente, ex.: ``from metrics.coletor import ...``):
  - :mod:`metrics.eventos`   — ``ExecutionEvent`` (evento atômico da execução);
  - :mod:`metrics.coletor`   — ``ExecutionCollector`` (acumula eventos, dedup);
  - :mod:`metrics.adk_usage` — tokens reais via ``usage_metadata`` do ADK;
  - :mod:`metrics.resumo`    — ``gerar_resumo()`` / ``ResumoExecucao``;
  - :mod:`metrics.exportar`  — terminal (``imprimir_resumo``) e JSON.
"""

from __future__ import annotations
