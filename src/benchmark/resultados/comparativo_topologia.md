# Comparativo de topologia: multiagente (peer review) vs. agente único

Gerado em: 2026-09-17T19:34:57.099134+00:00

## Conclusão

EXECUÇÕES REAIS (modo api): 1 documento(s) comparável(is) (sucesso nas duas topologias) de 1 rodado(s) — 2 execuções reais de pipeline no total.
Chamadas LLM: -85.7% em média no agente único (esperado -86% estrutural: 1 chamada em vez de 7).
Duração total: -74.4% em média no agente único.
Tokens totais: -86.6% em média no agente único.
Custo estimado: sem dado (sem preço configurado/tokens medidos).
Decisão final MUDOU em 1/1 documento(s): icd_hallucinations_2312_15710.
Quantidade de críticas: variação média de +1.0 crítica(s) no agente único frente ao multiagente.
SMOKE TEST (modo mock, sem chamada LLM e sem custo): 1 documento(s) — exemplo_mock. Serve só para provar que a ferramenta roda de ponta a ponta de graça; não entra em nenhuma média acima.
Leitura sugerida: se a decisão final e a quantidade de críticas NÃO mudam entre as duas topologias, a estrutura multiagente está pagando custo/tempo adicionais sem alterar o resultado nestes documentos — o valor dela, se houver, está na qualidade argumentativa e na especialização das críticas (texto de cada crítica em cada final_report.md), não capturada numericamente aqui.
LIMITE DESTA AVALIAÇÃO: 'qualidade' aqui é medida por indicadores automáticos (decisão final na escala 1-4, quantidade de críticas, quantas são bloqueantes). NÃO houve avaliação humana do conteúdo das críticas — nenhuma pessoa leu os pareceres para julgar se são pertinentes ou bem argumentados, nem se a topologia multiagente produz críticas mais específicas ou melhor fundamentadas que o agente único. 'notas_por_revisor' não é comparável entre as topologias (formatos diferentes) e não entra no comparativo. Os números abaixo dizem quanto a topologia multiagente CUSTA a mais e se ela MUDA o resultado, não se ela o MELHORA.

## Execuções reais (modo api)

1 documento(s), cada um rodado 2x (multiagente e agente único) = 2 execuções reais de pipeline, com chamadas LLM, tokens e custo medidos.

| doc_id | provider:model | chamadas (multi/único) | duração_s (multi/único) | Δduração | tokens (multi/único) | Δtokens | custo USD (multi/único) | Δcusto | decisão (multi/único) | críticas (multi/único) |
|---|---|---|---|---|---|---|---|---|---|---|
| icd_hallucinations_2312_15710 | gemini:gemini-3-flash-preview | 7/1 | 53.46/13.70 | -74.4% | 153937/20643 | -86.6% | n/d/n/d | n/d | 4/3 | 3/4 |

## Smoke test (modo mock — sem chamada LLM, sem custo)

Não é evidência sobre o efeito da topologia: em modo mock os pareceres/veredito vêm de um JSON pré-salvo, nenhuma chamada LLM acontece e não há tokens nem custo para medir. Serve para provar que a ferramenta roda de ponta a ponta de graça. **Estes números não entram em nenhuma média da seção anterior.**

| doc_id | provider:model | chamadas (multi/único) | duração_s (multi/único) | Δduração | tokens (multi/único) | Δtokens | custo USD (multi/único) | Δcusto | decisão (multi/único) | críticas (multi/único) |
|---|---|---|---|---|---|---|---|---|---|---|
| exemplo_mock | None:None | None/None | 0.02/0.01 | -64.3% | None/None | n/d | n/d/n/d | n/d | 3/3 | 5/3 |
