# Grupo 2 — Métricas, Tools e Resumo Auditável com ADK

## 1. Contexto da atividade

Esta entrega pediu ao Grupo 2 para transformar o que já acontece dentro de
uma execução do pipeline — chamadas de tool, validações de schema, retries,
falhas, decisão final — em **eventos estruturados**, calcular a partir deles
**métricas simples** (duração, contagens, decisão, status) e produzir um
**resumo auditável** da execução. O requisito explícito era que tudo isso
fosse pensado desde já para evoluir em direção a observabilidade estilo
ADK/OpenTelemetry, sem depender de reler `pipeline.log` como texto solto.

Isso se encaixa entre os outros dois grupos sem invadir o território deles:

- **Grupo 1 (validação/retry estruturado)** já produz, em
  `src/validacao_retry.py`, um `ResultadoValidacao` por chamada de
  `validar_com_tentativas()` (`sucesso`, `tentativas_usadas`, `historico`,
  `erro_final`). O Grupo 2 **consome** esse objeto — não recalcula nem
  reimplementa validação — e só traduz `ResultadoValidacao` em eventos de
  métricas (`_registrar_validacao()` em `src/pipeline.py`).
- **Grupo 3 (base de observabilidade/traces)** é responsável pela hierarquia
  de spans com `parent_span_id` e pela ponte com o `Runner` do ADK. No
  momento desta entrega, esse trabalho existe em branches remotas
  (`feature/grupo1-integracao-observabilidade`, `observabilidade-testes`)
  ainda **não mescladas em `main`**. Para não depender de código que pode
  mudar antes de ser integrado, `src/metrics/` foi construído de forma
  **autocontida** em cima de `main` — mas com nomes de campo pensados para
  mapear em conceitos de span OTel mais tarde (documentado em
  [`docs/metricas_reference.md §5`](metricas_reference.md)).

---

## 2. Checklist — o que foi pedido x o que foi entregue

| Pedido no PDF | Como foi atendido |
|---|---|
| Registrar chamadas e resultados das tools de forma estruturada. | `ExecutionCollector.tool()`/`.registrar(tipo="tool", ...)` em `src/metrics/coletor.py`, chamado em `src/pipeline.py` para cada execução de `validar_completude` (Fase 1, por revisor) e `auditar_decisao_final` (Fase 3), com `duracao_ms`, `score_completude`/`completo` ou `requer_revisao_humana` em `detalhes`. |
| Mostrar quais verificações determinísticas foram feitas em cada execução. | Cada chamada a `validar_com_tentativas()` é traduzida por `_registrar_validacao()` (`src/pipeline.py`) em eventos `tipo="validacao"`/`"retry"`/`"falha"`; o resumo final lista `quantidade_validacoes` e `quantidade_tools_chamadas` por nome — dá pra ver o que rodou sem abrir `pipeline.log`. |
| Gerar um resumo auditável da execução a partir dos eventos registrados. | `gerar_resumo()` em `src/metrics/resumo.py`, chamado em `run_demo()` (`src/pipeline.py`) logo após `pipeline.run()` retornar, produzindo um `ResumoExecucao`. |
| Calcular métricas simples (duração total, duração por fase, qtd validações, qtd retries, qtd falhas, qtd tools chamadas, alertas/revisão humana, decisão final, status final). | Todos são campos de `ResumoExecucao` (`src/metrics/resumo.py`): `duracao_total_ms`, `duracao_por_fase_ms`, `quantidade_validacoes`, `quantidade_retries`, `quantidade_falhas`, `quantidade_tools_chamadas`, `requer_revisao_humana`/`alertas`, `decisao_final`, `status_final`. Números reais de uma rodada em §6. |
| Preparar campos para tokens, custo, modelo usado, chamadas LLM (mesmo que não preenchidos ainda). | `ResumoExecucao.tokens_totais`, `.custo_estimado`, `.modelo_usado`, `.chamadas_llm` existem como campos `None` explícitos — ver caminho de preenchimento futuro em [`docs/metricas_reference.md §5`](metricas_reference.md). |
| Separar claramente tool determinística obrigatória do pipeline x o que poderia virar skill reutilizável em outro domínio. | Não é código — é uma distinção de design. Ver parágrafo dedicado logo abaixo desta tabela. |
| Criar forma simples de visualizar/imprimir o resumo (JSON, tabela, relatório). | `src/metrics/exportar.py`: `imprimir_resumo()` (tabela no terminal) e `salvar_resumo_json()` (arquivo JSON). Ambos chamados automaticamente ao fim de `run_demo()`. |
| Pensar nas métricas como evoluível para OpenTelemetry sem parsing manual. | `ExecutionEvent`/`ResumoExecucao` são dataclasses tipadas do início ao fim — nunca viram string antes de virar JSON. Mapeamento campo-a-campo para conceitos OTel documentado em [`docs/metricas_reference.md §5`](metricas_reference.md). |

