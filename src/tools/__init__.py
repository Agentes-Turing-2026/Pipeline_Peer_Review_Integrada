"""Tools determinísticas do Grupo 2 sobre o contrato oficial.

Três ferramentas SEM LLM que auditam os schemas oficiais do pipeline
(``ReviewSchema`` e ``EditorVerdictSchema``, definidos em ``src/review_schema.py``):

- :func:`validar_completude` — auditoria estrutural de um parecer de revisor.
- :func:`checar_coerencia` — coerência semântica de um parecer ou veredito.
- :func:`auditar_decisao_final` — resumo de auditoria do veredito do editor.

Todas operam sobre ``dict`` puro (o JSON já carregado), usando apenas a biblioteca
padrão, de modo a rodar offline em um clone limpo. A adaptação a partir do contrato
antigo da Atividade 5 está documentada em ``docs/tools_reference.md``.
"""

from __future__ import annotations

from .validar_completude import validar_completude
from .checar_coerencia import checar_coerencia            # João Pedro Souza

# A linha abaixo é adicionada pelo dono da tool (ver TODO_Grupo2.md):
# from .auditar_decisao_final import auditar_decisao_final  # Giulio

__all__ = [
    "validar_completude",
    "checar_coerencia",
    # "auditar_decisao_final",
]
