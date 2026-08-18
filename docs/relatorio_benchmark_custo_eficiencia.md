# Grupo 2 — Benchmark, Custo e Eficiência

Relatório da entrega que completa o cálculo de custo, registra a configuração
completa de cada execução do benchmark e compara o pipeline completo com uma
variante experimental sem leitura cruzada.

---

## 1. Contexto da atividade

O pedido tinha quatro partes:

1. O peer review já capturava tokens, duração, chamadas, retries e falhas,
   mas o campo de custo continuava vazio — os preços dos modelos não estavam
   sendo usados na geração do resumo.
2. O benchmark já tinha dez documentos, mas os resultados não identificavam
   de forma completa o provedor, o modelo e a configuração usados em cada
   execução — o que dificulta comparar resultados depois (inclusive para o
   documento que o grupo vai entregar à Turing).
3. O fluxo completo faz sete chamadas de LLM (três revisões independentes,
   três leituras cruzadas e uma decisão do editor), e não havia como medir o
   quanto a leitura cruzada melhora a resposta frente ao custo/tempo
   adicionais que ela introduz.
4. A entrega deveria mostrar uma tabela e uma conclusão sobre o benefício da
   comunicação entre os agentes, comparando os mesmos PDFs e modelos nas duas
   configurações (tokens, duração, custo e qualidade das críticas/decisão
   final).

Este trabalho partiu de `origin/main` já com tokens reais capturados via
`usage_metadata` do ADK e suporte a três provedores (Gemini, Maritaca,
OpenAI) via `model_provider.py` — nenhum desses dois pontos foi criado aqui,
só consumidos.

---

## 2. Checklist — o que foi pedido x o que foi entregue

| Pedido | Como foi atendido |
|---|---|
| Completar o cálculo de custo. | Novo módulo [`src/metrics/precos_modelos.py`](../src/metrics/precos_modelos.py) com `resolver_precos()`, conectado às duas chamadas de `gerar_resumo()` em [`src/pipeline.py`](../src/pipeline.py). `custo_estimado` deixa de ser sempre `null`. |
| Consultar a documentação oficial para calcular o custo corretamente. | Preços pesquisados nas páginas oficiais de Gemini, OpenAI e Maritaca (fontes e datas documentadas no módulo) — e depois reforçados por uma segunda fonte viva, `litellm.model_cost`, já que o projeto depende de `litellm`. |
| Registrar provedor, modelo e configuração de cada execução do benchmark. | [`src/benchmark/executar.py`](../src/benchmark/executar.py) grava `provider`, `model`, `structured_output` e `cross_review_enabled` em todo registro; [`comparar.py`](../src/benchmark/comparar.py) expõe essas colunas na tabela comparativa. |
| Criar uma comparação pequena entre o pipeline completo e uma versão sem leitura cruzada. | `pipeline.run_demo(cross_review=False)` desativa a Fase 2 sem chamada LLM; novo módulo [`src/benchmark/ablacao_cross_review.py`](../src/benchmark/ablacao_cross_review.py) roda o par (com/sem) e gera tabela + conclusão. |
| Usar os mesmos PDFs e modelos nas duas opções. | `ablacao_cross_review.py` roda o MESMO `doc_id`, no MESMO `--mode`/provedor/modelo, só alternando `cross_review`. |
| Comparar tokens, duração, custo e qualidade das críticas/decisão final. | `delta` calculado por `comparar_par()`: variação % de chamadas LLM/duração/tokens/custo, mais se decisão final, notas por revisor ou quantidade de críticas mudaram. |
| Entregar tabela + conclusão. | `resultados/ablacao_cross_review.md` — tabela markdown e uma conclusão **calculada automaticamente** a partir dos dados (não escrita à mão), com a ressalva de que "qualidade" ali é um proxy objetivo, não uma nota da qualidade textual das críticas. |

---

## 3. Custo estimado — ativação

### 3.1. O que já existia

