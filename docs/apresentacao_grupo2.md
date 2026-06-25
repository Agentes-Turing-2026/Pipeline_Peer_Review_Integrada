# Grupo 2 — Tools Determinísticas de Auditoria

## O que o Grupo 2 fez

Implementamos **três tools sem LLM** que auditam as saídas do pipeline de peer review. Elas não tomam decisões — só analisam e sinalizam problemas de forma rastreável. Rodam 100% offline, sobre `dict` puro, sem nenhuma chamada à API.

Estão em `src/tools/`:

```
src/tools/
  validar_completude.py       ← Tool 1 (Pedro)
  checar_coerencia.py         ← Tool 2 (João Pedro)
  auditar_decisao_final.py    ← Tool 3 (Giulio)
  demo_tools.py               ← demo offline das 3 tools
  tests/
    test_validar_completude.py
    test_checar_coerencia.py
    test_auditar_decisao_final.py
```

---

## Tool 1 — `validar_completude`

**Arquivo:** `src/tools/validar_completude.py`

### O que faz

Verifica se um parecer de revisor ou um veredito do editor está **estruturalmente correto**: todos os campos obrigatórios presentes, notas dentro das escalas certas, justificativas não vazias. Reporta **todos os problemas de uma vez** (não para no primeiro) e devolve um `score_completude` de 0 a 1.

Funciona nos dois formatos do contrato oficial:
- **Parecer** (`ReviewSchema`): verifica `revisor`, os 4 critérios (`solidez_tecnica`, `originalidade`, `significancia`, `clareza`), `nota_geral` (escala 1–4) e `confianca` (escala 1–3). Cada um precisa ter `nota` e `justificativa`.
- **Veredito** (`EditorVerdictSchema`): verifica `decisao` (1–4), `justificativa`, `sintese` e `notas_por_revisor` (dict não vazio).

### Diferença do Pydantic (Grupo 1)

O Pydantic do Grupo 1 **bloqueia** o pipeline na primeira falha. Esta tool **audita**: percorre tudo, lista cada problema encontrado e devolve um score. São camadas complementares — o Pydantic é o portão, a completude é o relatório detalhado.

### O que retorna

```json
{
  "status": "ok",
  "tipo": "parecer",
  "completo": false,
  "campos_faltando": ["significancia"],
  "campos_invalidos": [
    { "campo": "clareza", "motivo": "'nota' fora do intervalo 1..4" },
    { "campo": "confianca", "motivo": "'justificativa' vazia ou nao e string" }
  ],
  "score_completude": 0.5714,
  "mensagem": "parecer incompleto: 1 campo(s) faltando e 2 campo(s) invalido(s)"
}
```

### Onde é chamada no pipeline

**Arquivo:** `src/pipeline.py` — **linhas 202–208**, dentro de `IndependentReviewPhase.run()`.

É chamada **após a Fase 1**, um vez por revisor, logo depois que o Pydantic já validou o schema:

```python
# src/pipeline.py — linha 202
if _tool_completude is not None:
    for rid, review in reviews.items():
        audit = _tool_completude(review.model_dump())
        logger.info(
            "[completude] Fase 1 '%s': score=%.4f completo=%s",
            rid, audit["score_completude"], audit["completo"],
        )
```

**O que aparece no log quando roda:**
```
[completude] Fase 1 'statistician': score=1.0000 completo=True
[completude] Fase 1 'domain_expert': score=1.0000 completo=True
[completude] Fase 1 'copyeditor':    score=1.0000 completo=True
```

---

## Tool 2 — `checar_coerencia`

**Arquivo:** `src/tools/checar_coerencia.py`

### O que faz

Detecta **contradições semânticas** que passam pela validação do Pydantic e pela completude. Um parecer pode estar estruturalmente perfeito mas ser incoerente — por exemplo, dar nota 4 em todos os critérios e depois nota geral 1. Esta tool pega isso.

Funciona nos dois modos:

**Modo parecer:** compara a média dos 4 critérios com a `nota_geral`. Se a diferença for maior que 1.0 ponto (na escala 1–4), é incoerente.

**Modo veredito:** faz 3 checagens:
1. **`decisao_vs_notas`** — a decisão editorial diverge mais de 1.0 ponto da média das notas dos revisores?
2. **`aceite_com_critica_bloqueante`** — decisão 4 (Aceitar) convivendo com uma crítica do tipo `"critica"` (bloqueante)?
3. **`critica_sem_revisor`** — uma crítica foi atribuída a um revisor que não está em `notas_por_revisor`?

### O que retorna (exemplo com problema detectado)

```json
{
  "status": "ok",
  "tipo": "veredito",
  "coerente": false,
  "inconsistencias": [
    {
      "tipo": "decisao_vs_notas",
      "detalhe": "decisão é 4 (Aceitar), mas a média das notas é 2.33 (Rejeitar com ressalvas); diferença 1.67 > 1.0"
    },
    {
      "tipo": "aceite_com_critica_bloqueante",
      "detalhe": "decisão 4 (Aceitar) apesar de 1 crítica(s) bloqueante(s)"
    },
    {
      "tipo": "critica_sem_revisor",
      "detalhe": "crítica atribuída a 'external_referee' que não está em notas_por_revisor"
    }
  ],
  "avisos": [],
  "score_coerencia": 0.0
}
```

