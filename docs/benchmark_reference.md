# Benchmark e Diagnóstico — referência (Grupo 2)

`src/benchmark/` roda o pipeline completo sobre um **corpus de PDFs reais**
(não só o artigo de exemplo embutido) e registra, por documento, o
diagnóstico da execução — sucesso, bloqueio de entrada ou falha, com as
métricas que [`src/metrics/`](../src/metrics/) já produz. O objetivo original
é avaliação/benchmark: em vez de confiar só no modo mock (que usa pareceres
fixos e nunca falha), ter evidência real de como o pipeline se comporta com
documentos variados — diferentes tamanhos, formatos, qualidades de PDF — e
acumular essa evidência de forma auditável, sem gastar em API sem controle.
Ver [`README.md §10`](../README.md) para como isso se encaixa no restante do
projeto.

Este documento é a referência de **schema do manifesto**, **como rodar** e
os **achados** já coletados com o corpus atual.

---

## 1. Visão geral

`src/benchmark/` só **consome** o que já existe — `pipeline.run_demo()` e
`report.data["resumo_execucao"]` (produzido por `metrics/resumo.py`) — não
recalcula nem duplica lógica de métrica. A ferramenta adiciona:

1. Um **manifesto** (`corpus_manifest.json`) que descreve um corpus de
   documentos reais, com metadados (área, características de layout,
   observações) independentes de onde o PDF mora.
2. Um **executor em lote** que roda o pipeline sequencialmente sobre uma
   seleção explícita de documentos, capturando os três desfechos possíveis
   (sucesso, entrada bloqueada, falha de execução) sem deixar um erro em um
   documento derrubar o lote inteiro — e registrando, por execução,
   provedor/modelo/`structured_output`/`cross_review_enabled` completos
   (§3.1), não só o `mode` (mock/api).
3. Um **comparativo** entre execuções já registradas, com um bloco de
   atenção que separa tudo que não é "sucesso limpo".
4. Um **comparativo pareado** com/sem leitura cruzada
   (`ablacao_cross_review.py`, §4.1) — a mesma execução, duas vezes, com e
   sem a Fase 2, para medir se a leitura cruzada compensa o custo/tempo
   extra que ela introduz.

Toda a orquestração de fases, validação e agentes continua sendo
responsabilidade de `pipeline.py`/`metrics/`; este pacote não os altera.

---

## 2. Estrutura do pacote

| Caminho | O que é | Committed? |
|---|---|---|
| `corpus.py` | `DocumentoCorpus` (dataclass), `carregar_corpus()` (lê e valida o manifesto), `resolver_pdf_local()` (resolve/baixa o PDF de um documento). | Sim |
| `executar.py` | CLI (`python -m src.benchmark.executar`) — roda o lote selecionado, imprime o diagnóstico de cada execução e faz upsert em `resultados/execucoes.json`. | Sim |
| `comparar.py` | CLI (`python -m src.benchmark.comparar`) — gera o comparativo a partir de `resultados/execucoes.json`, sem rodar o pipeline. | Sim |
| `ablacao_cross_review.py` | CLI (`python -m src.benchmark.ablacao_cross_review`) — roda cada documento DUAS vezes (com/sem leitura cruzada) e grava o comparativo pareado em `resultados/ablacao_cross_review.{json,md}`. Reaproveita `executar.processar_documento`, não duplica a execução. | Sim |
| `corpus_manifest.json` | O corpus em si: lista de documentos com seus metadados. | Sim |
| `resultados/execucoes.json` | Um registro por `doc_id` com o **resultado mais recente** daquele documento (upsert). Ver §3 para o schema completo do registro (inclui provedor/modelo/`cross_review_enabled`/qualidade desde a atividade de custo e eficiência). | Sim |
| `resultados/execucoes/<run_id>_resumo.json` | Um arquivo por **execução** (histórico completo, não só a mais recente) — cópia do `ResumoExecucao` daquela rodada. | Sim |
| `resultados/comparativo.json` / `comparativo.md` | Saída de `comparar.py` — snapshot mais recente do comparativo. | Sim |
| `resultados/ablacao_cross_review.json` / `.md` | Saída de `ablacao_cross_review.py` — pares (com/sem leitura cruzada) por `doc_id`, com o delta calculado e uma conclusão agregada. | Sim |
| `cache_pdfs/` | PDFs baixados de `fonte_url` (cache de download) e PDFs fornecidos manualmente via `caminho_local`. | **Não** (`.gitignore`) |
| `tests/test_corpus.py`, `tests/test_comparar.py`, `tests/test_executar.py`, `tests/test_ablacao_cross_review.py` | Testes 100% offline (modo mock quando exercitam `run_demo`) — nunca gastam API real. | Sim |

