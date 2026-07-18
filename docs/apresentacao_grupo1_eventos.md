# Grupo 1 — Logs Estruturados de Validação, Retry e Confiabilidade

## 1. Contexto da atividade

Esta entrega pediu ao Grupo 1 para transformar a camada de **validação e
retry** — que já existia e funcionava — em **eventos estruturados**, capazes de
responder, para cada saída de agente que passa pelo pipeline: **passou, falhou
ou foi corrigida — e por quê?** O requisito central era que esses rastros
fossem claros, auditáveis, ligados ao identificador comum da execução e
compatíveis com a observabilidade do restante do pipeline (trace do Grupo 3,
métricas do Grupo 2), sem que erro importante ficasse escondido em texto
livre, print ou console.

Isso se encaixa entre os outros dois grupos sem invadir o território deles:

- **Grupo 2 (métricas e resumo auditável)** consome o `ResultadoValidacao`
  devolvido por `validar_com_tentativas()` e o traduz em eventos de métricas
  (`_registrar_validacao()` em `src/pipeline.py`). O Grupo 1 não calcula
  métricas — só garante que a informação de confiabilidade exista de forma
  estruturada e correlacionável.
- **Grupo 3 (base de observabilidade/traces)** é dono do ciclo de vida da
  execução (`run_id`, spans, trace JSONL). O Grupo 1 **herda** esse `run_id`
  e **espelha** cada evento de validação no `emit_event()` deles — de forma
  guardada: sem o pacote `observability`, nada quebra.

## 2. Checklist — o que foi pedido × o que foi entregue

| Pedido no PDF | Como foi atendido |
|---|---|
| Registrar cada tentativa de validação como evento estruturado. | `validar_com_tentativas()` ([`src/validacao_retry.py`](../src/validacao_retry.py)) emite um `EventoValidacao` ([`src/eventos_validacao.py`](../src/eventos_validacao.py)) em TODA tentativa — sucesso, falha, correção ou bloqueio — gravado como uma linha JSON em `src/logs/validacao_events.jsonl` (uma linha = um evento = um JSON válido). |
| Registrar quando uma validação passou de primeira. | Categoria `passou_de_primeira` (tentativa 1 com sucesso). |
| Registrar quando falhou e qual foi o motivo. | Categoria `falhou_recuperavel`, com o campo `erro` carregando a mensagem do `ValidationError` do Pydantic. |
| Registrar quando houve retry. | Cada evento carrega `tentativa`/`max_tentativas`; a sequência `falhou_recuperavel` → `corrigido` → `passou_apos_correcao` (ou novo `falhou_recuperavel`) reconstrói o retry passo a passo. |
| Registrar quando uma correção foi aplicada. | Categoria `corrigido`, com o campo `correcao_aplicada`: um **diff por campo** (`{"campo": {"antes": ..., "depois": ...}}`) calculado por `diff_correcao()` — mostra exatamente o que o corrector (mock ou API) alterou. |
| Registrar quando todas as tentativas falharam e a saída foi bloqueada. | Categoria `bloqueado` + `requer_revisao_humana=True`, e `PipelineValidationError` é levantada — o dado inválido **não** segue para a próxima fase. |
| Diferenciar erro recuperável, corrigido, bloqueante e caso de revisão humana. | As 5 categorias (`passou_de_primeira`, `passou_apos_correcao`, `falhou_recuperavel`, `corrigido`, `bloqueado`) + a flag `requer_revisao_humana`. Categoria inválida é rejeitada na criação do evento (`__post_init__`). |
| Criar exemplos de execução com sucesso, com retry e com falha final. | [`src/demo_eventos.py`](../src/demo_eventos.py) (offline, sem API key): cenário 1 = sucesso de primeira; cenário 2 = falha → correção → sucesso; cenário 3 = esgotamento → bloqueio; cenário 4 = cross-review e veredito na mesma execução. |
| Erro importante não pode ficar só em texto livre/print/console. | Todo evento vai para o JSONL estruturado; o bloqueio vira exceção tipada (`PipelineValidationError` carrega o `ResultadoValidacao` completo); o log de texto continua existindo, mas é redundância, não fonte única. |
| Eventos conectáveis ao trace e usáveis para métricas. | `run_id` **herdado da execução** (ver §3) + espelhamento de cada evento em `observability.emit_event()` — os eventos do Grupo 1 aparecem na MESMA linha do tempo reconstruída por `src/demo_observabilidade.py`. As métricas do Grupo 2 consomem o `ResultadoValidacao` da mesma chamada. |

**Critérios de sucesso, verificados:**

- *"Abrir o arquivo de eventos e entender o histórico"* — `ler_eventos(run_id=...)`
  + `formatar_linha_do_tempo()` reconstroem a narrativa (saída real em §5).
- *"O pipeline não deve aceitar erro silenciosamente"* — esgotou tentativas ⇒
  `PipelineValidationError`; falha do corrector ⇒ evento `bloqueado` + exceção.
- *"README deve explicar como rodar e interpretar"* — README §4, incluindo a
  tabela de categorias e o porquê do arquivo próprio.
- *"Eventos ligados ao identificador da execução"* — mesma `run_id` no
  relatório, no trace e no `validacao_events.jsonl` (verificação real em §5).
