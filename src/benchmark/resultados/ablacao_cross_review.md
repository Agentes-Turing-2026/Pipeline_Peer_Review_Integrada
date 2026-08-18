# Ablação: pipeline completo vs. sem leitura cruzada (Grupo 2)

Gerado em: 2026-08-13T16:32:11.814088+00:00

## Conclusão

EXECUÇÕES REAIS (modo api): 5 documento(s) comparável(is) (sucesso nos dois lados) de 5 rodado(s) — 10 execuções reais de pipeline no total.
Chamadas LLM: -42.9% em média sem leitura cruzada (esperado -43% estrutural: 4 chamadas em vez de 7).
Duração total: -20.9% em média sem leitura cruzada.
Tokens totais: -47.7% em média sem leitura cruzada.
Custo estimado: -41.9% em média sem leitura cruzada.
Decisão final MUDOU em 1/5 documento(s): arxiv_2606_00819.
Quantidade de críticas: variação média de +2.8 crítica(s) ao desativar a leitura cruzada.
SMOKE TEST (modo mock, sem chamada LLM e sem custo): 1 documento(s) — exemplo_mock. Serve só para provar que a ferramenta roda de ponta a ponta de graça; não entra em nenhuma média acima.
Leitura sugerida: se a decisão final e a quantidade de críticas NÃO mudam entre as duas variantes, a leitura cruzada está pagando custo/tempo adicionais sem alterar o resultado nestes documentos — o valor dela, se houver, está na qualidade argumentativa da resposta aos pares (texto de 'resposta_aos_pares' em cada final_report.md), não capturada numericamente aqui.
LIMITE DESTA AVALIAÇÃO: 'qualidade' aqui é medida por indicadores automáticos (quantidade de críticas, quantas são bloqueantes, mudança na decisão final e nas notas por revisor). NÃO houve avaliação humana do conteúdo das críticas — nenhuma pessoa leu os pareceres para julgar se são pertinentes ou bem argumentados. Os números abaixo dizem quanto a leitura cruzada CUSTA e se ela MUDA o resultado, não se ela o MELHORA.

## Execuções reais (modo api)

5 documento(s), cada um rodado 2x (com e sem leitura cruzada) = 10 execuções reais de pipeline, com chamadas LLM, tokens e custo medidos.

| doc_id | provider:model | chamadas (com/sem) | duração_s (com/sem) | Δduração | tokens (com/sem) | Δtokens | custo USD (com/sem) | Δcusto | decisão (com/sem) | críticas (com/sem) |
|---|---|---|---|---|---|---|---|---|---|---|
| acl_emnlp2024_116 | openai:gpt-5.6-luna | 7/4 | 36.05/26.97 | -25.2% | 115533/61612 | -46.7% | 0.0336/0.0192 | -42.7% | 2/2 | 51/44 |
| arxiv_2606_00819 | openai:gpt-5.6-luna | 7/4 | 34.30/27.96 | -18.5% | 93294/49201 | -47.3% | 0.0292/0.0171 | -41.5% | 2/1 | 33/42 |
| comdem_17665 | openai:gpt-5.6-luna | 7/4 | 36.72/32.79 | -10.7% | 123708/66156 | -46.5% | 0.0361/0.0206 | -42.8% | 2/2 | 38/47 |
| icd_hallucinations_2312_15710 | openai:gpt-5.6-luna | 7/4 | 36.70/28.94 | -21.2% | 156062/85153 | -45.4% | 0.0416/0.0241 | -42.0% | 2/2 | 43/43 |
| psicologia_slides_ciclo_sono | openai:gpt-5.6-luna | 7/4 | 33.91/24.08 | -29.0% | 36713/17445 | -52.5% | 0.0166/0.0099 | -40.3% | 2/2 | 49/52 |

## Smoke test (modo mock — sem chamada LLM, sem custo)

Não é evidência sobre o efeito da leitura cruzada: em modo mock os pareceres vêm de um JSON pré-salvo, nenhuma chamada LLM acontece e não há tokens nem custo para medir. Serve para provar que a ferramenta roda de ponta a ponta de graça. **Estes números não entram em nenhuma média da seção anterior.**

| doc_id | provider:model | chamadas (com/sem) | duração_s (com/sem) | Δduração | tokens (com/sem) | Δtokens | custo USD (com/sem) | Δcusto | decisão (com/sem) | críticas (com/sem) |
|---|---|---|---|---|---|---|---|---|---|---|
| exemplo_mock | None:None | None/None | 0.01/0.01 | -0.3% | None/None | n/d | n/d/n/d | n/d | 3/3 | 5/5 |
