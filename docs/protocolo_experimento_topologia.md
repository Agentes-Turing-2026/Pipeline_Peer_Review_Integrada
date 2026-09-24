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

Configurada por `pipeline.run_demo(single_agent=..., cross_review=...)`
(também exposta em `main.py --single-agent`/`--no-cross-review` e em
`src/benchmark/executar.py`). O comparativo (`comparar_topologia.py`) roda as
três combinações abaixo para cada documento:

| Config | Flags | Topologia | Fases de LLM | Chamadas LLM |
|---|---|---|---|---|
| C0 | `single_agent=True` | `agente_unico` | Fase única (`build_single_agent_pipeline`, `src/single_agent_baseline.py`) | 1 |
| C1 | `single_agent=False`, `cross_review=False` | `multiagente` sem leitura cruzada | Revisão independente + editor-chefe (mesma variante de `ablacao_cross_review.py`) | 4 (3 revisores + 1 editor) |
| C2 | `single_agent=False`, `cross_review=True` (default) | `multiagente` completo | Revisão independente + leitura cruzada + editor-chefe (`build_peer_review_pipeline`) | 7 (3 revisores + 3 leituras cruzadas + 1 editor) |

As três topologias produzem a mesma saída oficial (`EditorVerdictSchema`),
para que relatório final e métricas comparem o mesmo contrato — ver §4. O
comparativo calcula dois saltos entre elas — **Salto 1** (C0 → C1) e
**Salto 2** (C1 → C2) — mais o total (C0 → C2).

### 3.2. Variáveis controladas (mantidas fixas entre as três execuções de cada documento)

- **Documento:** o mesmo artigo/PDF nas três execuções.
- **Modo:** `api` (execução real) ou `mock` (smoke test, sem chamada LLM —
  ver §5.2).
- **Provedor/modelo:** o mesmo `LLM_PROVIDER`/`LLM_MODEL` (`model_provider.py`)
  nas três execuções. A baseline de agente único aceita sobrescrita própria
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
| `decisao_final` (escala 1-4) | veredito | Sinal principal de H2 — é a única grandeza diretamente comparável entre as três topologias (mesma escala nas três). |
| `quantidade_criticas` / `quantidade_criticas_bloqueantes` | veredito | Sinal secundário de H2 — quantas críticas cada topologia levanta. |
| `requer_revisao_humana` | auditoria do veredito | Se a auditoria (`tools/auditar_decisao_final.py`) muda de recomendação entre topologias. |
| `notas_por_revisor` | veredito | Só no Salto 2 (C1 → C2) — se as notas dos três revisores mudam com a leitura cruzada. |

`notas_por_revisor` só é comparável entre C1 e C2, que têm os mesmos 3
revisores especializados. Na `agente_unico` (C0) ela é um mapa com 1 chave
sintética (`agente_unico`), então fica de fora do Salto 1 e do total —
comparar esses formatos não teria significado (ver `AGENTE_UNICO_ID` em
`src/single_agent_baseline.py`).

## 4. Mecanismo de configuração

A topologia é uma **flag simples** (`single_agent: bool`), não um conceito
geral de "topologia" com múltiplos eixos — decisão deliberada para manter o
mecanismo fácil de usar em testes pontuais:

- `pipeline.run_demo(single_agent=True|False)` — ponto de entrada único.
- `main.py --single-agent` — CLI de execução avulsa (`python main.py mock --single-agent`).
- `src/benchmark/executar.py --single-agent` — lote do corpus (registra sob a
  chave `<doc_id>__agente_unico` em `resultados/execucoes.json`, para não
  sobrescrever o registro multiagente do mesmo documento).
- `src/benchmark/comparar_topologia.py` — roda as três configurações (C0,
  C1 e C2, ver §3.1) para os mesmos documentos e gera o comparativo (ver §6).

A flag `single_agent` é independente de `cross_review`: este último só se
aplica DENTRO da topologia multiagente (liga/desliga a Fase 2); a topologia
`agente_unico` nunca tem Fase 2, com ou sem essa flag. É a combinação das
duas flags que gera as três configurações do comparativo.

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
baseline). Serve só para provar que as três topologias rodam de ponta a ponta
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
   Confirma que as três topologias rodam de ponta a ponta e que o script de
   comparação produz `resultados/comparativo_topologia.{json,md}`.

2. **Execução real, documento a documento ou em lote:**
   ```bash
   python -m src.benchmark.comparar_topologia --mode api --docs doc_1,doc_2,...
   ```
   Cada documento roda TRÊS vezes (C0, C1 e C2) — o triplo do custo de
   `executar.py` para a mesma lista. Sequencial (nunca em paralelo),
   mesmo motivo de `executar.py`/`ablacao_cross_review.py`: não estourar rate
   limit e manter o custo previsível.

3. **Regerar o relatório sem rodar o pipeline de novo** (depois de mudar a
   lógica de agregação/relatório):
   ```bash
   python -m src.benchmark.comparar_topologia --regerar
   ```
   Pares gravados no formato antigo (pareado, C0 vs. C2, de antes do
   comparativo virar três vias) não entram no comparativo: são movidos para
   `resultados/comparativo_topologia_legado.json`, sem perder o dado, e
   precisam ser rodados de novo com `--docs` para ganhar a execução C1.

4. **Leitura dos resultados:** `resultados/comparativo_topologia.md` traz a
   conclusão calculada (não redigida à mão), com Salto 1, Salto 2 e total,
   + uma tabela com as três topologias lado a lado por documento; o `.json`
   irmão traz os registros completos de cada execução (mesmo formato de
   `executar.processar_documento`) e os deltas de cada salto, para quem
   quiser reprocessar os números com outra agregação.

5. **Conclusão qualitativa (fora do escopo automatizado):** ler o texto das
   críticas em cada `final_report.md` das três execuções de cada documento e
   registrar, à parte, o que cada salto de especialização levanta que a
   configuração anterior não levanta — a ferramenta não faz esse julgamento
   (ver §5.1).

## 7. Referências

- `src/single_agent_baseline.py` — agente único (baseline).
- `src/pipeline.py` — `SingleAgentVerdictPhase`, `SingleAgentReportPhase`,
  `build_single_agent_pipeline`, `run_demo(single_agent=...)`.
- `src/benchmark/comparar_topologia.py` — comparativo das três topologias
  (C0, C1, C2).
- `src/benchmark/ablacao_cross_review.py` — comparativo pareado dedicado só
  ao eixo "leitura cruzada" (C1 vs. C2); a variante C1 usada aqui é a mesma
  de lá.
- `docs/benchmark_reference.md` — corpus e infraestrutura de benchmark
  reaproveitados aqui.
- `docs/metricas_reference.md` — `ResumoExecucao` e custo estimado.