`cache_pdfs/` é gitignorado porque parte do seu conteúdo é reconstruível
(download via `fonte_url`) e parte não é rastreada por design — documentos
fornecidos via `caminho_local` (ver §3) não têm `fonte_url` e, portanto, não
são reproduzíveis automaticamente num clone novo do repositório; isso é uma
limitação conhecida, não um bug (ver §7).

---

## 3. Schema do manifesto

Cada entrada de `corpus_manifest.json["documentos"]` é validada por
`carregar_corpus()` contra o schema de `DocumentoCorpus`:

| Campo | Tipo | Obrigatório? | Descrição |
|---|---|---|---|
| `id` | `str` | Sim | Identificador curto e único — chave usada em `--docs`, em `resultados/execucoes.json` e nos nomes de arquivo de resumo. |
| `titulo` | `str` | Sim | Título legível do documento. |
| `area` | `str` | Sim | Categoria livre (ex.: `"inteligencia_artificial"`, `"psicologia"`, `"smoke_test"`). |
| `caracteristicas` | `list[str]` | Sim | Lista livre de características de layout/qualidade/formato (ex.: `"duas_colunas"`, `"escaneado"`, `"slide"`). |
| `fonte_url` | `str \| None` | Não (default `None`) | URL pública do PDF, se baixável. |
| `caminho_local` | `str \| None` | Não (default `None`) | Caminho já em disco, se o PDF foi fornecido manualmente (não baixável). |
| `observacoes` | `str \| None` | Não (default `None`) | Notas livres — origem, decisões pendentes, avisos para quem for rodar em `api`. |

`id`, `titulo`, `area` e `caracteristicas` ausentes fazem `carregar_corpus()`
levantar `ValueError` apontando o documento (pelo `id`, ou pelo índice na
lista se o próprio `id` estiver faltando) e o campo exato que falta — não há
default silencioso. `id`s duplicados também levantam `ValueError`, listando
os ids repetidos.

`fonte_url` e `caminho_local` podem **ambos ser `None`** — esse é o caso do
documento placeholder `exemplo_mock` (sem PDF real associado; o executor
roda com `pdf_path=None`, que usa o artigo de exemplo embutido do pipeline).
Isso é válido e intencional, não um erro.

### Exemplo real (`icd_hallucinations_2312_15710`)

```json
{
  "id": "icd_hallucinations_2312_15710",
  "titulo": "Alleviating Hallucinations of Large Language Models through Induced Hallucinations",
  "area": "inteligencia_artificial",
  "fonte_url": "https://arxiv.org/pdf/2312.15710v2",
  "caminho_local": null,
  "caracteristicas": ["duas_colunas", "tabelas", "formulas_matematicas", "citacoes_extensas", "medio"],
  "observacoes": "arXiv 2312.15710v2 (Zhang, Cui, Bi, Shi). Formato padrao ACL. Caracteristicas de layout (colunas/paginas) a confirmar apos a extracao real — ajustar aqui se a extracao mostrar algo diferente."
}
```

`resolver_pdf_local()` baixa PDFs de `fonte_url` via `urllib.request` (sem
dependência nova), com um `User-Agent` explícito — algumas fontes (ex.:
arXiv) recusam requisições sem um `User-Agent` reconhecível (HTTP 403). O
download é cacheado em `cache_pdfs/<id>.pdf`; se o arquivo já estiver
cacheado, não baixa de novo. Erro de rede/HTTP propaga como exceção clara —
nunca vira silenciosamente um "documento sem PDF real" (que é um caso válido
e diferente, ver §2).

---

### 3.1. Schema do registro de execução (`execucoes.json[doc_id]`)

Cada valor de `execucoes.json["execucoes"]` é o dict que `executar.py` monta
em `processar_documento()` — upsert por `doc_id` (o registro mais recente
vence; histórico completo fica em `resultados/execucoes/<run_id>_resumo.json`).

