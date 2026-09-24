# Comparativo de topologia: agente único vs. sem leitura cruzada vs. completo

Gerado em: 2026-09-24T19:25:07.086487+00:00

C0 = agente único · C1 = multiagente sem leitura cruzada · C2 = multiagente completo (com leitura cruzada).

## Conclusão

EXECUÇÕES REAIS (modo api): 3 documento(s) comparável(is) (sucesso nas três topologias) de 4 rodado(s) — 9 execuções reais de pipeline no total.

### Salto 1 — agente único → sem leitura cruzada (C0→C1)
Chamadas LLM: +300.0% em média.
Duração total: +17.2% em média.
Tokens totais: +268.9% em média.
Custo estimado: +276.3% em média.
Decisão final mudou em 2/3 documento(s): psicologia_historia_livro_scielo, quimica_phase_space_arxiv_2608_11448.
Quantidade de críticas: variação média de -5.3 crítica(s).

### Salto 2 — sem leitura cruzada → completo (C1→C2)
Chamadas LLM: +75.0% em média.
Duração total: +106.1% em média.
Tokens totais: +86.0% em média.
Custo estimado: +88.9% em média.
Decisão final mudou em 1/3 documento(s): quimica_phase_space_arxiv_2608_11448.
Quantidade de críticas: variação média de +4.7 crítica(s).
Notas por revisor mudaram em 2/3 documento(s): psicologia_historia_livro_scielo, quimica_phase_space_arxiv_2608_11448.

### Total — agente único → completo (C0→C2)
Chamadas LLM: +600.0% em média. (esperado +600% estrutural: 7 chamadas em vez de 1.)
Duração total: +135.7% em média.
Tokens totais: +585.8% em média.
Custo estimado: +610.7% em média.
Decisão final mudou em 1/3 documento(s): psicologia_historia_livro_scielo.
Quantidade de críticas: variação média de -0.7 crítica(s).

Documentos não comparáveis (falha/bloqueio em alguma topologia): psicologia_lacan_cinema_pepsic.
SMOKE TEST (modo mock, sem chamada LLM e sem custo): 1 documento(s) — exemplo_mock. Serve só para provar que a ferramenta roda de ponta a ponta de graça; não entra em nenhuma média acima.
Leitura sugerida: compare o Δqualidade/Δcusto de cada salto (decisão mudou? quantidade de críticas mudou? a que custo em tempo/tokens?). Se um salto NÃO muda decisão nem críticas, aquela camada está pagando custo/tempo adicionais sem alterar o resultado nestes documentos — o valor dela, se houver, está na qualidade argumentativa e na especialização das críticas (texto de cada crítica em cada final_report.md), não capturada numericamente aqui. Note também que o Salto 1 tem base pequena (1 chamada LLM) — a variação percentual amplifica, então vale conferir os números absolutos na tabela.
LIMITE DESTA AVALIAÇÃO: 'qualidade' aqui é medida por indicadores automáticos (decisão final na escala 1-4, quantidade de críticas, quantas são bloqueantes). NÃO houve avaliação humana do conteúdo das críticas — nenhuma pessoa leu os pareceres para julgar se são pertinentes ou bem argumentados, nem se uma topologia mais especializada produz críticas mais específicas ou melhor fundamentadas que uma mais simples. 'notas_por_revisor' só é comparável entre C1 e C2 (os mesmos três revisores) e por isso aparece só no Salto 2; com o agente único (C0) os formatos diferem e ela fica de fora. Os números abaixo dizem quanto cada salto de especialização CUSTA a mais e se ele MUDA o resultado, não se ele o MELHORA.

## Execuções reais (modo api)

4 documento(s), cada um rodado 3x (C0, C1 e C2) = 12 execuções reais de pipeline, com chamadas LLM, tokens e custo medidos.

| doc_id | provider:model | chamadas (C0/C1/C2) | duração_s (C0/C1/C2) | tokens (C0/C1/C2) | custo USD (C0/C1/C2) | decisão (C0/C1/C2) | críticas (C0/C1/C2) |
|---|---|---|---|---|---|---|---|
| icd_hallucinations_2312_15710 | gemini:gemini-2.5-flash | 1/4/7 | 30.32/44.35/61.72 | 23192/88256/169299 | 0.0079/0.0315/0.0621 | 3/3/3 | 5/2/3 |
| psicologia_historia_livro_scielo | gemini:gemini-2.5-flash | 1/4/7 | 33.95/40.93/111.23 | 93232/365241/657765 | 0.0295/0.1145/0.2047 | 2/3/3 | 7/3/6 |
| psicologia_lacan_cinema_pepsic | gemini:gemini-2.5-flash | None/None/None | n/d/n/d/n/d | None/None/None | n/d/n/d/n/d | None/None/None | None/None/None |
| quimica_phase_space_arxiv_2608_11448 | gemini:gemini-2.5-flash | 1/4/7 | 55.97/47.45/98.44 | 40237/134569/250290 | 0.0132/0.0452/0.0861 | 3/4/3 | 14/5/15 |

## Smoke test (modo mock — sem chamada LLM, sem custo)

Não é evidência sobre o efeito da topologia: em modo mock os pareceres/veredito vêm de um JSON pré-salvo, nenhuma chamada LLM acontece e não há tokens nem custo para medir. Serve para provar que a ferramenta roda de ponta a ponta de graça. **Estes números não entram em nenhuma média da seção anterior.**

| doc_id | provider:model | chamadas (C0/C1/C2) | duração_s (C0/C1/C2) | tokens (C0/C1/C2) | custo USD (C0/C1/C2) | decisão (C0/C1/C2) | críticas (C0/C1/C2) |
|---|---|---|---|---|---|---|---|
| exemplo_mock | None:None | None/None/None | 0.01/0.03/0.02 | None/None/None | n/d/n/d/n/d | 3/3/3 | 3/5/5 |