`src/metrics/adk_usage.py` já sabia **calcular** custo
(`estimar_custo_usd(tokens_entrada, tokens_resposta, precos)`) e já sabia
**ler** um preço configurado por variável de ambiente
(`precos_de_ambiente()`). O que faltava: ninguém no pipeline chamava essas
duas peças juntas — `gerar_resumo()` nunca recebia `precos=`, então
`custo_estimado` era sempre `null`, mesmo com tokens medidos.

### 3.2. O que foi adicionado

**[`src/metrics/precos_modelos.py`](../src/metrics/precos_modelos.py)** —
`resolver_precos(config, papel=None)` decide o preço de uma execução em
**três camadas de precedência**:

1. **Variável de ambiente** (`GRUPO2_PRECO_USD_MILHAO_TOKENS_ENTRADA`/
   `_SAIDA`) — as duas precisam estar presentes; sempre vence quando
   configurada. É o único jeito de corrigir manualmente um preço que as
   fontes automáticas não têm ou têm errado.
2. **`litellm.model_cost`**, se o pacote `litellm` estiver instalado.
   `litellm` já é dependência do projeto (usado por `model_provider.py` para
   rotear Maritaca/OpenAI através do adaptador `LiteLlm` do ADK) — em vez de
   manter só uma tabela própria, esta camada reaproveita a tabela de preços
   que o `litellm` já mantém (quase 3 mil modelos, atualizada a cada release
   do pacote), sem adicionar dependência nova. Import guardado: sem
   `litellm` instalado (setup só-Gemini/mock), esta camada é um no-op.
3. **Tabela de preços oficiais estática** (`TABELA_PRECOS_OFICIAIS`, no
   mesmo módulo) — cobre os defaults e exemplos documentados dos três
   provedores: Gemini 2.5/2.0 Flash, OpenAI gpt-4o-mini/gpt-4o, Maritaca
   Sabiá 4/Sabiazinho 4/Sabiá-3. Cada entrada cita a URL oficial e a data em
   que o preço foi consultado (2026-08-06).

Sem preço configurado e sem entrada reconhecida em nenhuma das três fontes,
`custo_estimado` permanece `null` — nunca `0` (ausência de preço não é preço
zero).