| Campo | De onde vem | Observação |
|---|---|---|
| `doc_id`, `titulo`, `area`, `caracteristicas` | manifesto (§3) | — |
| `mode` | `--mode` | `"mock"` ou `"api"`. |
| `provider` | `model_provider.resolver_config` | `None` em modo mock (nenhuma chamada real acontece — anunciar um provedor aqui sugeriria configuração que não foi usada); em modo api, `"gemini"`/`"maritaca"`/`"openai"`. |
| `model` | idem | id do modelo CONFIGURADO (ex.: `"gemini-2.5-flash"`) — não confundir com `modelo_usado` do resumo, que é o `model_version` cru DEVOLVIDO pelo ADK (podem divergir, ver §6). |
| `structured_output` | idem | se o `response_schema` nativo estava ligado para essa execução. |
| `cross_review_enabled` | `--cross-review` (`executar.py`) | `True` (default) roda a Fase 2 normalmente; `False` roda a variante sem leitura cruzada (`pipeline.run_demo(cross_review=False)`). Execuções `False` são gravadas sob a chave `"<doc_id>__sem_cross_review"` em vez de `doc_id` puro — a chave `doc_id` continua reservada para a variante default (`True`), para não apagar silenciosamente um registro pela outra variante no upsert. |
| `timestamp`, `run_id`, `resultado`, `decisao_final`, `requer_revisao_humana`, `duracao_total_s`, `tokens_totais`, `custo_estimado`, `chamadas_llm`, `modelo_usado`, `quantidade_tools_chamadas`, `quantidade_retries`, `quantidade_falhas`, `alertas`, `erro` | `run_demo`/`ResumoExecucao` | Mesma semântica de `docs/metricas_reference.md §3`. |
| `notas_por_revisor` | `report.data["phase3_verdict"]` | Mapa revisor → nota geral (1-4) considerada na síntese do editor. |
| `quantidade_criticas` | idem | Total de entradas em `criticas` (fraquezas + críticas bloqueantes), sem distinguir tipo. |
| `quantidade_criticas_bloqueantes` | idem | Subconjunto de `quantidade_criticas` com `tipo == "critica"`. |

`provider`/`model`/`structured_output`/`notas_por_revisor`/
`quantidade_criticas`/`quantidade_criticas_bloqueantes` não existiam antes da
atividade de custo/eficiência (Grupo 2) — registros gravados por uma versão
anterior de `executar.py` não os têm; `comparar.py` mostra `—` para eles em
vez de quebrar (ver `resultados/comparativo.md`, linhas anteriores a
2026-08-06).

---

## 4. Como rodar

```bash
# Executa um lote (sempre sequencial, nunca em paralelo — evita estourar
# rate limit em modo api). --mode e --docs são OBRIGATÓRIOS.
.venv/bin/python -m src.benchmark.executar --mode mock --docs exemplo_mock
.venv/bin/python -m src.benchmark.executar --mode api --docs icd_hallucinations_2312_15710,acl_emnlp2024_116

# Variante experimental sem leitura cruzada (Fase 2 sem chamada LLM) — grava
# sob "<doc_id>__sem_cross_review", não sobrescreve o registro default.
.venv/bin/python -m src.benchmark.executar --mode api --docs icd_hallucinations_2312_15710 --cross-review off

# Gera o comparativo a partir do que já foi executado (não roda o pipeline).
.venv/bin/python -m src.benchmark.comparar
```

`--mode` não tem default e `--docs` não tem um modo implícito de "roda o
corpus inteiro" — as duas coisas são obrigatórias de propósito. `--mode api`
gasta dinheiro de verdade (chamadas reais ao provedor de LLM configurado);
sem essas duas exigências, um comando digitado sem cuidado (ex.: esquecer
`--mode` ou rodar sem `--docs` esperando que processasse tudo) poderia
disparar chamadas reais sobre documentos que não deveriam ser testados
naquele momento — o controle de custo é o motivo, não burocracia.

Outras flags: `--manifest` (default `src/benchmark/corpus_manifest.json`),
`--cache-dir` (default `src/benchmark/cache_pdfs/`) e `--cross-review`
(`on`/`off`, default `on`).

### 4.1. Ablação: pipeline completo vs. sem leitura cruzada

`ablacao_cross_review.py` roda CADA documento DUAS vezes — uma com
`cross_review=True` (pipeline completo, 7 chamadas LLM: 3 revisores + 3
leituras cruzadas + 1 editor) e outra com `cross_review=False` (variante
experimental, 4 chamadas: 3 revisores + 1 editor) — no MESMO modo/provedor/
modelo, e grava as duas lado a lado com o delta entre elas:

