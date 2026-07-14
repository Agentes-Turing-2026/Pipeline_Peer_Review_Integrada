# Métricas de execução — referência (Grupo 2)

`src/metrics/` transforma o que acontece durante uma execução do pipeline
(fases, tools, validações, retries, falhas, decisão final) em **eventos
estruturados** (`ExecutionEvent`) e agrega esses eventos num **resumo
auditável** (`ResumoExecucao`) — sem depender de parsing de texto solto do
`pipeline.log`. Ver [`README.md §7`](../README.md#7-métricas-de-execução-e-resumo-auditável-grupo-2)
para como isso se integra ao pipeline.

Este documento é a referência de **schema** (campo a campo) e de **convenção
de agregação** — o contrato que quem *emite* eventos (hoje só `pipeline.py`)
precisa respeitar para que `gerar_resumo()` produza números corretos.

---

## 1. Tipos de evento e o que cada um exige em `detalhes`

`ExecutionEvent.tipo` é um `Literal` com seis valores. `gerar_resumo()`
interpreta cada um de um jeito específico:

| `tipo` | Quando ocorre | O que `gerar_resumo()` faz com ele |
|---|---|---|
| `"fase"` | Início/fim de uma das 4 fases do pipeline. | `duracao_ms` do evento vira a duração daquela fase em `duracao_por_fase_ms[fase]`. |
| `"tool"` | Uma chamada de tool determinística do Grupo 2 (`validar_completude`, `auditar_decisao_final`). | `nome` identifica a tool; cada evento incrementa `quantidade_tools_chamadas[nome]` em 1. |
| `"validacao"` | Uma verificação de schema (`validar_com_tentativas`) foi realizada — independente de ter passado de primeira ou não. | Incrementa `quantidade_validacoes`. |
| `"retry"` | Uma tentativa de retry ocorreu após falha recuperável. | Incrementa `quantidade_retries`. |
| `"falha"` | Todas as tentativas se esgotaram e a saída foi bloqueada (falha definitiva, não recuperável). | Incrementa `quantidade_falhas`; também marca `status_final = "falha"` se `evento.status == "falha"`. |
| `"decisao_final"` | A decisão editorial foi consolidada (Fase 4). | Lê `detalhes["decisao"]` e preenche `resumo.decisao_final`. |

Duas convenções cruzam **qualquer** tipo de evento, não só um:

- **`detalhes={"requer_revisao_humana": True}`** em qualquer evento liga
  `resumo.requer_revisao_humana = True` — usado hoje pelo evento `"tool"` de
  `auditar_decisao_final` e pelo evento `"decisao_final"` da Fase 4.
- **`status="aviso"`** em qualquer evento vira um item em `resumo.alertas`
  (lendo `detalhes["mensagem"]` para o texto, com um texto padrão gerado a
  partir de `tipo`/`nome` se a chave não existir).

> `status_final` é calculado por prioridade: **`"falha"`** (se qualquer
> evento tem `status="falha"`) **> `"sucesso_com_alertas"`** (se há algum
> `status="aviso"` mas nenhuma falha) **> `"sucesso"`** (caso contrário).

---

## 2. Schema de `ExecutionEvent` (`src/metrics/eventos.py`)

Dataclass puro, sem dependências externas.

| Campo | Tipo | Obrigatório? | Descrição |
|---|---|---|---|
| `run_id` | `str` | Sim | Identificador da execução à qual o evento pertence. |
| `fase` | `str` | Sim | Nome da fase do pipeline (ex.: `fase_1_revisao_independente`). |
| `tipo` | `TipoEvento` (`Literal["fase","tool","validacao","retry","falha","decisao_final"]`) | Sim | Natureza do evento — ver tabela §1. |
| `nome` | `str` | Sim | Rótulo do evento (nome da fase, da tool, do agente, etc., conforme o tipo). |
| `status` | `StatusEvento` (`Literal["sucesso","falha","aviso"]`) | Sim | Resultado do evento. |
| `timestamp` | `str` | Não (default: agora, ISO-8601 UTC) | Momento em que o evento foi criado. |
| `duracao_ms` | `float \| None` | Não (default `None`) | Duração medida do que o evento representa, em milissegundos. |
| `detalhes` | `dict[str, Any]` | Não (default `{}`) | Espaço livre para dados específicos do evento (ver convenções em §1). |

Métodos: `to_dict()` serializa as 8 chaves acima num dict JSON-pronto;
`from_dict(data)` reconstrói um `ExecutionEvent` a partir desse dict.

---

## 3. Schema de `ResumoExecucao` (`src/metrics/resumo.py`)

Snapshot agregado de uma lista de `ExecutionEvent`, produzido por
`gerar_resumo(eventos, *, run_id=None)`.

| Campo | Tipo | Descrição |
|---|---|---|
| `run_id` | `str` | Herdado de `eventos[0].run_id` (ou do parâmetro `run_id`, se informado). |
| `duracao_total_ms` | `float \| None` | Soma das durações dos eventos `tipo="fase"`. `None` se nenhuma fase foi registrada. |
| `duracao_por_fase_ms` | `dict[str, float]` | Duração de cada fase, por nome. |
| `quantidade_validacoes` | `int` | Total de eventos `tipo="validacao"`. |
| `quantidade_retries` | `int` | Total de eventos `tipo="retry"`. |
| `quantidade_falhas` | `int` | Total de eventos `tipo="falha"`. |
| `quantidade_tools_chamadas` | `dict[str, int]` | Contagem de chamadas por nome de tool. |
| `requer_revisao_humana` | `bool` | `True` se algum evento trouxe `detalhes["requer_revisao_humana"] = True`. |
| `decisao_final` | `Any \| None` | Extraído de `detalhes["decisao"]` do evento `tipo="decisao_final"`. |
| `status_final` | `str` | `"sucesso"` \| `"sucesso_com_alertas"` \| `"falha"` (ver regra de prioridade em §1). |
| `alertas` | `list[str]` | Uma entrada por evento com `status="aviso"`, no formato `"[fase] mensagem"`. |
| `tokens_totais` | `int \| None` | **Placeholder** — não preenchido hoje (ver §5). |
| `custo_estimado` | `float \| None` | **Placeholder** — não preenchido hoje (ver §5). |
| `modelo_usado` | `str \| None` | **Placeholder** — não preenchido hoje (ver §5). |
| `chamadas_llm` | `int \| None` | **Placeholder** — não preenchido hoje (ver §5). |

Método `to_dict()` serializa as 15 chaves acima num dict JSON-pronto (é o que
vira `final_report.json["resumo_execucao"]` e `resumo_execucao.json`).

`gerar_resumo([])` **levanta `ValueError`** se `run_id` não for informado
explicitamente — não há como inferir o `run_id` de uma lista vazia.

---

## 4. Exemplo real de `resumo_execucao.json` (modo mock)

Gerado por `.venv/bin/python main.py mock` nesta rodada — números reais, não
inventados:

```json
{
  "run_id": "f57527f7-3b5f-477b-84e6-9b517bc78fa4",
  "duracao_total_ms": 3.3010260085575283,
  "duracao_por_fase_ms": {
    "fase_1_revisao_independente": 1.4937100058887154,
    "fase_2_leitura_cruzada": 0.7933570013847202,
    "fase_3_editor_chefe": 0.7870710105635226,
    "fase_4_relatorio_final": 0.2268879907205701
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

`quantidade_validacoes == 7` porque a Fase 1 e a Fase 2 validam 3 pareceres
cada (um por revisor: `statistician`, `domain_expert`, `copyeditor`) e a Fase
3 valida 1 veredito do editor (3 + 3 + 1 = 7). `quantidade_tools_chamadas`
reflete `validar_completude` rodando uma vez por revisor na Fase 1 (3x) e
`auditar_decisao_final` rodando uma vez na Fase 3 (1x).

---

## 5. Caminho de evolução para ADK/OpenTelemetry

Os nomes de campo de `ExecutionEvent`/`ResumoExecucao` foram escolhidos para
mapear diretamente em conceitos de **span**/**trace** OpenTelemetry, mesmo sem
implementar isso agora:

| Conceito hoje (`src/metrics/`) | Conceito OTel/ADK equivalente |
|---|---|
| `ExecutionEvent.run_id` | `trace_id` (identifica toda a execução). |
| Evento `tipo="fase"` | Um **span** de nome `nome` (ex. `fase_1_revisao_independente`), com `start_time`/`end_time` derivados de `timestamp`/`duracao_ms`. |
| Evento `tipo="tool"` | Um **span filho** do span da fase corrente, `kind=INTERNAL`, com `nome` como nome do span e `detalhes` como `span.attributes` (ex.: `score_completude`, `completo`). |
| Evento `tipo="validacao"` / `"retry"` / `"falha"` | Eventos anexados (`span.add_event(...)`) ao span da fase, com `detalhes["erro"]`/`detalhes["tentativas_usadas"]` como atributos do evento. |
| Evento `tipo="decisao_final"` | Um **atributo do span raiz** (`decisao_final`, `requer_revisao_humana`) ou um evento terminal do trace. |
| `ExecutionEvent.status` | `span.status` (`OK`/`ERROR`) — `"aviso"` mapearia para `OK` com um atributo/evento adicional, já que OTel não tem um terceiro estado nativo. |
| `ResumoExecucao.tokens_totais`, `.custo_estimado`, `.modelo_usado`, `.chamadas_llm` | Atributos de span padronizados pela semântica **gen-ai** do OpenTelemetry (`gen_ai.usage.*`, `gen_ai.request.model`) quando os agentes (Fases 1-3, que já usam `google-adk`) expuserem essa informação — hoje o pipeline não a captura em lugar nenhum, por isso os campos ficam `None`. |

A camada atual (`ExecutionCollector` em memória + JSON no fim da execução) é
deliberadamente a implementação mais simples que respeita esse contrato de
campos — trocar o "storage" por um exporter OTel real não deveria exigir
mudar `ExecutionEvent`, `ResumoExecucao` nem a convenção de agregação
documentada em §1.
