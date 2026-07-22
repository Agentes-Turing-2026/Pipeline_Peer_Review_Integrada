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
| `"fase"` | Início/fim de uma das 4 fases do pipeline. | `duracao_s` do evento vira a duração daquela fase em `duracao_por_fase_s[fase]`. |
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
| `duracao_s` | `float \| None` | Não (default `None`) | Duração medida do que o evento representa, em **segundos** (float, precisão total). |
| `detalhes` | `dict[str, Any]` | Não (default `{}`) | Espaço livre para dados específicos do evento (ver convenções em §1). |

Métodos: `to_dict()` serializa as 8 chaves acima num dict JSON-pronto;
`from_dict(data)` reconstrói um `ExecutionEvent` a partir desse dict (ver
§2.1 "Compatibilidade de leitura" para o comportamento com payloads
legados em milissegundos).

### 2.1. Compatibilidade de leitura

`ExecutionEvent.from_dict` aceita payloads legados que ainda gravam
`duracao_ms` (do contrato anterior, em milissegundos) e converte para
`duracao_s` dividindo por `1000.0`. Pontos importantes:

- A conversão é de **mão única** — leitura apenas. `to_dict()` nunca volta a
  emitir `duracao_ms`.
- Se a chave `duracao_s` está presente no payload (mesmo com valor `None`),
  ela **vence** e `duracao_ms` é ignorado — não há soma das duas.
- Chaves desconhecidas no payload (fora dos campos do dataclass) ainda
  levantam `TypeError` — não há filtro de campos extras.

---

## 3. Schema de `ResumoExecucao` (`src/metrics/resumo.py`)

Snapshot agregado de uma lista de `ExecutionEvent`, produzido por
`gerar_resumo(eventos, *, run_id=None)`.

| Campo | Tipo | Descrição |
|---|---|---|
| `run_id` | `str` | Herdado de `eventos[0].run_id` (ou do parâmetro `run_id`, se informado). |
| `duracao_total_s` | `float \| None` | Tempo de **parede** da execução inteira (início ao fim), tipicamente `ExecutionCollector.duracao_execucao_s` passado por quem chama `gerar_resumo()`. Cobre tudo, inclusive o que nenhuma fase mede. Se `gerar_resumo()` for chamado sem esse parâmetro, cai para `duracao_soma_fases_s` como aproximação e um alerta de aproximação é adicionado a `alertas`. |
| `duracao_soma_fases_s` | `float \| None` | SOMA das durações dos eventos `tipo="fase"` — o valor antigo. `None` se nenhuma fase com duração foi registrada. Sempre calculado a partir dos eventos, independente de `duracao_total_s`. |
| `duracao_por_fase_s` | `dict[str, float]` | Duração de cada fase, por nome. |
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

Método `to_dict()` serializa as 16 chaves acima num dict JSON-pronto (é o que
vira `final_report.json["resumo_execucao"]` e `resumo_execucao.json`).

`gerar_resumo([])` **levanta `ValueError`** se `run_id` não for informado
explicitamente — não há como inferir o `run_id` de uma lista vazia.

### 3.1. `duracao_total_s` vs. `duracao_soma_fases_s`

Nenhum dos dois substitui o outro — os dois convivem de propósito:

- **`duracao_total_s`** é o tempo de parede real da execução, do início ao
  fim. É o número que deveria bater com o span `<run>` do trace do Grupo 3.
- **`duracao_soma_fases_s`** é a soma das durações que cada fase mediu de si
  mesma. Ignora tudo que acontece fora das fases (ex.: extração de PDF,
  montagem do relatório, gravação em disco).

A **diferença entre os dois** é o tempo não atribuído a nenhuma fase — isso é
**informação de auditoria útil, não um erro ou inconsistência**. Se
`duracao_total_s` vier menor que `duracao_soma_fases_s`, aí sim seria sinal
de bug (fases não podem, juntas, durar mais que a execução inteira).

Quando `gerar_resumo()` é chamado **sem** passar `duracao_total_s`
explicitamente, o valor cai para `duracao_soma_fases_s` como aproximação, e
um alerta é adicionado a `resumo.alertas` registrando que a duração total é
aproximada (não é tempo de parede real).

---

## 4. Exemplo real de `resumo_execucao.json` (modo mock)

Gerado por `.venv/bin/python main.py mock` nesta rodada — números reais, não
inventados:

```json
{
  "run_id": "a8ffbc92-984f-4fba-9174-0add31fe98ed",
  "duracao_total_s": 0.0064597539603710175,
  "duracao_soma_fases_s": 0.004867468029260635,
  "duracao_por_fase_s": {
    "fase_1_revisao_independente": 0.0024566210340708494,
    "fase_2_leitura_cruzada": 0.00139256299007684,
    "fase_3_editor_chefe": 0.0007801270112395287,
    "fase_4_relatorio_final": 0.00023815699387341738
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

**Precisão total no JSON, arredondamento só na exibição.** Repare que os
valores de duração acima têm todas as casas decimais que o `float` do Python
carrega — `salvar_resumo_json()` nunca arredonda. O arredondamento só existe
em `imprimir_resumo()` (tabela do terminal), via `_fmt_s`:

| Valor | Exibição no terminal |
|---|---|
| `None` | `"n/d"` |
| `0.0` exato | `"0 s"` |
| `0 < valor < 0.01` | `"< 0.01 s"` (evita mostrar `"0.00 s"` para durações reais, mas muito pequenas) |
| `valor >= 0.01` | `f"{valor:.2f} s"` (duas casas) |

---

## 5. Caminho de evolução para ADK/OpenTelemetry

Os nomes de campo de `ExecutionEvent`/`ResumoExecucao` foram escolhidos para
mapear diretamente em conceitos de **span**/**trace** OpenTelemetry, mesmo sem
implementar isso agora:

| Conceito hoje (`src/metrics/`) | Conceito OTel/ADK equivalente |
|---|---|
| `ExecutionEvent.run_id` | `trace_id` (identifica toda a execução). |
| Evento `tipo="fase"` | Um **span** de nome `nome` (ex. `fase_1_revisao_independente`), com `start_time`/`end_time` derivados de `timestamp`/`duracao_s`. |
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