```bash
.venv/bin/python -m src.benchmark.ablacao_cross_review --mode mock --docs exemplo_mock
.venv/bin/python -m src.benchmark.ablacao_cross_review --mode api --docs icd_hallucinations_2312_15710
```

Mesmas obrigatoriedades de `--mode`/`--docs` de `executar.py`, pelo mesmo
motivo — e o mesmo aviso reforçado: em modo `api`, cada `doc_id` em `--docs`
roda o pipeline **duas vezes**, o dobro do custo de uma chamada equivalente a
`executar.py`.

Saída em `resultados/ablacao_cross_review.json` (os dois registros brutos +
o bloco `delta` por documento, upsert por `doc_id`) e `.md` (tabela +
conclusão). O `delta` calcula variação percentual de chamadas LLM/duração/
tokens/custo (negativo = a variante sem leitura cruzada gastou menos) e sinaliza
se a decisão final, as notas por revisor ou a quantidade de críticas mudaram
— tudo objetivo/numérico; a leitura do TEXTO das críticas em cada
`final_report.md` (a parte qualitativa de verdade) continua manual.

---

## 5. Composição atual do corpus

Lido diretamente de `src/benchmark/corpus_manifest.json` (10 documentos):

| Área | Quantidade |
|---|---|
| `inteligencia_artificial` | 5 |
| `psicologia` | 4 |
| `smoke_test` | 1 |

Características cobertas (contagem de documentos que trazem cada uma —
um documento pode ter várias): `medio` (4), `tabelas` (3), `curto` (2),
`duas_colunas` (2), `slide` (2), `citacoes_extensas` (2),
`nao_e_artigo_academico` (2), e mais uma ocorrência cada de
`autor_unico`, `baixa_qualidade`, `escaneado`, `sem_camada_de_texto`,
`formulas_matematicas`, `longo`, `monografia_tcc`,
`nao_e_artigo_de_periodico`, `apresentacao_curso`, `smoke_test`,
`sem_pdf_real`.

Lido de `src/benchmark/resultados/execucoes.json` (10 documentos com
execução registrada — o registro mais recente de cada `doc_id`):

| `mode` | Quantidade |
|---|---|
| `api` | 8 |
| `mock` | 2 (`exemplo_mock`, e `psicologia_neuropsi_escaneado` — ver §6) |

| `resultado` | Quantidade |
|---|---|
| `sucesso` | 9 |
| `entrada_bloqueada` | 1 (`psicologia_neuropsi_escaneado`) |
| `falha_execucao` | 0 (nenhum até agora) |

`src/benchmark/resultados/execucoes/` guarda 17 arquivos de resumo — um por
execução já rodada (histórico completo), não deduplicado por documento como
`execucoes.json` (que guarda só o registro mais recente de cada `doc_id`).

---

## 6. Achados até agora

