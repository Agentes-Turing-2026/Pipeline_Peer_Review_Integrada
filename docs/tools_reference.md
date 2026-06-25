# Tools determinísticas do Grupo 2 — referência e conversão Atividade 5 → contrato oficial

As tools do Grupo 2 são funções **puras, determinísticas e sem LLM** que auditam as
saídas do pipeline. Elas nasceram na **Atividade 5**, escritas contra um contrato
**antigo** (escala 1-5, critérios `metodologia`/`relevancia`, campos `recomendacao` e
`evidencias`), e foram **adaptadas** aqui ao **contrato oficial do Grupo 3**
(`ReviewSchema` e `EditorVerdictSchema`, em [`../src/review_schema.py`](../src/review_schema.py)).

Este documento registra **o que mudou na conversão** — o critério exigido pela tarefa do
Grupo 2 ("quando for necessário adaptar dados antigos, documentar explicitamente a
conversão").

---

## 1. Tabela de conversão do contrato (Atividade 5 → oficial)

### 1.1 Escalas

| Conceito | Atividade 5 | Contrato oficial | Observação |
|---|---|---|---|
| Notas dos critérios | inteiro **1–5** | inteiro **1–4** (`4=Excelente … 1=Fraco`) | Reescala. Limiares determinísticos recalibrados (ver §2). |
| Nota geral / recomendação editorial | nota **1–5** + campo textual `recomendacao` | **1–4** (`ESCALA_NOTA_GERAL`: `4=Aceitar … 1=Rejeitar`) | Uma única escala em todo o sistema. |
| Confiança do revisor | (não padronizada) | nota **1–3** + justificativa | Bloco próprio no `ReviewSchema`. |
| Decisão do editor | — (não havia veredito tipado) | **1–4** (`ESCALA_VEREDITO` = `ESCALA_NOTA_GERAL`) | Mesma escala da `nota_geral`. |

### 1.2 Campos / nomes

| Atividade 5 | Contrato oficial | Tipo de conversão |
|---|---|---|
| `criterios.metodologia` | `solidez_tecnica` | **Renomeado.** |
| `criterios.relevancia` | `significancia` | **Renomeado.** |
| `criterios.originalidade` | `originalidade` | Mantido (renomeado de chave-int para bloco `{nota, justificativa}`). |
| `criterios.clareza` | `clareza` | Mantido (idem). |
| nota solta `int` por critério | bloco `{nota, justificativa}` | **Estrutural.** Toda nota agora carrega justificativa obrigatória. |
| `recomendacao` (`aceitar`/`revisar`/`rejeitar`) | **deixa de existir** | **Removido.** A recomendação é derivada de `nota_geral` (parecer) / `decisao` (veredito). |
| `evidencias` (`{afirmacao, secao, trecho_manuscrito}`) | **deixa de existir** | **Removido.** O contrato oficial não tem campo de evidências ancoradas — ver a *limitação* em §3. |
| `id_revisao` | `revisor` (no parecer) | Identificação passou a ser o nome do papel (`statistician`, etc.). |
| — | `EditorVerdictSchema` (`decisao`, `notas_por_revisor`, `criticas`, …) | **Novo.** A 3ª fase (veredito do editor) não existia na Atividade 5. |

---

## 2. Como cada tool foi adaptada

### 2.1 `validar_completude(dado, tipo="auto")` — completude estrutural (Pedro)

Auditoria estrutural **dual-mode**: parecer (`ReviewSchema`) **e** veredito
(`EditorVerdictSchema`). Reporta *todos* os problemas de uma vez (`campos_faltando`,
`campos_invalidos`, `score_completude`) em vez de levantar na primeira falha. Adaptação:
critérios renomeados, escala 1-4 (confiança 1-3) e cada critério virou bloco
`{nota, justificativa}`.

### 2.2 `checar_coerencia(dado, tipo="auto")` — coerência semântica (João Pedro Souza)

Evolução direta da skill `checar-coerencia` da Atividade 5. **A construção foi
preservada** (helpers `_checar_*` devolvendo `{tipo, detalhe} | None`, lista de `avisos`,
e `score_coerencia = (checks_total − checks_falhos) / checks_total`); só mudou *o que* é
comparado:

| Checagem (Atividade 5) | Checagem (oficial) | O que aconteceu |
|---|---|---|
| `recomendacao_vs_nota` | — | **Removida** (não há mais `recomendacao`). |
| `criterios_vs_nota` (limiar **1.5**) | `nota_vs_criterios` (limiar **1.0**), modo *parecer* | Reescalado de 1-5 para 1-4. Média dos 4 critérios vs `nota_geral`. |
| `evidencia_sem_ancoragem` / `evidencia_secao_invalida` | — | **Removidas** (não há campo `evidencias`). |
| — | `decisao_vs_notas`, modo *veredito* | **Nova.** `decisao` vs média de `notas_por_revisor`. |
| — | `aceite_com_critica_bloqueante`, modo *veredito* | **Nova.** `decisao=4` com crítica `tipo="critica"`. |
| — | `critica_sem_revisor`, modo *veredito* | **Nova.** `revisor` da crítica fora de `notas_por_revisor`. |

> **Limiar 1.5 → 1.0.** Na escala 1-5, uma divergência de até 1.5 ponto era tolerada;
> na escala 1-4, comprimida, o limiar passou a **1.0** para manter a sensibilidade
> proporcional (uma diferença de mais de um nível inteiro é incoerente).

> **Mudança de comportamento (defensivo).** A versão da Atividade 5 retornava
> `status="erro"` quando faltava `nota_geral`/`recomendacao`, orientando rodar
> `validar_completude` antes. No pipeline integrado quem **barra** a entrada é o gate
> `validar_completude` (chamado antes); então aqui a tool é **defensiva**: campos
> ausentes viram `avisos` (checagem pulada) e `status` só é `"erro"` para entrada que
> não é `dict`. Isso a torna segura de chamar em qualquer ponto sem quebrar a demo.

### 2.3 `auditar_decisao_final(veredito)` — auditoria da decisão (Giulio)

Continuação do `calcular_score_decisao`/`montar_parecer_final` da Atividade 5, agora
sobre o `EditorVerdictSchema`. **Chama `checar_coerencia` por dentro** e resume a decisão
(média/divergência das notas, críticas por tipo, `requer_revisao_humana`).

---

## 3. O que ficou de fora (limitações documentadas)

- **`evidencias` (ancoragem ao manuscrito).** A checagem de *grounding* da Atividade 5
  (`evidencia_sem_ancoragem` / `evidencia_secao_invalida`) **não tem equivalente** no
  contrato oficial, porque `EditorVerdictSchema`/`ReviewSchema` não carregam evidências
  com `secao`/`trecho_manuscrito`. Essa verificação foi **removida** — se o contrato
  oficial passar a incluir evidências no futuro, ela pode ser reintroduzida sem alterar
  a estrutura das demais checagens.
- **`recomendacao` textual.** Substituída por comparação **nota↔nota** (`nota_geral` no
  parecer, `decisao` no veredito), que é mais robusta e não depende de string livre.

---

## 4. Quando cada tool roda no pipeline

| Tool | Ponto no pipeline | Bloqueia? |
|---|---|---|
| `validar_completude` | Fase 1/2 — auditoria estrutural de cada parecer; e do veredito antes do relatório. | Sim (gate). |
| `checar_coerencia` | Fase 2 (parecer revisado) e Fase 3 (veredito do editor) — coerência semântica. | Não — marca para editor humano. |
| `auditar_decisao_final` | Após a Fase 3, antes da Fase 4 — log de auditoria da decisão final. | Não — registra. |

Ordem típica: **`validar_completude` → `checar_coerencia` → `auditar_decisao_final`**.
Todas são determinísticas, offline, sobre `dict` puro (só biblioteca padrão).