### Tool obrigatória do pipeline x skill reutilizável

**Obrigatório e específico do peer review** é tudo que vive dentro de
`src/pipeline.py`: o bloco de import guardado de `ExecutionCollector`/
`gerar_resumo`/`imprimir_resumo`/`salvar_resumo_json`, as chamadas
`coletor.fase(self.name)` dentro de cada uma das 4 fases, `_registrar_validacao()`
(que sabe especificamente que existe um `ResultadoValidacao` do Grupo 1 e como
traduzi-lo) e as chamadas pontuais `coletor.registrar(tipo="tool", nome="validar_completude", ...)`
/ `nome="auditar_decisao_final"`. Essas peças conhecem nomes concretos de
fases e de tools do peer review — não fazem sentido fora desse domínio.

**Genérico o suficiente para virar skill reutilizável** é tudo dentro de
`src/metrics/` (`eventos.py`, `coletor.py`, `resumo.py`, `exportar.py`).
Nenhum desses quatro arquivos importa `review_schema.py`, `reviewer_agent.py`
ou qualquer coisa específica de peer review — `ExecutionEvent` só conhece
`fase`/`tipo`/`nome`/`status`/`duração`/`detalhes` genéricos, `ExecutionCollector`
só acumula e mede tempo, `gerar_resumo()` só soma/conta por tipo de evento.
Qualquer outro pipeline construído sobre `src/pipeline_base.py` poderia
importar `src/metrics/` inteiro sem mudar uma linha, desde que escrevesse sua
própria tradução (equivalente ao que `pipeline.py` faz hoje).

Sendo honesto sobre o limite disso: **não existe uma interface formal de
"skill" plugável** (não há classe base, não há empacotamento separado). Na
prática, reaproveitar hoje significa "copiar a pasta `src/metrics/` para o
outro projeto e escrever a tradução equivalente a `_registrar_validacao()`",
não "importar de um catálogo de skills instalável". Não fomos além disso.

---

## 3. Checklist — critérios de sucesso