- Testes offline: [`src/tests/test_eventos_validacao.py`](../src/tests/test_eventos_validacao.py)
  (12 testes: categorias, diff, JSONL, filtro por run_id, linha do tempo e as
  três trajetórias de validação).

## 3. Decisões de projeto (e os porquês)

- **Arquivo próprio (`validacao_events.jsonl`) em vez de escrever no
  `pipeline.log`.** O `pipeline.log` já é usado, em texto, pela orquestração e
  pelas tools do Grupo 2. Um arquivo dedicado em JSON Lines evita disputa e
  permite consulta com `jq`/`grep`/pandas sem parsing heurístico.
- **`run_id` herdado, não inventado.** `Pipeline.run()` (em
  `src/pipeline_base.py`) sincroniza `context.run_id` com o `tracer.run_id`
  do Grupo 3 antes de qualquer fase; as fases repassam `run_id`/`fase` a
  `validar_com_tentativas()`. O `uuid4` de `gerar_run_id()` é só fallback
  para uso isolado (demos, testes, pipeline sem `observability`).
- **Espelhamento best-effort no trace.** `emitir_evento()` grava o JSONL
  primeiro (fonte de verdade; falha de escrita SOBE) e depois repassa ao
  `emit_event()` do Grupo 3 dentro de try/except — a observabilidade nunca
  derruba a validação.
- **Schema simples de propósito.** `EventoValidacao` é um dataclass → dict
  plano, sem dependência de biblioteca de observabilidade — margem para
  plugar um exportador OTel/Langfuse no futuro sem redesenhar nada.

## 4. O que NÃO foi feito (por escopo)

- **Não** capturamos eventos internos do ADK (`Runner`, `invocation_id`,
  tool calls) — essa base é do Grupo 3 (`src/observability/adk_bridge.py`).
- **Não** calculamos métricas gerais da execução (duração, contagens,
  resumo) — isso é do Grupo 2 (`src/metrics/`), que consome nosso
  `ResultadoValidacao`.
- **Não** unificamos o `run_id` do resumo de métricas: o `ExecutionCollector`
  do Grupo 2 ainda gera identificador próprio, e o ajuste foi atribuído pelo
  líder aos **Grupos 2 e 3** — está em andamento no **PR #3**
  (`grupo-2-3-unifica-run-id`), que alinha o coletor ao mesmo `run_id` de
  relatório/trace/validação.

## 5. Como rodar e saída real

```bash
# Demo dedicada dos eventos (offline, sem API key, sem internet)
python src/demo_eventos.py

# Pipeline completo em modo mock — artefatos organizados por run_id
python main.py mock
```

Após `python main.py mock` (rodada real de 18/07, suíte com **110 testes
passando**), os artefatos da execução `run_1e909f3046164e83` ficaram assim —
**mesmo `run_id`** no relatório, no trace e nos eventos de validação:

```
src/outputs/run_1e909f3046164e83/final_report.md
src/outputs/run_1e909f3046164e83/final_report.json   <- "run_id": "run_1e909f3046164e83"
src/logs/traces/run_1e909f3046164e83.jsonl           <- trace do Grupo 3
src/logs/validacao_events.jsonl                      <- eventos abaixo
```

Uma linha real do `validacao_events.jsonl` dessa execução:

```json
{"run_id": "run_1e909f3046164e83", "agente": "editor", "schema": "validar_editor_verdict",
 "tentativa": 1, "max_tentativas": 3, "categoria": "passou_de_primeira", "status": "sucesso",
 "erro": null, "correcao_aplicada": null, "requer_revisao_humana": false,
 "fase": "fase_3_editor_chefe", "timestamp": "2026-07-18T19:27:19.960695+00:00"}
```

E a linha do tempo que `demo_eventos.py` reconstrói lendo o arquivo de volta
(trecho real — note o diff da correção e o alerta de revisão humana):

```
[...] fase=demo_caso_2_retry agente=domain_expert schema=validar_review tentativa=1/3 -> FALHOU (recuperável, retry a seguir)
    erro: 4 validation errors for ReviewSchema
[...] fase=demo_caso_2_retry agente=domain_expert schema=validar_review tentativa=1/3 -> CORRIGIDO (corrector aplicado)
    corrigido: originalidade.nota: 5 -> 4
    corrigido: confianca.nota: 0 -> 1
    corrigido: confianca.justificativa: None -> '[corrigido automaticamente — revisar]'
[...] fase=demo_caso_2_retry agente=domain_expert schema=validar_review tentativa=2/3 -> PASSOU (após correção)
[...] fase=demo_caso_3_bloqueio agente=copyeditor schema=validar_review tentativa=3/3 -> BLOQUEADO (esgotou tentativas)
    erro: 6 validation errors for ReviewSchema
    ATENÇÃO: candidato a revisão humana.
```

Como ler: cada linha responde a pergunta da task — o dado **passou**
(`passou_de_primeira`/`passou_apos_correcao`), **falhou** (`falhou_recuperavel`,
com o motivo), **foi corrigido** (`corrigido`, com o diff exato) ou **foi
bloqueado** (`bloqueado`, com pedido de revisão humana) — sempre amarrado ao
`run_id` da execução.
