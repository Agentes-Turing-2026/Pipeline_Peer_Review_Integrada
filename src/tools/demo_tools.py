"""Demo offline das tools determinísticas do Grupo 2.

Roda **sem internet e sem GOOGLE_API_KEY**, demonstrando os três itens que o PDF pede:
  1. a tool de completude rodando sobre um parecer do contrato oficial;
  2. a tool de coerência / auditoria detectando um problema;
  3. tudo offline, sobre exemplos versionados em ``src/examples/``.

As tools de coerência (João Pedro) e auditoria (Giulio) são importadas de forma
GUARDADA: enquanto não estiverem no repositório, seus cenários aparecem como
PENDENTES e o restante da demo roda normalmente. Assim que os arquivos existirem,
os cenários acendem sozinhos — sem mexer nesta demo.

Uso:
    python src/tools/demo_tools.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent      # .../src/tools
SRC = HERE.parent                           # .../src
EXAMPLES = SRC / "examples"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tools.validar_completude import validar_completude  # noqa: E402

# Importações guardadas das tools dos colegas (ainda em integração).
try:
    from tools.checar_coerencia import checar_coerencia  # type: ignore  # noqa: E402
except ImportError:
    checar_coerencia = None
try:
    from tools.auditar_decisao_final import auditar_decisao_final  # type: ignore  # noqa: E402
except ImportError:
    auditar_decisao_final = None

LARGURA = 72


def _sep(titulo: str = "") -> None:
    if titulo:
        print(f"\n{'=' * LARGURA}")
        print(f"  {titulo}")
        print("=" * LARGURA)
    else:
        print("-" * LARGURA)


def _carregar(nome: str) -> dict:
    return json.loads((EXAMPLES / nome).read_text(encoding="utf-8"))


def _mostrar(resultado: dict) -> None:
    print(json.dumps(resultado, indent=2, ensure_ascii=False))


def _pendente(tool: str, dono: str) -> None:
    print(f"  [PENDENTE] tool '{tool}' ainda não integrada (responsável: {dono}).")
    print("  Este cenário acende automaticamente quando o arquivo existir.")


# ---------------------------------------------------------------------------
# Cenários de completude (Tool 1 — Pedro)
# ---------------------------------------------------------------------------

def cenario_completude_parecer_valido() -> None:
    _sep("1) COMPLETUDE · parecer do contrato oficial (válido)")
    print("Entrada: src/examples/example_valid_output.json (ReviewSchema)\n")
    _mostrar(validar_completude(_carregar("example_valid_output.json")))


def cenario_completude_parecer_incompleto() -> None:
    _sep("2) COMPLETUDE · parecer incompleto (detecta o problema)")
    print("Entrada: src/examples/example_parecer_incompleto.json\n")
    _mostrar(validar_completude(_carregar("example_parecer_incompleto.json")))


def cenario_completude_veredito_incompleto() -> None:
    _sep("3) COMPLETUDE · veredito do editor incompleto (detecta o problema)")
    print("Entrada: src/examples/example_veredito_incompleto.json (EditorVerdictSchema)\n")
    _mostrar(validar_completude(_carregar("example_veredito_incompleto.json")))


# ---------------------------------------------------------------------------
# Cenários de coerência (Tool 2 — João Pedro) e auditoria (Tool 3 — Giulio)
# ---------------------------------------------------------------------------

def _veredito_para_auditoria() -> tuple[dict, str]:
    """Veredito incoerente (do João) se existir; senão cai no válido versionado."""
    incoerente = EXAMPLES / "example_editor_verdict_incoerente.json"
    if incoerente.exists():
        return _carregar(incoerente.name), incoerente.name
    return _carregar("example_editor_verdict_output.json"), "example_editor_verdict_output.json"


def cenario_coerencia() -> None:
    _sep("4) COERÊNCIA · detectando incoerência semântica no veredito")
    if checar_coerencia is None:
        _pendente("checar_coerencia", "João Pedro Souza")
        return
    veredito, nome = _veredito_para_auditoria()
    print(f"Entrada: src/examples/{nome}\n")
    _mostrar(checar_coerencia(veredito))


def cenario_auditoria() -> None:
    _sep("5) AUDITORIA · resumo determinístico da decisão final")
    if auditar_decisao_final is None:
        _pendente("auditar_decisao_final", "Giulio")
        return
    veredito, nome = _veredito_para_auditoria()
    print(f"Entrada: src/examples/{nome}\n")
    _mostrar(auditar_decisao_final(veredito))


def main() -> None:
    _sep("DEMO OFFLINE · Tools determinísticas (Grupo 2)")
    print("Sem internet, sem GOOGLE_API_KEY, lógica 100% determinística (sem LLM).")
    cenario_completude_parecer_valido()
    cenario_completude_parecer_incompleto()
    cenario_completude_veredito_incompleto()
    cenario_coerencia()
    cenario_auditoria()
    _sep()
    print("Fim da demo.")


if __name__ == "__main__":
    main()
