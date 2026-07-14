"""Reconstrução da LINHA DO TEMPO de uma execução a partir do JSONL de trace.

Responde à pergunta central da observabilidade — "por onde a execução passou?" —
relendo os eventos gravados e remontando a árvore:

    run
     ├─ fase 1 ... (agentes/eventos)
     ├─ fase 2 ...
     ├─ fase 3 ... (tools)
     └─ fase 4 (relatório)  -> arquivos gerados
    fim (status + duração)

Não depende de nada além da biblioteca padrão, então roda offline em clone limpo.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .events import EventType, Status, TraceEvent


def load_events(path: str | Path) -> list[TraceEvent]:
    """Lê um arquivo JSONL de trace e devolve os eventos em ordem cronológica."""
    eventos: list[TraceEvent] = []
    for linha in Path(path).read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha:
            continue
        eventos.append(TraceEvent.from_dict(json.loads(linha)))
    eventos.sort(key=lambda e: e.timestamp)
    return eventos


def build_timeline(eventos: list[TraceEvent]) -> dict[str, Any]:
    """Agrega os eventos por span, montando a hierarquia (pai -> filhos).

    Devolve ``{"roots": [...], "spans": {span_id: nó}}``. Cada nó guarda o
    ``span_start`` (aberto), o ``span_end`` (fechado, com duração/status) e os
    eventos pontuais ancorados nele.
    """
    spans: dict[str, dict[str, Any]] = {}
    filhos: dict[str | None, list[str]] = {}

    def _no(span_id: str) -> dict[str, Any]:
        return spans.setdefault(
            span_id,
            {"span_id": span_id, "abertura": None, "fechamento": None, "eventos": []},
        )

    for ev in eventos:
        if ev.event_type in (EventType.SPAN_START.value, EventType.RUN_START.value):
            no = _no(ev.span_id or "")
            no["abertura"] = ev
            filhos.setdefault(ev.parent_span_id, [])
            if ev.span_id not in filhos[ev.parent_span_id]:
                filhos[ev.parent_span_id].append(ev.span_id or "")
        elif ev.event_type in (EventType.SPAN_END.value, EventType.RUN_END.value):
            _no(ev.span_id or "")["fechamento"] = ev
        else:
            # Evento pontual (log/erro/adk) — ancorado no seu span.
            _no(ev.span_id or "")["eventos"].append(ev)

    roots = filhos.get(None, [])
    return {"roots": roots, "spans": spans, "filhos": filhos}


_ICONE_STATUS = {
    Status.OK.value: "OK",
    Status.ERROR.value: "ERR",
    Status.WARNING.value: "!",
    Status.RUNNING.value: "...",
}


def render_timeline(path: str | Path) -> str:
    """Monta uma representação textual em árvore da execução (para imprimir)."""
    eventos = load_events(path)
    if not eventos:
        return "(trace vazio)"
    arvore = build_timeline(eventos)
    linhas: list[str] = []

    def _fmt_span(no: dict[str, Any]) -> str:
        abertura: TraceEvent | None = no["abertura"]
        fechamento: TraceEvent | None = no["fechamento"]
        nome = abertura.name if abertura else "(desconhecido)"
        kind = abertura.kind if abertura else None
        status = fechamento.status if fechamento else (abertura.status if abertura else "?")
        icone = _ICONE_STATUS.get(status, "?")
        dur = fechamento.duration_ms if fechamento else None
        dur_txt = f" [{dur:.0f} ms]" if dur is not None else ""
        autor = f" (autor: {abertura.author})" if abertura and abertura.author else ""
        kind_txt = f"<{kind}> " if kind else ""
        return f"{icone} {kind_txt}{nome}{autor}{dur_txt}"

    def _walk(span_id: str, nivel: int) -> None:
        no = arvore["spans"].get(span_id)
        if no is None:
            return
        prefixo = "  " * nivel
        linhas.append(prefixo + _fmt_span(no))
        for ev in no["eventos"]:
            ic = _ICONE_STATUS.get(ev.status, "·")
            autor = f" [{ev.author}]" if ev.author else ""
            extra = ""
            if ev.attributes:
                pares = ", ".join(f"{k}={v}" for k, v in list(ev.attributes.items())[:4])
                extra = f" — {pares}"
            linhas.append(f"{prefixo}  · {ic} {ev.name}{autor}{extra}")
        for filho in arvore["filhos"].get(span_id, []):
            _walk(filho, nivel + 1)

    for root in arvore["roots"]:
        _walk(root, 0)
    return "\n".join(linhas)


def print_timeline(path: str | Path) -> None:
    """Imprime a linha do tempo reconstruída no stdout."""
    print(render_timeline(path))
