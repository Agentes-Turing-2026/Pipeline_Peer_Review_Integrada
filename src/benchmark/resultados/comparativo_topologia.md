# Comparativo de topologia: multiagente (peer review) vs. agente único

Gerado em: 2026-09-17T18:45:23.416060+00:00

## Conclusão

Nenhuma execução REAL (modo api) com sucesso nas duas topologias — sem base para uma conclusão numérica.
SMOKE TEST (modo mock, sem chamada LLM e sem custo): 1 documento(s) — exemplo_mock. Serve só para provar que a ferramenta roda de ponta a ponta de graça; não entra em nenhuma média acima.

## Execuções reais (modo api)

Nenhuma execução real registrada até agora.

## Smoke test (modo mock — sem chamada LLM, sem custo)

Não é evidência sobre o efeito da topologia: em modo mock os pareceres/veredito vêm de um JSON pré-salvo, nenhuma chamada LLM acontece e não há tokens nem custo para medir. Serve para provar que a ferramenta roda de ponta a ponta de graça. **Estes números não entram em nenhuma média da seção anterior.**

| doc_id | provider:model | chamadas (multi/único) | duração_s (multi/único) | Δduração | tokens (multi/único) | Δtokens | custo USD (multi/único) | Δcusto | decisão (multi/único) | críticas (multi/único) |
|---|---|---|---|---|---|---|---|---|---|---|
| exemplo_mock | None:None | None/None | 0.02/0.01 | -64.3% | None/None | n/d | n/d/n/d | n/d | 3/3 | 5/3 |
