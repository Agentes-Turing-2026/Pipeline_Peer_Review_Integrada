# Grupo 2 — Benchmark, Custo e Eficiência

Este arquivo é o resumo de entrada da entrega do Grupo 2. Para o relatório
técnico completo, ver
[`docs/relatorio_benchmark_custo_eficiencia.md`](docs/relatorio_benchmark_custo_eficiencia.md).

Histórico: entregue em `grupo-2-custo-config-e-comparacao-cross-review`
(PR #16), custo por chamada em `grupo-2-custo-por-chamada-modelo` (PR #18),
correções de documentação/corpus/CI em `grupo-2-corpus-publico-docs-e-ci`.

## A task

1. O peer review já capturava tokens/duração/chamadas/retries/falhas, mas o
   campo de custo continuava vazio — os preços dos modelos não estavam
   sendo usados na geração do resumo.
2. O benchmark já tinha dez documentos, mas os resultados não identificavam
   de forma completa o provedor, o modelo e a configuração usados em cada
   execução.
3. O fluxo completo faz sete chamadas de LLM (três revisões independentes,
   três leituras cruzadas, uma decisão do editor) e não havia como medir o
   quanto a leitura cruzada melhora a resposta frente ao custo/tempo
   adicionais que ela introduz.
4. Entregar uma tabela e uma conclusão comparando os mesmos PDFs/modelos com
   e sem leitura cruzada (tokens, duração, custo, qualidade da decisão).

## O que foi implementado nesta branch

- **`src/metrics/precos_modelos.py`** (novo) — ativa `custo_estimado`, antes
  sempre `null`. Resolve o preço por precedência: variável de ambiente >
  `litellm.model_cost` (se instalado) > tabela de preços oficiais estática.
  Conectado a `gerar_resumo()` em `src/pipeline.py`.
- **`src/benchmark/executar.py`** — cada execução do benchmark passa a
  registrar `provider`, `model`, `structured_output`, `cross_review_enabled`
  e sinais de qualidade (`notas_por_revisor`, `quantidade_criticas`).
- **`pipeline.run_demo(cross_review=False)`** — variante experimental que
  roda a Fase 2 (leitura cruzada) sem nenhuma chamada LLM (4 chamadas em vez
  de 7), exposta via `python main.py --no-cross-review` e
  `executar.py --cross-review off`.
- **`src/benchmark/ablacao_cross_review.py`** (novo) — roda cada documento
  duas vezes (com/sem leitura cruzada) e gera tabela + conclusão em
  `src/benchmark/resultados/ablacao_cross_review.{json,md}`.
- **44 testes novos**, suíte completa (283 testes) passando.

## Validação real feita nesta branch

Com uma chave real da OpenAI (fornecida pelo usuário só para teste, nunca
commitada — fica em `.env`, que é gitignored), foram rodados os 5
documentos abaixo, duas vezes cada (com e sem leitura cruzada), modelo
`gpt-5.6-luna` (modelo real da OpenAI, lançado em 2026-07-09 — não é um
gateway/proxy, como uma suposição anterior errada tinha documentado; ver
correção em `docs/benchmark_reference.md §6`).

As **10 execuções são reais**: chamadas de verdade à API da OpenAI, com
tokens medidos pelo `usage_metadata` do ADK. O que **não** é medido é o
custo em dinheiro.

> **Custo total ESTIMADO da comparação: US$ 0,248** (10 execuções reais).
>
> "Estimado", não "real": este valor não vem da fatura da OpenAI nem de
> nenhuma API de billing. Ele é calculado por
> [`src/metrics/precos_modelos.py`](src/metrics/precos_modelos.py)
> multiplicando os **tokens realmente consumidos** pela **tabela de preços
> por milhão de tokens** documentada no módulo. Divergências com a cobrança
> real são esperadas quando o preço de tabela estiver desatualizado, quando
> houver desconto/crédito na conta, ou quando o provedor cobrar itens que os
> tokens não capturam (cache, ferramentas, requisições mínimas). O que é
> medido é o consumo; o preço é configuração — e os dois nunca se misturam
> no código (ver `docs/metricas_reference.md §5.3`).

Desde o [PR #18](https://github.com/Agentes-Turing-2026/Pipeline_Peer_Review_Integrada/pull/18),
o custo é somado **chamada a chamada**, cada uma precificada pelo modelo que
de fato respondeu — e não mais por um preço único aplicado ao total agregado
da execução. Isso importa quando o editor usa um modelo diferente do resto do
pipeline ou quando o fallback do Grupo 3 troca de modelo no meio da execução.
Nas 10 execuções abaixo todas as chamadas foram no mesmo modelo, então o total
coincide com o que o cálculo antigo daria; a diferença aparece em execuções
com mais de um modelo.

| Documento | Chamadas | Duração (s) | Tokens | Custo (US$) | Decisão | Críticas (total/bloqueantes) |
|---|---|---|---|---|---|---|
| icd_hallucinations_2312_15710 | 7 → 4 | 36,70 → 28,94 | 156.062 → 85.153 | 0,0416 → 0,0241 | 2 → 2 | 43/18 → 43/11 |
| acl_emnlp2024_116 | 7 → 4 | 36,05 → 26,97 | 115.533 → 61.612 | 0,0336 → 0,0192 | 2 → 2 | 51/38 → 44/24 |
| arxiv_2606_00819 | 7 → 4 | 34,30 → 27,96 | 93.294 → 49.201 | 0,0292 → 0,0171 | **2 → 1** | 33/23 → 42/22 |
| comdem_17665 | 7 → 4 | 36,72 → 32,79 | 123.708 → 66.156 | 0,0361 → 0,0206 | 2 → 2 | 38/19 → 47/23 |
| psicologia_slides_ciclo_sono | 7 → 4 | 33,91 → 24,08 | 36.713 → 17.445 | 0,0166 → 0,0099 | 2 → 2 | 49/19 → 52/24 |
| **Média da variação** | **-42,9%** | **-20,9%** | **-47,7%** | **-41,9%** | 1/5 mudou | sem padrão claro |

### Conclusão

- Desativar a leitura cruzada corta as chamadas LLM de 7 para 4 (-42,9%,
  exato em todos os documentos), reduzindo tokens (~48%), duração (~21%) e
  custo (~42%) de forma consistente.
- A decisão final mudou em 1 dos 5 documentos (`arxiv_2606_00819`): com
  leitura cruzada, decisão 2 (Rejeitar com ressalvas); sem ela, decisão 1
  (Rejeitar) — mais severa. Nos outros 4, a decisão final ficou idêntica.
- A quantidade de críticas não tem um padrão claro (subiu em 3 documentos,
  caiu em 1, ficou igual em 1) — não dá pra dizer que a leitura cruzada
  sistematicamente aumenta ou diminui o número de críticas levantadas.
- Na maioria dos casos (4/5), a leitura cruzada custou ~42% a mais em
  tokens/dinheiro sem mudar a decisão final. No único caso em que mudou,
  ela tornou o veredito mais brando — sugerindo que o valor da leitura
  cruzada, quando existe, está em moderar posições isoladas mais severas
  dos revisores, não em produzir mais críticas.

> **Limite desta avaliação.** Tudo acima é medido por **indicadores
> automáticos**: quantidade de críticas, quantas são bloqueantes, se a
> decisão final mudou e se as notas por revisor mudaram. **Nenhum avaliador
> humano leu o conteúdo das críticas** para julgar se são pertinentes,
> corretas ou bem argumentadas, e não houve LLM-juiz. Ou seja: está medido
> o quanto a leitura cruzada **custa** e se ela **muda** o resultado — não
> se ela o **melhora**. Responder isso exige leitura humana dos pareceres
> (texto de `resposta_aos_pares` em cada `final_report.md`), que fica como
> próximo passo.

Dados completos em [`src/benchmark/resultados/ablacao_cross_review.json`](src/benchmark/resultados/ablacao_cross_review.json)
e [`.md`](src/benchmark/resultados/ablacao_cross_review.md), onde as
execuções reais e o smoke test em modo mock aparecem em **seções separadas**
— o mock não entra em nenhuma média.

> **Nota de corpus.** O documento `psicologia_slides_ciclo_sono`, usado numa
> destas 5 comparações, era um PDF **local** e foi aposentado do
> `corpus_manifest.json` em 13/08/2026, quando o corpus passou a ser
> inteiramente público. O registro da execução continua no
> `ablacao_cross_review.json` (não se apaga resultado de execução paga), mas
> esse documento específico não é mais reprodutível por terceiros; os outros
> 4 são.

## Como rodar

```bash
# Ativa custo automaticamente:
python main.py api

# Variante sem leitura cruzada:
python main.py api --no-cross-review

# Comparação pareada (tabela + conclusão) — cada doc_id roda 2x:
python -m src.benchmark.ablacao_cross_review --mode api --docs id1,id2,...

# Testes desta entrega:
python -m pytest src/metrics/tests/test_precos_modelos.py src/benchmark/tests/ src/tests/test_cross_review_ablation.py -v
```

## Documentação completa

- [`docs/relatorio_benchmark_custo_eficiencia.md`](docs/relatorio_benchmark_custo_eficiencia.md) — relatório técnico detalhado (checklist, arquitetura, testes).
- [`docs/metricas_reference.md`](docs/metricas_reference.md) — schema de métricas e precedência de custo.
- [`docs/benchmark_reference.md`](docs/benchmark_reference.md) — schema do benchmark, achados e limitações.
