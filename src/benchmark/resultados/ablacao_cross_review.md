# Ablação: pipeline completo vs. sem leitura cruzada (Grupo 2)

Gerado em: 2026-08-06T21:29:51.519842+00:00

## Conclusão

6 documento(s) comparável(is) (sucesso nos dois lados) de 6 rodado(s).
Chamadas LLM: -42.9% em média sem leitura cruzada (esperado -43% estrutural: 4 chamadas em vez de 7).
Duração total: -17.5% em média sem leitura cruzada.
Tokens totais: -47.7% em média sem leitura cruzada.
Custo estimado: -41.9% em média sem leitura cruzada.
Decisão final MUDOU em 1/6 documento(s): arxiv_2606_00819.
Quantidade de críticas: variação média de +2.3 crítica(s) ao desativar a leitura cruzada.
Leitura sugerida: se a decisão final e a quantidade de críticas NÃO mudam entre as duas variantes, a leitura cruzada está pagando custo/tempo adicionais sem alterar o resultado nestes documentos — o valor dela, se houver, está na qualidade argumentativa da resposta aos pares (texto de 'resposta_aos_pares' em cada final_report.md), não capturada numericamente aqui.

## Tabela

| doc_id | provider:model | chamadas (com/sem) | duração_s (com/sem) | Δduração | tokens (com/sem) | Δtokens | custo USD (com/sem) | Δcusto | decisão (com/sem) | críticas (com/sem) |
|---|---|---|---|---|---|---|---|---|---|---|
| acl_emnlp2024_116 | openai:gpt-5.6-luna | 7/4 | 36.0511250999989/26.973181100009242 | -25.2% | 115533/61612 | -46.7% | 0.0335826/0.019246399999999997 | -42.7% | 2/2 | 51/44 |
| arxiv_2606_00819 | openai:gpt-5.6-luna | 7/4 | 34.298723100015195/27.956102199997986 | -18.5% | 93294/49201 | -47.3% | 0.0292088/0.0170762 | -41.5% | 2/1 | 33/42 |
| comdem_17665 | openai:gpt-5.6-luna | 7/4 | 36.72246590000577/32.78738299998804 | -10.7% | 123708/66156 | -46.5% | 0.0360886/0.020640199999999997 | -42.8% | 2/2 | 38/47 |
| exemplo_mock | None:None | None/None | 0.012073799996869639/0.012034100014716387 | -0.3% | None/None | n/d | None/None | n/d | 3/3 | 5/5 |
| icd_hallucinations_2312_15710 | openai:gpt-5.6-luna | 7/4 | 36.701086999994004/28.937941800017143 | -21.2% | 156062/85153 | -45.4% | 0.041552399999999996/0.0240936 | -42.0% | 2/2 | 43/43 |
| psicologia_slides_ciclo_sono | openai:gpt-5.6-luna | 7/4 | 33.91491439999663/24.075644600001397 | -29.0% | 36713/17445 | -52.5% | 0.0165966/0.009902999999999999 | -40.3% | 2/2 | 49/52 |