- **O bloqueio de entrada (Grupo 1) funciona corretamente para PDF
  escaneado sem camada de texto.** `psicologia_neuropsi_escaneado` (PDF
  gerado por scanner — `Producer: Epson Scan 2`, sem texto pesquisável nos
  metadados) foi bloqueado por `validar_documento_extraido` **antes de
  qualquer chamada LLM** — custo zero — com `requer_revisao_humana=true` e
  uma mensagem explicando o motivo ("a extração não produziu texto
  utilizável (possível PDF escaneado ou protegido); uma nova tentativa com
  OCR pode recuperar o conteúdo"). É a primeira vez que esse caminho foi
  exercitado com dado real, não só em teste sintético.

- **O pipeline não diferencia formato de documento.** Os dois slides do
  corpus (`psicologia_slides_modalidades_grupais`,
  `psicologia_slides_ciclo_sono` — um deck Canva, um PowerPoint de curso,
  nenhum dos dois é artigo acadêmico) passaram pela revisão completa em
  modo `api` e receberam avaliação de mérito igual a um artigo real
  (decisão "Rejeitar com ressalvas", mesmo padrão da maioria dos artigos de
  IA testados), **sem nenhum sinal** — alerta, `requer_revisao_humana`, ou
  qualquer outra coisa — indicando que o tipo de documento não era o
  esperado. O rigor dos revisores em `api` parece geral, não uma detecção
  específica de formato.

- **Contagem de páginas não é proxy confiável de volume de tokens.** A
  monografia de psicologia (`psicologia_respiracao_monografia`, 48 páginas —
  3x a maior coisa já testada antes dela) consumiu 168.648 tokens totais em
  modo `api`; o artigo `comdem_17665` (15 páginas) consumiu 124.642; o
  `icd_hallucinations_2312_15710` (15 páginas) consumiu 155.144. O volume de
  tokens de um documento de 48 páginas ficou na mesma ordem de grandeza de
  documentos de 15 — o número de chamadas LLM é estrutural (7: 3 revisores +
  3 leituras cruzadas + 1 editor, sempre, independente do tamanho do
  documento), mas o volume de tokens por chamada não escala linearmente com
  a contagem de páginas do PDF.

- **`modelo_usado` no resumo reflete o nome cru retornado pelo provedor**
  (via `model_version` do `Event` do ADK — ver
  [`docs/metricas_reference.md §5.1`](metricas_reference.md)), sem o
  prefixo `"provedor:"` usado em outros displays do pipeline (ex.: o log de
  inicialização mostra `modelo=openai:gpt-5.6-luna`, mas
  `resumo_execucao.json["modelo_usado"]` traz só `"gpt-5.6-luna"`). Isso é
  comportamento esperado do dado que o ADK devolve, não um bug do
  benchmark nem de `metrics/adk_usage.py`.
  **Correção (2026-08-06):** uma versão anterior deste documento especulava
  que `"gpt-5.6-luna"` era um gateway/proxy que reescrevia o nome do modelo,
  por não corresponder a nenhum modelo conhecido no momento da primeira
  redação. Isso estava errado — confirmado por chamada real à API (com uma
  chave de teste) e por busca na documentação oficial da OpenAI, GPT-5.6
  Luna é um modelo real e atual (lançado em 2026-07-09, a variante mais
  rápida/barata da família GPT-5.6), só posterior ao corte de conhecimento
  do agente que escreveu a versão original deste texto. `metrics/precos_modelos.py`
  (custo, §7) já tem o preço dele na tabela oficial. O PRINCÍPIO continua
  válido — precificar pelo modelo CONFIGURADO (`provider`/`model` do
  registro, §3.1), nunca pelo `modelo_usado` devolvido, ainda é o certo para
  o caso genérico de um gateway/proxy real — só o exemplo estava errado.

---

## 7. Limitações conhecidas / backlog

- **OCR desligado por padrão.** `src/extraction/extractor.py` roda com
  `ocr_enabled=False`; um PDF escaneado sem camada de texto sempre vai
  bloquear na validação (§6), nunca recuperar o conteúdo via OCR. Ligar
  isso no futuro precisa de Tesseract instalado localmente — **não precisa
  de API key** (é processamento local, não chamada a provedor de LLM).
- **`execucoes.json` não guarda `run_id` para o caso `entrada_bloqueada`.**
  O `run_id` da execução existe de verdade (`src/logs/validacao_events.jsonl`
  registra o evento de bloqueio com esse `run_id`), mas o registro do
  benchmark para esse caso não o captura/linka — por design atual de
  `executar.py`, não por perda de dado.
- **`custo_estimado` agora é calculado por padrão em modo `api`**, via
  `metrics/precos_modelos.py`: variável de ambiente > `litellm.model_cost`
  (se o pacote estiver instalado — já é dependência do projeto para
  Maritaca/OpenAI, reaproveitada aqui só para preço; não cobre Maritaca) >
  tabela de preços oficiais estática do módulo — ver
  `docs/metricas_reference.md §5.3` para a precedência completa. Continua
  `null` quando: (a) o modo é mock (nenhuma chamada LLM, nenhum token
  medido); (b) o modelo configurado não está reconhecido em NENHUMA das três
  fontes (ex.: um modelo muito novo — foi exatamente o caso do
  `"gpt-5.6-luna"` até ele ser adicionado à tabela oficial, ver o achado
  acima); (c) o provedor é de fato um gateway/proxy com tabela de preço
  PRÓPRIA, diferente da pública, e ninguém configurou o preço real por
  ambiente — nesse caso o custo calculado automaticamente pela tabela
  pública SERIA impreciso, então vale configurar
  `GRUPO2_PRECO_USD_MILHAO_TOKENS_ENTRADA`/`_SAIDA` manualmente antes de
  rodar.
- **Os 8 registros `api` já commitados em `execucoes.json` são anteriores a
  esta ativação** — não têm `custo_estimado` retroativo (o preço não foi
  aplicado sobre uma execução passada, só é calculado NA hora da execução).
  Re-rodar esses documentos com `executar.py` preencheria o custo; isso não
  foi feito automaticamente aqui para não gastar API sem pedido explícito.