| Critério de sucesso | Como está satisfeito |
|---|---|
| Deve ser possível olhar para uma execução e entender quais verificações determinísticas foram feitas. | `imprimir_resumo()` imprime, no terminal, as seções "Validações/Retries/Falhas" e "Tools chamadas" ao fim de cada `python main.py mock\|api`; o mesmo dado está em `resumo_execucao.json`. |
| Deve existir algum resumo local da execução. | `src/outputs/resumo_execucao.json`, escrito por `salvar_resumo_json()` a cada rodada de `run_demo()` — junto com a cópia embutida em `final_report.json["resumo_execucao"]`. |
| As métricas não devem depender de parsing manual de texto solto. | A cadeia inteira é objetos Python tipados (`ExecutionEvent` → `ResumoExecucao`) até a serialização final via `to_dict()`; em nenhum ponto se lê `pipeline.log` nem se aplica regex sobre string para calcular algo. |
| O README deve explicar como gerar o resumo e quais métricas estão sendo calculadas. | [`README.md §7 — Métricas de Execução e Resumo Auditável (Grupo 2)`](../README.md#7-métricas-de-execução-e-resumo-auditável-grupo-2): tabela dos arquivos, como a integração funciona, comandos, exemplo de saída. |
| A solução deve deixar claro como esses dados poderiam evoluir para métricas ADK/OpenTelemetry no futuro. | [`docs/metricas_reference.md §5 — Caminho de evolução para ADK/OpenTelemetry`](metricas_reference.md): tabela mapeando cada tipo de evento/campo para o conceito OTel equivalente, mais os campos placeholder já reservados em `ResumoExecucao`. |

---

## 4. Arquitetura — como os dados fluem

```
src/pipeline.py — dentro do run() de cada Phase (Fase 1..4)
  │
  ├─ with coletor.fase(self.name): ─────────────► mede início/fim da fase
  │                                                (time.perf_counter, automático)
  │
  ├─ validar_com_tentativas(...) roda (Grupo 1)
  │     └─ devolve ResultadoValidacao
  │           └─ _registrar_validacao(coletor, resultado=...)
  │                 ├─ tipo="validacao"   (sempre, 1 por chamada)
  │                 ├─ tipo="retry"       (1 por tentativa além da 1ª)
  │                 └─ tipo="falha"       (se esgotou as tentativas)
  │
  ├─ _tool_completude(parecer) roda  ── Fase 1, por revisor ──┐
  ├─ _tool_auditoria(veredito) roda  ── Fase 3 ───────────────┤─► coletor.registrar(
  │                                                            │      tipo="tool", nome=..., duracao_ms=...)
  │                                                           ─┘
  └─ Fase 4: coletor.registrar(tipo="decisao_final",
             detalhes={"decisao": verdict.decisao, "requer_revisao_humana": ...})

                                │
                                ▼
                 ExecutionCollector.eventos   (lista de ExecutionEvent, em memória)
                                │
                                ▼
        gerar_resumo(coletor.eventos, run_id=coletor.run_id)   [src/metrics/resumo.py]
                                │
                                ▼
                         ResumoExecucao
                          ┌──────┴──────┐
                          ▼             ▼
        final_report.json[               resumo_execucao.json
         "resumo_execucao"]              (arquivo próprio, salvar_resumo_json())
                          │
                          ▼
               imprimir_resumo()  →  tabela impressa no terminal
```

---

## 5. Decisões de design

- **`context.config`, não um novo atributo em `PipelineContext`.** Alterar
  `pipeline_base.py` está fora do escopo desta entrega (é a camada de
  orquestração genérica, compartilhada por todos os grupos). `config` já era
  usado como "estado interno de execução" por outras partes do sistema
  (`_mock_cache`, `_auditoria_veredito`) — `_metrics_collector` só segue essa
  convenção já estabelecida em vez de inventar um mecanismo novo.
- **Medir a fase por dentro do `run()` de cada fase, não com um hook
  central.** `pipeline_base.py` não tem `on_phase_start`/`on_phase_end` hoje,
  e criar um exigiria mexer em código compartilhado fora do escopo do Grupo
  2. Envolver o corpo de cada `run()` com `with coletor.fase(self.name):`
  entrega a mesma instrumentação sem tocar em `Pipeline.run()`.
- **Importação guardada em tudo.** Mesmo padrão defensivo já usado pelas
  tools (`_tool_completude`, `_tool_auditoria`): se `src/metrics/` não
  existir ou falhar ao importar por qualquer razão, `ExecutionCollector`
  vira `None` e `_fase_medida()`/`_registrar_validacao()` viram no-ops — o
  pipeline continua rodando exatamente como antes, só sem instrumentação.
- **`duracao_total_ms` como soma das durações por fase.** O pipeline atual
  roda as 4 fases estritamente em sequência (o laço em `Pipeline.run()` é
  síncrono), então somar a duração medida de cada fase é equivalente ao
  tempo de parede total. Isso é uma limitação **documentada**: se o pipeline
  passar a paralelizar fases no futuro, essa soma deixa de ser correta e
  precisaria virar `timestamp` de início da 1ª fase até fim da última.
- **Campos de tokens/custo/modelo/chamadas LLM como placeholder.** O PDF
  pediu para "preparar os campos, mesmo que nem tudo seja preenchido agora".
  Hoje nenhum dos módulos que chamam o Gemini (`reviewer_agent.py`,
  `cross_review.py`, `editor_agent.py`) expõe uso de tokens ou custo de volta
  para as fases de forma que `pipeline.py` pudesse capturar — instrumentar
  isso exigiria mexer nesses módulos, fora do escopo desta entrega. Os campos
  ficam reservados (`None`) até que essa informação exista em algum lugar.

---

## 6. Resultado real de uma execução

Rodada real (`.venv/bin/python main.py mock`), conteúdo integral de
`src/outputs/resumo_execucao.json`:

```json
{
  "run_id": "8368ec88-590e-4632-940b-f7a9d0ce6168",
  "duracao_total_ms": 3.414211008930579,
  "duracao_por_fase_ms": {
    "fase_1_revisao_independente": 1.6100610082503408,
    "fase_2_leitura_cruzada": 0.8304000075440854,
    "fase_3_editor_chefe": 0.7619389798492193,
    "fase_4_relatorio_final": 0.2118110132869333
  },
  "quantidade_validacoes": 7,
  "quantidade_retries": 0,
  "quantidade_falhas": 0,
  "quantidade_tools_chamadas": {
    "validar_completude": 3,
    "auditar_decisao_final": 1
  },
  "requer_revisao_humana": false,
  "decisao_final": 3,
  "status_final": "sucesso",
  "alertas": [],
  "tokens_totais": null,
  "custo_estimado": null,
  "modelo_usado": null,
  "chamadas_llm": null
}
```

Leitura dos números: **7 validações** = 3 pareceres validados na Fase 1 (um
por revisor: `statistician`, `domain_expert`, `copyeditor`) + 3 pareceres
revisados validados na Fase 2 + 1 veredito do editor validado na Fase 3.
**`validar_completude: 3x`** porque a Fase 1 audita a completude de cada um
dos 3 pareceres; **`auditar_decisao_final: 1x`** porque essa tool roda uma
única vez, sobre o veredito consolidado da Fase 3. `quantidade_retries` e
`quantidade_falhas` em `0` porque, em modo mock, todos os payloads pré-salvos
já são válidos contra os schemas de primeira tentativa — não houve
necessidade de correção. `requer_revisao_humana: false` e `alertas: []`
porque `auditar_decisao_final` não encontrou divergência alta entre notas nem
inconsistências semânticas nesta rodada (ver `auditoria_veredito` em
`final_report.json`). `status_final: "sucesso"` segue diretamente disso — sem
falha e sem alerta, é a categoria mais alta.

---

## 7. Como rodar / verificar

```bash
# Testes das métricas (offline, sem API key)
.venv/bin/pytest src/metrics/tests/ -v

# Pipeline completo em modo mock — imprime o resumo no terminal ao final
.venv/bin/python main.py mock
```

Onde olhar os resultados depois de rodar:

- **Terminal:** tabela impressa por `imprimir_resumo()` ao fim da execução.
- **`src/outputs/resumo_execucao.json`:** o `ResumoExecucao` sozinho, em JSON.
- **`src/outputs/final_report.json`:** o mesmo resumo embutido na chave
  `"resumo_execucao"`, junto com o restante do relatório do peer review.

---

## 8. Fora de escopo desta atividade

- **Hierarquia de span/`parent_span_id` e ponte com o `Runner` do ADK.**
  Território explícito do Grupo 3. Esse trabalho já existe em branches
  remotas (`feature/grupo1-integracao-observabilidade`,
  `observabilidade-testes`), mas **não está mesclado em `main`** — por isso
  `src/metrics/` foi construído de forma autocontida, sem depender dele.
- **Integração de `checar_coerencia` diretamente na Fase 2.** Pendência
  antiga, já documentada em `docs/apresentacao_grupo2.md` desde a entrega
  anterior das tools — não fazia parte do pedido desta atividade
  (métricas/resumo) e continua pendente.
- **Dashboard ou visualização gráfica.** O pedido era por algo "simples":
  JSON + tabela no terminal, como entregue — nenhuma UI foi construída.
- **Export real para OpenTelemetry.** Só o caminho de mapeamento está
  documentado (`docs/metricas_reference.md §5`); nenhum exportador OTel foi
  implementado.
- **Cálculo de duração para fases paralelas.** `duracao_total_ms` assume
  execução sequencial (ver §5) — não há suporte a somar/reconciliar
  intervalos sobrepostos.
- **Preenchimento de tokens/custo/modelo/chamadas LLM.** Campos existem em
  `ResumoExecucao` mas seguem `None`, porque os módulos de agente não expõem
  esses dados hoje (ver §5).