### Onde é chamada no pipeline

É chamada **por dentro da `auditar_decisao_final`** (Tool 3), não diretamente pelo pipeline. Quando o arquivo `checar_coerencia.py` existe, a Tool 3 a importa automaticamente e herda suas inconsistências.

```python
# src/tools/auditar_decisao_final.py — importação guardada
try:
    from tools.checar_coerencia import checar_coerencia
except Exception:
    checar_coerencia = None  # pipeline continua sem ela
```

Isso significa que ao rodar a Tool 3, as inconsistências semânticas da Tool 2 aparecem automaticamente no resumo de auditoria.

---

## Tool 3 — `auditar_decisao_final`

**Arquivo:** `src/tools/auditar_decisao_final.py`

### O que faz

Produz um **log de auditoria rastreável** do veredito do Editor-Chefe. Não recalcula nem altera a decisão — só a torna auditável. Faz três coisas:

1. **Agrega as notas dos revisores:** calcula média e divergência (max − min). Se divergência ≥ 2, o veredito precisa de revisão humana.
2. **Chama `checar_coerencia`** (quando disponível) e herda as inconsistências semânticas encontradas.
3. **Decide `requer_revisao_humana`:** `True` se divergência ≥ 2 **ou** se há inconsistências semânticas.

Tudo isso vai para o campo `auditoria_veredito` no `final_report.json`.

### O que retorna

```json
{
  "status": "ok",
  "decisao": 4,
  "decisao_rotulo": "Aceitar",
  "media_notas": 2.3333,
  "divergencia_notas": 1,
  "criticas_por_tipo": { "fraqueza": 2, "critica": 1 },
  "requer_revisao_humana": true,
  "inconsistencias": [
    { "tipo": "decisao_vs_notas", "detalhe": "..." },
    { "tipo": "aceite_com_critica_bloqueante", "detalhe": "..." },
    { "tipo": "critica_sem_revisor", "detalhe": "..." }
  ],
  "resumo_auditoria": "Decisão editorial: 4 (Aceitar). Média: 2.3333 (entre 2 e 3); divergência 1. Críticas: 2 fraqueza(s) e 1 crítica(s) bloqueante(s). Coerência: 3 inconsistência(s) detectada(s). Revisão humana RECOMENDADA.",
  "veredito": { ... }
}
```

### Onde é chamada no pipeline

**Arquivo:** `src/pipeline.py` — **linhas 294–299**, dentro de `EditorVerdictPhase.run()`.

É chamada **ao final da Fase 3**, depois que o Editor-Chefe produziu e o Pydantic validou o veredito, antes de passar para a Fase 4:

```python
# src/pipeline.py — linha 294
if _tool_auditoria is not None:
    auditoria = _tool_auditoria(verdict.model_dump())
    logger.info("[auditoria] %s", auditoria["resumo_auditoria"])
    if auditoria["requer_revisao_humana"]:
        logger.warning("[auditoria] Veredito requer revisão humana.")
    context.config["_auditoria_veredito"] = auditoria
```

O resultado fica salvo em `context` e a Fase 4 o inclui automaticamente no `final_report.json` sob a chave `"auditoria_veredito"`.

**O que aparece no log quando roda:**
```
[auditoria] Decisão editorial: 3 (Aceitar com ressalvas). Média: 2.6667 (entre 2 e 3);
            divergência 1. Coerência: 0 inconsistência(s). Revisão humana não recomendada.
```

---

## Como as tools se encadeiam

```
Fase 1 — Revisão Independente
  └─ Pydantic valida o schema (Grupo 1)
  └─ validar_completude(parecer)         ← Tool 1: score estrutural por revisor

Fase 2 — Leitura Cruzada
  └─ Pydantic valida o schema (Grupo 1)

Fase 3 — Editor-Chefe
  └─ Pydantic valida o schema (Grupo 1)
  └─ auditar_decisao_final(veredito)     ← Tool 3: auditoria da decisão
       └─ checar_coerencia(veredito)     ← Tool 2: chamada por dentro da Tool 3

Fase 4 — Relatório Final
  └─ final_report.json["auditoria_veredito"]  ← resultado da Tool 3 preservado
```

---

## Como rodar

```bash
# Demo offline das 3 tools (sem internet, sem API key)
.venv/bin/python src/tools/demo_tools.py

# Testes automatizados (33 testes, 100% passando)
.venv/bin/pytest src/tools/tests/ -v

# Pipeline completo em modo mock (ver tools no log)
.venv/bin/python main.py mock
cat src/logs/pipeline.log
```

---

## O que ainda está pendente

| Item | Situação |
|---|---|
| Integração da `checar_coerencia` diretamente na Fase 2 (nos pareceres revisados) | Não implementado — a tool já existe e funciona, mas só é chamada pela Tool 3. Poderia ser adicionada ao `CrossReviewPhase` seguindo o mesmo padrão da Tool 1. |
| Erro no modo API com Gemini | A Fase 2 (`cross_review.py`) gera erro de schema com a versão atual da API Gemini (`additionalProperties` não suportado). Não é código do Grupo 2. |
