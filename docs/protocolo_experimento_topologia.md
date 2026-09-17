# Protocolo do experimento — topologia: multiagente vs. agente único (Grupo 3)

Este documento registra o protocolo do experimento que compara a arquitetura
multiagente de peer review (revisão independente + leitura cruzada +
editor-chefe) com uma baseline experimental de **agente único**: um só agente
que lê o artigo e produz o veredito final diretamente, sem revisores
especializados nem leitura cruzada.

O objetivo é ter um registro reprodutível de **hipótese, condições, métricas e
procedimento** antes de rodar o experimento em escala — não um relatório de
resultados (esses ficam em `src/benchmark/resultados/comparativo_topologia.{json,md}`,
gerados por `src/benchmark/comparar_topologia.py`, ver §6).

---

## 1. Motivação

O pipeline de peer review (`src/pipeline.py`) usa, por padrão, uma topologia
multiagente: 3 revisores especializados avaliam o artigo de forma
independente (Fase 1), leem os argumentos uns dos outros (Fase 2) e um
editor-chefe sintetiza tudo em um veredito final (Fase 3) — 7 chamadas LLM no
total (3 + 3 + 1). Essa estrutura tem um custo claro (tempo, tokens, chamadas
de API) frente a uma alternativa mais simples: um único agente que lê o
artigo inteiro e já devolve o veredito final, em 1 chamada LLM.

A pergunta que este experimento tenta responder: **o que a topologia
multiagente entrega a mais, e isso justifica o custo adicional?**

## 2. Hipótese

**H1 (estrutural, não precisa de dado para confirmar):** a topologia
`agente_unico` consome menos chamadas LLM, tokens, tempo e custo estimado que
a `multiagente`, na proporção aproximada de 1 chamada contra 7.

**H2 (empírica, é o que o experimento mede):** a topologia `multiagente`
produz uma decisão editorial e/ou uma quantidade de críticas
sistematicamente diferente da `agente_unico` para o mesmo artigo — indício de
que a especialização dos revisores e a leitura cruzada mudam o resultado, e
não só o custo para chegar nele.

Este experimento **não** testa se as críticas da topologia multiagente são
qualitativamente melhores (mais pertinentes, mais bem argumentadas) — isso
exigiria avaliação humana ou um LLM-juiz, fora do escopo aqui (ver §5).

## 3. Variáveis

### 3.1. Variável independente: topologia

Configurada por `pipeline.run_demo(single_agent=...)` (também exposta em
`main.py --single-agent` e `src/benchmark/executar.py --single-agent`):

| Valor | Topologia | Fases de LLM | Chamadas LLM |
|---|---|---|---|
| `single_agent=False` (default) | `multiagente` | Revisão independente + leitura cruzada + editor-chefe (`build_peer_review_pipeline`) | 7 (3 revisores + 3 leituras cruzadas + 1 editor) |
| `single_agent=True` | `agente_unico` | Fase única (`build_single_agent_pipeline`, `src/single_agent_baseline.py`) | 1 |

As duas topologias produzem a mesma saída oficial (`EditorVerdictSchema`),
para que relatório final e métricas comparem o mesmo contrato — ver §4.

### 3.2. Variáveis controladas (mantidas fixas entre as duas execuções do par)

- **Documento:** o mesmo artigo/PDF nas duas execuções.
- **Modo:** `api` (execução real) ou `mock` (smoke test, sem chamada LLM —
  ver §5.2).
- **Provedor/modelo:** o mesmo `LLM_PROVIDER`/`LLM_MODEL` (`model_provider.py`)
  nas duas execuções. A baseline de agente único aceita sobrescrita própria
  (`LLM_PROVIDER_SINGLE_AGENT`/`LLM_MODEL_SINGLE_AGENT`) — para este
  experimento ela **não** é usada, exatamente para isolar o efeito da
  topologia do efeito de trocar de modelo.

### 3.3. Variáveis dependentes (o que é medido)

Vêm de `metrics/resumo.py` (`ResumoExecucao`) e do veredito
(`EditorVerdictSchema`), já expostos por `executar.processar_documento`:

| Métrica | Fonte | Uso na comparação |
|---|---|---|
| `chamadas_llm` | resumo | Confirma H1 (estrutural). |
| `duracao_total_s` | resumo | Custo em tempo. |
| `tokens_totais` | resumo | Custo em tokens. |
| `custo_estimado` | resumo (requer preço configurado — ver `docs/metricas_reference.md`) | Custo em USD. |
| `decisao_final` (escala 1-4) | veredito | Sinal principal de H2 — é a única grandeza diretamente comparável entre as duas topologias (mesma escala nas duas). |
| `quantidade_criticas` / `quantidade_criticas_bloqueantes` | veredito | Sinal secundário de H2 — quantas críticas cada topologia levanta. |
| `requer_revisao_humana` | auditoria do veredito | Se a auditoria (`tools/auditar_decisao_final.py`) muda de recomendação entre as duas topologias. |

