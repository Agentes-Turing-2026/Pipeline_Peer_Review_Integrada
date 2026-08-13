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

`ExecutionEvent.tipo` é um `Literal` com oito valores. `gerar_resumo()`
interpreta cada um de um jeito específico:

| `tipo` | Quando ocorre | O que `gerar_resumo()` faz com ele |
|---|---|---|
| `"fase"` | Início/fim de uma das 4 fases do pipeline. | `duracao_s` do evento vira a duração daquela fase em `duracao_por_fase_s[fase]`. |
| `"tool"` | Uma chamada de tool determinística do Grupo 2 (`validar_completude`, `auditar_decisao_final`). | `nome` identifica a tool; cada evento incrementa `quantidade_tools_chamadas[nome]` em 1. |
| `"validacao"` | Uma verificação de schema (`validar_com_tentativas`) foi realizada — independente de ter passado de primeira ou não. | Incrementa `quantidade_validacoes`. |
| `"retry"` | Uma tentativa de retry ocorreu após falha recuperável. | Incrementa `quantidade_retries`. |
| `"falha"` | Todas as tentativas se esgotaram e a saída foi bloqueada (falha definitiva, não recuperável). | Incrementa `quantidade_falhas`; também marca `status_final = "falha"` se `evento.status == "falha"`. |
| `"decisao_final"` | A decisão editorial foi consolidada (Fase 4). | Lê `detalhes["decisao"]` e preenche `resumo.decisao_final`. |
| `"chamada_llm"` | Uma chamada LLM real, registrada por `metrics/adk_usage.py` a partir do `usage_metadata` dos `Event` do ADK (Fases 1-3). | Conta em `chamadas_llm` e agrega os tokens de `detalhes` em `tokens_execucao`, `tokens_totais`, `tokens_por_agente[agente]` e `tokens_por_fase[fase]`; `detalhes["modelo"]` alimenta `modelo_usado`. Ver §5. |
| `"fallback_llm"` | O modelo principal falhou por motivo TEMPORÁRIO e o pipeline trocou para a opção reserva (`llm_fallback.py`, `LLM_FALLBACK_*`). | `detalhes["evento"]` (`"acionado"`\|`"respondeu"`\|`"esgotado"`) incrementa o contador `quantidade_fallbacks_*` correspondente e vira uma linha em `fallbacks_llm`. Ver §6. |

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
| `tipo` | `TipoEvento` (`Literal["fase","tool","validacao","retry","falha","decisao_final","chamada_llm"]`) | Sim | Natureza do evento — ver tabela §1. |
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
| `tokens_totais` | `int \| None` | Soma de `tokens_total` das chamadas LLM onde o ADK informou consumo. `None` = nenhuma medição disponível (ex.: modo mock) — **nunca 0 no lugar de "não medido"** (ver §5). |
| `custo_estimado` | `float \| None` | **Estimativa**, nunca valor faturado: soma do custo de CADA chamada LLM, cada uma precificada pelo modelo que respondeu nela (`detalhes.modelo`). `None` quando nenhum preço foi resolvido — nunca `0`. Consumo real (tokens) e preço (configuração) nunca se misturam (ver §5.3). |
| `modelo_usado` | `str \| None` | Modelo(s) informado(s) pelo ADK (`model_version`). Um modelo → o nome dele; vários → nomes únicos ordenados, separados por `", "`. |
| `chamadas_llm` | `int \| None` | Total de eventos `tipo="chamada_llm"`. `None` quando não houve nenhum. |
| `tokens_execucao` | `dict[str, int \| None] \| None` | Totais da execução por categoria: `tokens_entrada`, `tokens_resposta`, `tokens_pensamento`, `tokens_cache`, `tokens_total`. Valor `None` numa categoria = nenhuma chamada informou aquela categoria. `None` no campo inteiro = nenhuma chamada LLM. |
| `tokens_por_agente` | `dict[str, int \| None]` | `tokens_total` somado por agente (`author` do ADK). Valor `None` = o agente fez chamadas, mas o consumo está indisponível. |
| `tokens_por_fase` | `dict[str, int \| None]` | `tokens_total` somado por fase. Mesma semântica de `None`. |
| `quantidade_fallbacks_acionados` | `int` | Total de eventos `fallback_llm` com `detalhes["evento"] == "acionado"` (o principal falhou por motivo temporário e a reserva entrou). `0` quando nenhuma reserva foi configurada ou nenhuma falha temporária ocorreu. |
| `quantidade_fallbacks_respondidos` | `int` | Total de trocas em que a reserva respondeu com sucesso (`"respondeu"`). |
| `quantidade_fallbacks_esgotados` | `int` | Total de trocas em que principal E reserva falharam (`"esgotado"`) — cada uma também marca `status_final = "falha"` (o evento tem `status="falha"`, mesma regra genérica de qualquer tipo). |
| `fallbacks_llm` | `list[dict]` | Uma entrada por evento `fallback_llm`, na ordem em que ocorreram: `fase`, `papel`, `evento`, `status`, `timestamp`, `provedor_inicial`, `motivo_falha` (só em `"acionado"`), `opcao_fallback` e `modelo_que_respondeu` (só em `"respondeu"`). Ver §6. |

