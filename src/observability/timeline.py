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


#: Contadores de token somáveis (``tokens_total`` é derivado, não se soma junto
#: para não contar duas vezes o que entrada+saída já representam).
_TOKENS_SOMAVEIS = ("tokens_entrada", "tokens_saida", "tokens_raciocinio", "tokens_cache")


def resumir_tokens(eventos: list[TraceEvent]) -> dict[str, Any]:
    """Soma o consumo de tokens de uma execução, por modelo e no total.

    Só considera o que os provedores efetivamente reportaram: se nenhum evento
    trouxe ``usage_metadata``, devolve ``{"medido": False}`` em vez de zeros —
    "não medido" e "consumiu zero" são coisas diferentes.
    """
    totais: dict[str, int] = {}
    por_modelo: dict[str, dict[str, int]] = {}
    medido = False

    for ev in eventos:
        atributos = ev.attributes or {}
        presentes = {k: v for k, v in atributos.items() if k in _TOKENS_SOMAVEIS}
        if not presentes:
            continue
        medido = True
        modelo = str(atributos.get("modelo") or "desconhecido")
        alvo = por_modelo.setdefault(modelo, {})
        for rotulo, valor in presentes.items():
            totais[rotulo] = totais.get(rotulo, 0) + valor
            alvo[rotulo] = alvo.get(rotulo, 0) + valor

    if not medido:
        return {"medido": False}

    totais["tokens_total"] = totais.get("tokens_entrada", 0) + totais.get("tokens_saida", 0)
    return {"medido": True, "totais": totais, "por_modelo": por_modelo}


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
        dur = fechamento.duration_s if fechamento else None
        dur_txt = f" [{dur:.2f} s]" if dur is not None else ""
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

    resumo = resumir_tokens(eventos)
    if resumo["medido"]:
        totais = resumo["totais"]
        linhas.append("")
        linhas.append(
            f"tokens: entrada={totais.get('tokens_entrada', 0)} "
            f"saída={totais.get('tokens_saida', 0)} "
            f"total={totais.get('tokens_total', 0)}"
        )
        for modelo, valores in resumo["por_modelo"].items():
            linhas.append(
                f"  · {modelo}: entrada={valores.get('tokens_entrada', 0)} "
                f"saída={valores.get('tokens_saida', 0)}"
            )
    else:
        linhas.append("")
        linhas.append("tokens: não reportados nesta execução (modo mock ou provedor sem medição)")

    return "\n".join(linhas)


def print_timeline(path: str | Path) -> None:
    """Imprime a linha do tempo reconstruída no stdout."""
    print(render_timeline(path))