`notas_por_revisor` **não** entra na comparação: na topologia `multiagente`
é um mapa com 3 chaves (uma por revisor especializado); na `agente_unico` é
um mapa com 1 chave sintética (`agente_unico`). Comparar os dois diretamente
não teria significado — ver `AGENTE_UNICO_ID` em `src/single_agent_baseline.py`.

## 4. Mecanismo de configuração

A topologia é uma **flag simples** (`single_agent: bool`), não um conceito
geral de "topologia" com múltiplos eixos — decisão deliberada para manter o
mecanismo fácil de usar em testes pontuais:

- `pipeline.run_demo(single_agent=True|False)` — ponto de entrada único.
- `main.py --single-agent` — CLI de execução avulsa (`python main.py mock --single-agent`).
- `src/benchmark/executar.py --single-agent` — lote do corpus (registra sob a
  chave `<doc_id>__agente_unico` em `resultados/execucoes.json`, para não
  sobrescrever o registro multiagente do mesmo documento).
- `src/benchmark/comparar_topologia.py` — roda o par (multiagente +
  agente_unico) para os mesmos documentos e gera o comparativo (ver §6).

A flag `single_agent` é independente de `cross_review`: este último só se
aplica DENTRO da topologia multiagente (liga/desliga a Fase 2); a topologia
`agente_unico` nunca tem Fase 2, com ou sem essa flag.

## 5. Limitações

### 5.1. Qualidade é medida por proxy, não por julgamento humano

"Qualidade" aqui é `decisao_final` + contagem de críticas — indicadores
automáticos, no mesmo espírito de `src/benchmark/ablacao_cross_review.py`
(que faz a mesma ressalva para a comparação com/sem leitura cruzada). Nenhuma
pessoa leu os pareceres para julgar se as críticas da topologia multiagente
são mais pertinentes ou mais bem fundamentadas que as do agente único.

### 5.2. Modo mock é smoke test, não evidência

Em modo `mock`, nenhuma chamada LLM acontece — os pareceres/veredito vêm de
`src/mocks/peer_review_mock.json` (chaves `phase1_reviews`/`phase2_cross_reviews`/
`phase3_verdict` para a topologia multiagente, `single_agent_verdict` para a
baseline). Serve só para provar que as duas topologias rodam de ponta a ponta
sem gastar API; não entra em nenhuma média do comparativo (ver
`comparar_topologia.separar_por_modo`).

### 5.3. Tamanho de amostra

O corpus de benchmark (`src/benchmark/corpus_manifest.json`) é a fonte dos
documentos reais usados no experimento (ver `docs/benchmark_reference.md`).
Conclusões estatisticamente robustas exigem rodar o comparativo sobre o
corpus inteiro (ou uma amostra representativa dele), não sobre 1-2
documentos — o comparativo aceita qualquer subconjunto via `--docs`, mas a
generalização da conclusão depende de quantos documentos entraram nela.

## 6. Procedimento

1. **Smoke test (obrigatório antes de gastar API):**
   ```bash
   python -m src.benchmark.comparar_topologia --mode mock --docs exemplo_mock
   ```
   Confirma que as duas topologias rodam de ponta a ponta e que o script de
   comparação produz `resultados/comparativo_topologia.{json,md}`.

2. **Execução real, documento a documento ou em lote:**
   ```bash
   python -m src.benchmark.comparar_topologia --mode api --docs doc_1,doc_2,...
   ```
   Cada documento roda DUAS vezes (multiagente + agente único) — o dobro do
   custo de `executar.py` para a mesma lista. Sequencial (nunca em paralelo),
   mesmo motivo de `executar.py`/`ablacao_cross_review.py`: não estourar rate
   limit e manter o custo previsível.

3. **Regerar o relatório sem rodar o pipeline de novo** (depois de mudar a
   lógica de agregação/relatório):
   ```bash
   python -m src.benchmark.comparar_topologia --regerar
   ```

4. **Leitura dos resultados:** `resultados/comparativo_topologia.md` traz a
   conclusão calculada (não redigida à mão) + a tabela pareada por documento;
   o `.json` irmão traz os registros completos de cada execução (mesmo
   formato de `executar.processar_documento`) para quem quiser reprocessar os
   números com outra agregação.

5. **Conclusão qualitativa (fora do escopo automatizado):** ler o texto das
   críticas em cada `final_report.md` das execuções pareadas e registrar, à
   parte, se a topologia multiagente levanta pontos que o agente único não
   levanta — a ferramenta não faz esse julgamento (ver §5.1).

## 7. Referências

- `src/single_agent_baseline.py` — agente único (baseline).
- `src/pipeline.py` — `SingleAgentVerdictPhase`, `SingleAgentReportPhase`,
  `build_single_agent_pipeline`, `run_demo(single_agent=...)`.
- `src/benchmark/comparar_topologia.py` — comparativo pareado.
- `src/benchmark/ablacao_cross_review.py` — comparativo pareado equivalente
  para o eixo "leitura cruzada" (modelo seguido por este protocolo).
- `docs/benchmark_reference.md` — corpus e infraestrutura de benchmark
  reaproveitados aqui.
- `docs/metricas_reference.md` — `ResumoExecucao` e custo estimado.