**Custo por chamada (atualização do PR #18).** A versão original desta
entrega resolvia **um** preço por execução, em `pipeline.run_demo()`, e o
aplicava ao total agregado de tokens. Isso erra sempre que mais de um modelo
responde na mesma execução — o editor configurado com um modelo diferente do
resto do pipeline, ou o fallback do Grupo 3 assumindo no meio da execução.
O problema foi apontado e corrigido:

- `resolver_precos_por_modelo()` resolve o preço a partir do **modelo que
  efetivamente respondeu** (campo `detalhes.modelo` do evento, vindo de
  `event.model_version` do ADK), sem depender do provedor/papel configurado.
- `gerar_resumo()` precifica **cada evento `tipo="chamada_llm"`
  individualmente** e soma os custos; quando algum modelo não tem preço
  resolvido, a soma é sinalizada como **parcial** nos alertas, em vez de
  fingir um total completo.
- `pipeline.py` deixou de resolver um preço único para a execução inteira.

Reprodução manual do cenário (dois modelos na mesma execução), sem precisar
de API: [`scripts/validar_custo_por_chamada.py`](../scripts/validar_custo_por_chamada.py).

### 3.3. Descobertas ao longo do trabalho

- **A tabela precificava pelo modelo CONFIGURADO, não pelo devolvido pelo
  ADK.** Era uma proteção contra um gateway/proxy que reescrevesse o nome do
  modelo na resposta. **Esta decisão foi revertida no PR #18**: o custo agora
  é resolvido pelo modelo que realmente respondeu, porque o caso comum
  (editor com modelo próprio, fallback trocando de modelo) é mais frequente e
  mais caro de errar do que o caso hipotético do proxy — e, para o proxy,
  continua existindo o override por variável de ambiente, que vence tudo.
- **`litellm.model_cost` não cobre a Maritaca.** Verificado manualmente:
  nenhuma entrada `"sabia*"` nem `"openai/sabia*"` na tabela do `litellm`.
  Para esse provedor, a camada 3 (tabela estática própria) é a única fonte
  automática — foi por isso que ela precisou existir mesmo depois de eu
  descobrir a alternativa via `litellm`.
- **Correção de um engano meu:** eu tinha documentado (aqui, em
  `docs/metricas_reference.md` e em `docs/benchmark_reference.md`) que
  `"gpt-5.6-luna"` — visto num registro real do benchmark, com
  `LLM_PROVIDER=openai` — era provavelmente um gateway/proxy, porque não
  reconhecia esse nome de modelo. Estava errado: é um modelo real e atual da
  OpenAI (GPT-5.6 Luna, lançado em 2026-07-09), só posterior ao meu corte de
  conhecimento. Confirmei com uma chamada real à API (chave fornecida pelo
  usuário para teste) e com a documentação oficial da OpenAI, e corrigi os
  três documentos — a tabela de preços já tem a entrada dele
  (US$0,20/US$1,20 por milhão de tokens, preço padrão pós-corte de
  30/07/2026).

### 3.4. Testes

19 testes em [`src/metrics/tests/test_precos_modelos.py`](../src/metrics/tests/test_precos_modelos.py):
casamento por prefixo/case-insensitive na tabela estática, as três camadas
de precedência isoladas (incluindo simular ausência do `litellm` via
`builtins.__import__` e forçar divergência entre `litellm` e a tabela
estática para provar qual vence), e a lacuna de cobertura da Maritaca no
`litellm`.

---

## 4. Configuração completa por execução (benchmark)

### 4.1. O que faltava

`resultados/execucoes.json` registrava `mode` (mock/api), mas nada sobre
QUAL provedor/modelo foi de fato usado, nem se a leitura cruzada estava
ativa — impossível comparar duas execuções entre si com confiança, e
impossível documentar isso no relatório para a Turing.

### 4.2. O que foi adicionado

Em [`src/benchmark/executar.py`](../src/benchmark/executar.py):

- `_config_llm(mode)`: em modo `api`, resolve e grava `provider`, `model`,
  `structured_output` reais (via `model_provider.resolver_config`); em modo
  `mock`, grava `None` nos três — nenhuma chamada real aconteceu, então não
  faz sentido anunciar um provedor que não foi usado.
- `cross_review_enabled`: se a Fase 2 rodou normalmente ou não.
- `_campos_de_qualidade(verdict)`: extrai do veredito do editor
  `notas_por_revisor`, `quantidade_criticas` e `quantidade_criticas_bloqueantes`
  — sinais objetivos de qualidade que não existiam antes no registro do
  benchmark.
- Nova flag `--cross-review {on,off}` na CLI.
- **Proteção de dado:** execuções `--cross-review off` são gravadas sob a
  chave `"<doc_id>__sem_cross_review"` em vez de `doc_id` puro, para o
  upsert de `execucoes.json` não apagar silenciosamente o registro da
  variante completa do mesmo documento.

Em [`src/benchmark/comparar.py`](../src/benchmark/comparar.py): a tabela
comparativa (`CAMPOS_TABELA`) passa a expor todas essas colunas novas, mais
`chamadas_llm`/`modelo_usado` (que já existiam no resumo, mas não apareciam
na tabela do benchmark).

O schema completo do registro está documentado em
[`docs/benchmark_reference.md §3.1`](benchmark_reference.md).

### 4.3. Testes

7 testes em [`src/benchmark/tests/test_executar.py`](../src/benchmark/tests/test_executar.py).

---

## 5. Variante sem leitura cruzada

### 5.1. Mecanismo

Em [`src/pipeline.py`](../src/pipeline.py), `run_demo(cross_review=False)`
(também via `python main.py --no-cross-review`, e persistido em retomadas de
execução via checkpoint) desativa a Fase 2: **nenhuma chamada LLM** nela.
Cada revisor "mantém" o parecer da Fase 1 exatamente como está, construído
deterministicamente num `CrossReviewSchema` válido
(`mudou_posicao=False`, `mudancas=[]`, `resposta_aos_pares` documentando o
motivo). O contrato de dados entre fases não muda — a Fase 3 (editor) e a
Fase 4 (relatório) rodam sem nenhuma alteração de código.

Isso reduz as chamadas LLM de **7** (3 revisores + 3 leituras cruzadas + 1
editor) para **4** (3 revisores + 1 editor).

A flag é propagada por todo o rastro de observabilidade: log de início do
pipeline, atributo do span raiz do trace, e `final_report.json["cross_review_enabled"]`.

### 5.2. Testes

7 testes em [`src/tests/test_cross_review_ablation.py`](../src/tests/test_cross_review_ablation.py),
incluindo retomada de execução preservando a variante escolhida.

---

## 6. Ferramenta de comparação pareada

### 6.1. `ablacao_cross_review.py`

Novo módulo [`src/benchmark/ablacao_cross_review.py`](../src/benchmark/ablacao_cross_review.py).
CLI:

```bash
python -m src.benchmark.ablacao_cross_review --mode {mock,api} --docs id1,id2,...
```

Para cada `doc_id`, roda o pipeline **duas vezes** — `cross_review=True` e
`cross_review=False`, mesmo modo/provedor/modelo/PDF — reaproveitando
`executar.processar_documento()` (não duplica a lógica de execução). Calcula
o `delta` entre as duas: variação percentual de chamadas LLM, duração,
tokens e custo (negativo = a variante sem leitura cruzada gastou menos), e
sinaliza se a decisão final, as notas por revisor ou a quantidade de
críticas mudaram.

Gera `resultados/ablacao_cross_review.json` (os dois registros brutos + o
delta, upsert por `doc_id`) e `.md` (tabela + **conclusão calculada
automaticamente** a partir dos deltas agregados — não escrita à mão).

### 6.2. Limite explícito de escopo

"Qualidade" comparada é um **proxy objetivo** (contagem de críticas, quantas
são bloqueantes, se a decisão mudou, se as notas mudaram) — não é uma nota
de qualidade textual das críticas, que exigiria julgamento humano ou um
LLM-juiz. Isso está documentado no próprio `.md` gerado, na última linha da
conclusão.

### 6.3. Testes

11 testes em [`src/benchmark/tests/test_ablacao_cross_review.py`](../src/benchmark/tests/test_ablacao_cross_review.py),
incluindo um teste de integração que roda o par completo em modo mock.

---

## 7. Validação

### 7.1. Suíte de testes

**327 passando, 0 falhas** na execução do CI
([run `31834997779`](https://github.com/Agentes-Turing-2026/Pipeline_Peer_Review_Integrada/actions/runs/31834997779),
Ubuntu, Python 3.14, todas as dependências instaladas). Esta é a medição de
referência: foi a primeira vez que o passo do Pytest chegou a executar — até
então o job abortava antes dele (ver `alteracoes_grupo2_corpus_docs_ci.md` §8.1).

Na máquina de desenvolvimento a mesma suíte fecha em **280 passando / 9
pulados**: os 9 são dependências opcionais ausentes (`liteparse`,
`google-adk`, `google-genai`), e os arquivos de teste que dependem delas são
pulados por inteiro — daí a diferença para os 327 do CI. **Testes que envolvem
ADK ou LiteLLM não são verificáveis localmente**, apenas no CI.

> **Intermitência conhecida, de ambiente.** Rodando com o repositório dentro
> de uma pasta sincronizada pelo **OneDrive** no Windows, ~1 execução em 5
> falha com `PermissionError: [WinError 5]` no `os.replace()` de
> `src/persistencia.py:67` (escrita atômica do checkpoint): o OneDrive trava
> o `.tmp` no instante da troca. **Não é regressão desta branch** — foi
> reproduzido igual na `origin/dev` limpa — e não afeta o CI, que roda em
> `ubuntu-latest`. Quem for depurar isso no Windows, mova o clone para fora
> do OneDrive.

### 7.2. Comparação pareada com dados reais — EXECUTADA

**Situação atual: a comparação real foi feita.** São **5 PDFs reais**, cada
um rodado nas duas variantes (com e sem leitura cruzada) = **10 execuções
reais do pipeline**, provedor `openai`, modelo `gpt-5.6-luna`, com chamadas
LLM de verdade e tokens medidos pelo `usage_metadata` do ADK:

| Documento | Chamadas (com/sem) | Tokens (com/sem) | Custo estimado US$ (com/sem) |
|---|---|---|---|
| `icd_hallucinations_2312_15710` | 7/4 | 156.062/85.153 | 0,0416/0,0241 |
| `acl_emnlp2024_116` | 7/4 | 115.533/61.612 | 0,0336/0,0192 |
| `arxiv_2606_00819` | 7/4 | 93.294/49.201 | 0,0292/0,0171 |
| `comdem_17665` | 7/4 | 123.708/66.156 | 0,0361/0,0206 |
| `psicologia_slides_ciclo_sono` | 7/4 | 36.713/17.445 | 0,0166/0,0099 |

Total estimado das 10 execuções: **US$ 0,248**. Tabela completa, deltas e
conclusão calculada em
[`src/benchmark/resultados/ablacao_cross_review.md`](../src/benchmark/resultados/ablacao_cross_review.md);
leitura interpretada em
[`docs/entrega_grupo2_custo_eficiencia.md`](entrega_grupo2_custo_eficiencia.md).

Esta seção antes dizia que a comparação real "continua não validada" — estava
desatualizada: descrevia o estado do trabalho antes das execuções pagas.

### 7.3. Execuções reais x smoke test em modo mock — separados

O artefato `ablacao_cross_review.{json,md}` também guarda um par
`exemplo_mock`, em **modo mock**: pareceres lidos de um JSON pré-salvo,
**nenhuma chamada LLM**, sem tokens e sem custo. Ele existe só para provar
que a ferramenta roda de ponta a ponta sem gastar API.

Até 13/08/2026 esse par entrava no MESMO agregado das execuções reais, o que
produzia números errados no `.md` gerado: "6 documentos comparáveis" (eram 5
reais + 1 mock) e uma média de duração de -17,5% diluída pelo par mock, cuja
duração é ~0,01 s de I/O. `separar_por_modo()` corrigiu isso — agregados e
tabelas agora são calculados **só sobre `mode == "api"`**, e o mock aparece
numa seção própria marcada como smoke test. Com a separação, a duração real é
**-20,9%** e a decisão mudou em **1/5** documentos, que é exatamente o que a
tabela conferida à mão no README do grupo já dizia.

### 7.4. O que a avaliação de qualidade NÃO cobre

"Qualidade", em todos os números desta entrega, é medida por **indicadores
automáticos** extraídos do veredito do editor: quantidade de críticas,
quantas são bloqueantes, se a decisão final mudou e se as notas por revisor
mudaram.

**Não houve avaliação humana do conteúdo das críticas** — ninguém leu os
pareceres para julgar se são pertinentes, corretos ou bem argumentados — e
também não foi usado um LLM-juiz. A consequência é direta e vale registrar
sem rodeio: está medido **quanto a leitura cruzada custa** e **se ela muda o
resultado**; **não** está medido **se ela melhora a revisão**. A pergunta
original da atividade ("quanto a leitura cruzada melhora a resposta?") só é
respondida de verdade com leitura humana dos textos de `resposta_aos_pares`
em cada `final_report.md`, ou com um avaliador automático validado — nenhum
dos dois está nesta entrega.

Essa ressalva é emitida automaticamente na conclusão gerada
(`LIMITE_QUALIDADE` em `ablacao_cross_review.py`), com teste que garante que
ela nunca some do relatório.

### 7.5. Corpus 100% público (13/08/2026)

Todos os documentos do corpus passaram a ter `fonte_url` pública e
verificada. Os 4 PDFs de psicologia que só existiam em disco
(`caminho_local` em `cache_pdfs/`, que é gitignored) foram substituídos por
equivalentes públicos que preservam o papel de cada um no corpus, e o corpus
cresceu de 10 para **15 documentos** com a inclusão de química e arquitetura
— ver [`docs/benchmark_reference.md`](benchmark_reference.md).

Impacto nesta seção: dos 5 documentos da comparação real,
`psicologia_slides_ciclo_sono` era um dos locais e foi aposentado do
manifesto. O registro da execução **permanece** no
`ablacao_cross_review.json` (resultado de execução paga não se apaga), mas
esse documento não é reprodutível por terceiros; os outros 4 são.

---

## 8. Como rodar

```bash
# Ativa custo automaticamente (modo api, qualquer provedor já configurado):
python main.py api

# Registra provedor/modelo/config completos no benchmark:
python -m src.benchmark.executar --mode api --docs icd_hallucinations_2312_15710

# Variante sem leitura cruzada isolada:
python main.py api --no-cross-review
python -m src.benchmark.executar --mode api --docs icd_hallucinations_2312_15710 --cross-review off

# Comparação pareada (tabela + conclusão) — cada doc_id roda 2x:
python -m src.benchmark.ablacao_cross_review --mode api --docs icd_hallucinations_2312_15710,acl_emnlp2024_116,arxiv_2606_00819,comdem_17665,psicologia_slides_ciclo_sono

# Sobrescrever preço manualmente (útil se o provedor for um gateway com tabela própria):
GRUPO2_PRECO_USD_MILHAO_TOKENS_ENTRADA=0.30 GRUPO2_PRECO_USD_MILHAO_TOKENS_SAIDA=2.50 python main.py api

# Testes desta entrega:
python -m pytest src/metrics/tests/test_precos_modelos.py src/benchmark/tests/test_executar.py \
  src/benchmark/tests/test_ablacao_cross_review.py src/tests/test_cross_review_ablation.py -v
```

---

## 9. Arquivos alterados/criados

| Arquivo | Natureza |
|---|---|
| `src/metrics/precos_modelos.py` | Novo — tabela de preços + `resolver_precos()`. |
| `src/metrics/tests/test_precos_modelos.py` | Novo — 19 testes. |
| `src/pipeline.py` | Alterado — `resolver_precos()` conectado a `gerar_resumo()`; flag `cross_review_enabled` em `run_demo`/`CrossReviewPhase`/relatório/trace. |
| `main.py` | Alterado — flag `--no-cross-review`. |
| `src/benchmark/executar.py` | Alterado — `_config_llm()`, `_campos_de_qualidade()`, flag `--cross-review`, proteção de chave no upsert. |
| `src/benchmark/comparar.py` | Alterado — novas colunas na tabela. |
| `src/benchmark/ablacao_cross_review.py` | Novo — CLI de comparação pareada. |
| `src/benchmark/tests/test_executar.py` | Novo — 7 testes. |
| `src/benchmark/tests/test_ablacao_cross_review.py` | Novo — 11 testes. |
| `src/tests/test_cross_review_ablation.py` | Novo — 7 testes. |
| `.env.example` | Alterado — variáveis de preço documentadas. |
| `README.md`, `docs/metricas_reference.md`, `docs/benchmark_reference.md` | Alterados — documentação das três seções acima. |
| `src/benchmark/resultados/*.json`/`*.md` | Regenerados — artefatos de demonstração em modo mock (grátis, reprodutíveis). |

**Total (código + docs, sem contar os artefatos de resultado regenerados):**
8 arquivos alterados (428 inserções, 57 remoções) + 6 arquivos novos de
código/teste, **44 testes novos**.