Método `to_dict()` serializa as 23 chaves acima num dict JSON-pronto (é o que
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

Gerado por `python main.py mock` nesta rodada — números reais, não
inventados:

```json
{
  "run_id": "run_4d394bd382a4495e",
  "duracao_total_s": 0.011900800003786571,
  "duracao_soma_fases_s": 0.009830800001509488,
  "duracao_por_fase_s": {
    "fase_1_revisao_independente": 0.004149500004132278,
    "fase_2_leitura_cruzada": 0.003696300002047792,
    "fase_3_editor_chefe": 0.0017325999942841008,
    "fase_4_relatorio_final": 0.00025240000104531646
  },
  "quantidade_validacoes": 7,
  "quantidade_retries": 0,
  "quantidade_falhas": 0,
  "quantidade_tools_chamadas": {
    "validar_completude": 3,
    "auditar_decisao_final": 1,
    "checar_coerencia": 1
  },
  "requer_revisao_humana": false,
  "decisao_final": 3,
  "status_final": "sucesso",
  "alertas": [],
  "tokens_totais": null,
  "custo_estimado": null,
  "modelo_usado": null,
  "chamadas_llm": null,
  "tokens_execucao": null,
  "tokens_por_agente": {},
  "tokens_por_fase": {}
}
```

`quantidade_validacoes == 7` porque a Fase 1 e a Fase 2 validam 3 pareceres
cada (um por revisor: `statistician`, `domain_expert`, `copyeditor`) e a Fase
3 valida 1 veredito do editor (3 + 3 + 1 = 7). `quantidade_tools_chamadas`
reflete `validar_completude` rodando uma vez por revisor na Fase 1 (3x) e
`auditar_decisao_final` + `checar_coerencia` rodando na Fase 3 (1x cada).
Os campos de tokens estão todos `null`/vazios porque o modo mock **não faz
chamada LLM nenhuma** — indisponível é `null`, nunca `0` (ver §5).

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

## 5. Tokens reais — `usage_metadata` do ADK (`src/metrics/adk_usage.py`)

Os tokens vêm **diretamente dos `Event` do Runner do ADK** (campo
`usage_metadata`, contrato do google-genai) — nunca de estimativa por tamanho
de texto. Versão validada nos testes: `google-adk==1.27.5` (requirements.txt).

### 5.1. Captura e ponto de conexão

Os três loops `async for event in runner.run_async(...)` dos agentes
(`reviewer_agent.py`, `cross_review.py`, `editor_agent.py`) chamam
`registrar_usage_adk(event, fase=...)` ao lado do `trace_adk_event` do
Grupo 3 — mesma fonte de eventos, nenhuma segunda história da execução. O
consumidor é **no-op** quando nenhum coletor foi registrado (demos avulsas
continuam funcionando); o pipeline registra o coletor via
`definir_coletor_adk(coletor)` ao criar o `ExecutionCollector`.

Cada chamada LLM vira um `ExecutionEvent` `tipo="chamada_llm"` com
`detalhes`: `tokens_entrada` (`prompt_token_count`), `tokens_resposta`
(`candidates_token_count`), `tokens_pensamento` (`thoughts_token_count`),
`tokens_cache` (`cached_content_token_count`), `tokens_total`
(`total_token_count`), `modelo` (`model_version`), `agente` (`author`),
`invocation_id` e `event_id`. Nenhum prompt, chave de API ou conteúdo de
resposta é gravado nas métricas.

### 5.2. Regras (o que os testes garantem)

- **Ausência ≠ zero.** Campo sem valor no ADK vira `None` (exibido `"n/d"`),
  nunca `0`. Resposta de modelo **sem** `usage_metadata` ainda é registrada
  (a chamada aconteceu) com tokens `None` e `status="aviso"` — o aviso
  aparece em `alertas` e o `status_final` vira `sucesso_com_alertas`.
- **Sem contagem duplicada.** Eventos `partial=True` (streaming) são
  ignorados — o usage de parciais é cumulativo, só a resposta final conta. O
  registro usa a `chave_dedup` do coletor: `event.id` quando existir, senão
  `invocation_id + author` (agentes diferentes da mesma invocação, como no
  `ParallelAgent` da Fase 1, contam separado).
- **Eventos que não são chamada LLM** (mensagem do usuário, evento de
  controle sem conteúdo e sem usage) não entram.
- **Agregação parcial é explícita.** Se parte das chamadas veio sem usage, os
  totais somam só o que foi medido e o agente/fase sem medição aparece com
  `None` — a limitação fica visível, não escondida num número menor.

### 5.3. Custo (ativado)

> **`custo_estimado` é ESTIMATIVA, não valor faturado.** Ele é
> `tokens medidos × preço de tabela`. Nenhuma API de billing do provedor é
> consultada. Divergências com a cobrança real são esperadas quando o preço
> de tabela estiver desatualizado, quando a conta tiver desconto/crédito, ou
> quando o provedor cobrar itens que a contagem de tokens não captura. O
> consumo é **medido**; o preço é **configuração**.

**Precificação por chamada.** `gerar_resumo()` percorre os eventos
`tipo="chamada_llm"` e precifica **cada um** pelo modelo que respondeu
naquela chamada (`detalhes["modelo"]`, vindo do `model_version` do ADK), via
`resolver_precos_por_modelo()`; o total é a soma dessas parcelas. Um preço
único aplicado ao total agregado de tokens erra sempre que mais de um modelo
participa da mesma execução — o editor configurado com modelo próprio, ou o
fallback do Grupo 3 trocando de modelo no meio. Quando algum modelo não tem
preço resolvido, a soma vira **parcial** e isso é sinalizado nos `alertas`,
em vez de o resumo apresentar um total incompleto como se fosse completo.

`precos_override` (o parâmetro `precos=` de `gerar_resumo()`) continua
existindo: ele força um preço FIXO em TODAS as chamadas, para uso explícito
do chamador (testes, ou um contrato de preço que nenhuma fonte automática
conhece).

Consumo real (tokens, do ADK) e preço (configurado) continuam vindo de
fontes diferentes por construção — só se combinam na multiplicação final
(`estimar_custo_usd`).

`resolver_precos(config, papel=None)` — usado quando se quer o preço do
provedor/modelo CONFIGURADO — decide nesta ordem (a mesma precedência vale
para `resolver_precos_por_modelo()`, que parte do nome do modelo em vez da
config):

1. **Variável de ambiente** (`precos_de_ambiente()`): `GRUPO2_PRECO_USD_MILHAO_TOKENS_ENTRADA`
   e `GRUPO2_PRECO_USD_MILHAO_TOKENS_SAIDA` — as DUAS precisam estar
   presentes; configuração parcial conta como ausente. Sempre vence quando
   presente — é o único jeito de corrigir um preço que as fontes abaixo não
   têm ou têm desatualizado, sem editar código.
2. **`litellm.model_cost`** (`precos_modelos._preco_via_litellm`), SE o
   pacote `litellm` estiver instalado. `litellm` já é dependência do projeto
   para os provedores Maritaca/OpenAI (`model_provider.py`); em vez de manter
   só uma tabela estática própria, reaproveitamos a tabela de preços que o
   `litellm` já mantém (milhares de modelos, atualizada a cada release do
   pacote) — sem adicionar dependência nova. Sem `litellm` instalado (setup
   só-Gemini/mock), este passo é um no-op e cai direto no próximo. A Maritaca
   NÃO tem nenhuma entrada nessa tabela (nem por `"sabia*"`, nem por
   `"openai/sabia*"` — verificado em 2026-08-06); para ela, este passo sempre
   devolve `None`.
3. **Tabela de preços oficiais** (`precos_modelos.TABELA_PRECOS_OFICIAIS`):
   sem variável de ambiente e sem entrada no litellm, resolve o
   provedor/modelo já decidido para a execução (`model_provider.resolver_config`)
   e procura na tabela estática deste módulo — cobre os defaults e os
   exemplos documentados dos três provedores (Gemini 2.5/2.0 Flash, OpenAI
   gpt-4o-mini/gpt-4o, Maritaca Sabiá 4/Sabiazinho 4/Sabiá-3; os valores de
   Gemini/OpenAI foram conferidos contra `litellm.model_cost` e batem
   exatamente). Ver o módulo para as fontes e a data em que cada preço foi
   consultado — preços e câmbio mudam com o tempo, então trate os valores
   como referência documentada, não como fonte viva.
4. **`None`**: nem variável de ambiente, nem litellm, nem entrada na tabela
   oficial — `custo_estimado` permanece `None` (custo desconhecido, nunca 0).

**Mudança de decisão (PR #18): passou-se a precificar pelo modelo que
RESPONDEU, não pelo CONFIGURADO.** A versão original desta seção precificava
tudo pelo modelo configurado, para ser robusta a um gateway/proxy que
reescrevesse o nome do modelo na resposta. O trade-off foi reavaliado: o caso
de "mais de um modelo na mesma execução" (editor com modelo próprio, fallback
assumindo no meio) é **frequente e caro de errar**, enquanto o do gateway é
hipotético neste projeto — e, para ele, o override por variável de ambiente
(prioridade 1) continua vencendo tudo. Por isso `gerar_resumo()` agora usa
`detalhes["modelo"]` de cada evento `chamada_llm`. Se o provedor da sua
execução for um gateway com tabela de preço própria, configure o preço real
via ambiente em vez de confiar na tabela.

> **Nota de correção (2026-08-06):** uma versão anterior deste parágrafo
> citava `model_version="gpt-5.6-luna"` (visto num registro real do
> benchmark) como exemplo de identificador de gateway/proxy que não
> corresponderia a nenhum modelo público — essa suposição estava errada
> (baseada no corte de conhecimento do agente que escreveu o texto
> original, anterior ao lançamento do modelo). GPT-5.6 Luna é um modelo real
> e atual da OpenAI (lançado em 2026-07-09); a tabela oficial já tem o preço
> dele. Ver [`docs/benchmark_reference.md §6`](benchmark_reference.md) para
> o achado corrigido. O princípio do parágrafo acima continua válido — só o
> exemplo específico estava errado.

### 5.4. Como gerar e onde aparece

`python main.py mock` (reprodutível, sem chamadas LLM — tokens `n/d`) ou
`python main.py api [caminho.pdf]` (execução real com Gemini — requer
`GOOGLE_API_KEY`). O resumo sai no terminal (seção "Tokens (usage_metadata do
ADK)"), em `src/outputs/<run_id>/resumo_execucao.json` e dentro de
`final_report.json["resumo_execucao"]` — sempre com o `run_id` comum da
execução.

---

## 6. Fallback de LLM — trocas de modelo (`src/llm_fallback.py`)

Quando a reserva está configurada (`LLM_FALLBACK_PROVIDER`/`LLM_FALLBACK_MODEL`,
ver README), uma falha TEMPORÁRIA do modelo principal (timeout, limite de
requisições, erro de rede, indisponibilidade da API) troca para a opção
reserva em vez de interromper o pipeline. Cada tentativa e cada troca
alimentam as métricas pela MESMA fonte de fatos usada nos traces (Grupo 3):
provedor inicial, motivo da falha, opção fallback usada e qual modelo
respondeu.

### 6.1. Os três desfechos

Uma troca gera até dois eventos `tipo="fallback_llm"` (um terceiro evento de
tentativa bem-sucedida do principal, sem troca, não passa por aqui —
só a chamada real via `chamada_llm`/§5 registra isso):

| `detalhes["evento"]` | Quando | `status` do `ExecutionEvent` | Efeito no resumo |
|---|---|---|---|
| `"acionado"` | O principal falhou por motivo temporário; a reserva vai ser tentada. | `"aviso"` | `quantidade_fallbacks_acionados += 1`; entra em `alertas` (mensagem "fallback acionado: ..."). |
| `"respondeu"` | A reserva respondeu com sucesso. | `"sucesso"` | `quantidade_fallbacks_respondidos += 1`. |
| `"esgotado"` | Principal E reserva falharam; a exceção da reserva sobe e interrompe a execução. | `"falha"` | `quantidade_fallbacks_esgotados += 1`; marca `status_final = "falha"` (regra genérica de `status="falha"`, a mesma de qualquer outro tipo de evento). |

Cada evento traz em `detalhes`: `provedor_inicial` (rótulo `provedor:modelo`
do principal), `opcao_fallback` (idem, da reserva), `motivo_falha` (só em
`"acionado"` — um de `timeout`, `limite_de_requisicoes`, `erro_de_rede`,
`indisponibilidade_da_api`), `modelo_que_respondeu` (só em `"respondeu"`) e
`erro`/`erro_final` (resumo da exceção, só para depuração — nunca a chave de
API). `nome` do evento é o `papel` configurado (ex.: `"editor"`) ou `"llm"`
quando o papel não é distinguido (hoje só o Editor-Chefe passa `papel`
explícito a `build_model()`; os revisores e a leitura cruzada usam a reserva
GLOBAL, sem `papel`).

### 6.2. Ponto de conexão

`llm_fallback.py` não conhece `pipeline.py` nem `metrics/` diretamente — a
troca em si (`ModeloComFallback` e `model_provider.completar_texto`)
funciona igual com ou sem instrumentação. A ponte é feita por dois pontos de
contexto global, no mesmo padrão de `metrics/adk_usage.py`:

- **Coletor**: `metrics.adk_usage.obter_coletor_adk()` — o MESMO coletor que
  `registrar_usage_adk()` usa para os tokens (§5); o pipeline o registra uma
  vez, via `definir_coletor_adk(coletor)`.
- **Fase**: `metrics.coletor.obter_fase_atual()` — preenchida
  automaticamente por `ExecutionCollector.fase(nome)` durante o
  `with coletor.fase(self.name): ...` que já envolve cada fase em
  `pipeline.py`. Cobre tanto o `ModeloComFallback` (chamado pelos agentes
  dentro da fase) quanto `model_provider.completar_texto` (usado pelo
  corretor de retry, também dentro da fase que o disparou).

Sem coletor registrado (`metrics/` ausente ou execução avulsa fora do
pipeline), `_registrar_metrica()` é no-op — a troca de modelo nunca depende
da instrumentação. Sem fase ativa (uso fora de um `with coletor.fase(...)`),
o evento ainda é registrado, com `fase="desconhecida"`.

### 6.3. Traces (Grupo 3)

Nos traces, além dos três eventos acima (`llm_fallback_acionado`,
`llm_fallback_respondeu`, `llm_fallback_esgotado`, com os mesmos campos em
`attributes`), CADA tentativa — inclusive a do principal quando ele
responde de primeira, sem troca — vira um sub-span próprio
(`llm_tentativa_principal` / `llm_tentativa_reserva`), com início, fim,
duração e status. `author` é o `papel` (ou `"llm_fallback"` quando não há
papel) e `phase` é a mesma fase de `obter_fase_atual()` — os eventos de
fallback aparecem corretamente aninhados sob o span da fase em
`render_timeline()` (ver [`§8 do README`](../README.md#8-observabilidade-e-traces-grupo-3)).

### 6.4. Exemplo de `fallbacks_llm` no resumo

```json
"quantidade_fallbacks_acionados": 1,
"quantidade_fallbacks_respondidos": 1,
"quantidade_fallbacks_esgotados": 0,
"fallbacks_llm": [
  {
    "fase": "fase_3_editor_chefe",
    "papel": "editor",
    "evento": "acionado",
    "status": "aviso",
    "timestamp": 1731000000.123,
    "provedor_inicial": "gemini:gemini-2.5-flash",
    "motivo_falha": "limite_de_requisicoes",
    "opcao_fallback": "maritaca:sabia-3",
    "modelo_que_respondeu": null
  },
  {
    "fase": "fase_3_editor_chefe",
    "papel": "editor",
    "evento": "respondeu",
    "status": "sucesso",
    "timestamp": 1731000000.456,
    "provedor_inicial": "gemini:gemini-2.5-flash",
    "motivo_falha": null,
    "opcao_fallback": "maritaca:sabia-3",
    "modelo_que_respondeu": "maritaca:sabia-3"
  }
]
```

---

## 7. Caminho de evolução para ADK/OpenTelemetry

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
| Evento `tipo="chamada_llm"` (`detalhes` com `tokens_*`, `modelo`) | Atributos de span padronizados pela semântica **gen-ai** do OpenTelemetry: `gen_ai.usage.input_tokens`/`output_tokens` ↔ `tokens_entrada`/`tokens_resposta`, `gen_ai.request.model` ↔ `modelo`. A captura já existe (§5); trocar o destino por um exporter OTel não mudaria o contrato do evento. |
| Evento `tipo="fallback_llm"` (§6) | Já é um evento por TENTATIVA no trace (`llm_tentativa_principal`/`llm_tentativa_reserva`, spans reais); em métricas, os três desfechos mapeiam para `span.add_event("gen_ai.fallback", {...})` no span da chamada, com `provedor_inicial`/`opcao_fallback`/`motivo_falha` como atributos do evento. |

A camada atual (`ExecutionCollector` em memória + JSON no fim da execução) é
deliberadamente a implementação mais simples que respeita esse contrato de
campos — trocar o "storage" por um exporter OTel real não deveria exigir
mudar `ExecutionEvent`, `ResumoExecucao` nem a convenção de agregação
documentada em §1.
