# Código arquivado

Este diretório guarda código que não é mais importado por nenhum módulo ativo
do pipeline, preservado (em vez de apagado) porque documenta uma decisão de
design real e pode ser útil para migrar dados antigos no futuro.

## `legacy_adapter.py`

Verificação feita na atividade "Validação e Resiliência da Entrada por PDF"
(12/07), conforme o enunciado ("verificar se o código marcado como legacy
ainda é usado; caso não seja, removê-lo ou arquivá-lo em um commit separado,
somente depois de rodar todos os testes"):

- `LegacyEditorVerdictAdapter` / `legacy_verdict_to_schema` **não são
  importados** por `pipeline.py` nem por nenhum outro módulo, teste ou demo —
  o nome só aparecia em um comentário de `review_schema.py` explicando por que
  o sistema não tem um segundo formato de nota/veredito. Era código morto.
- Suíte completa (`pytest src`) rodada antes e depois da movimentação, sem
  nenhuma mudança de resultado — nada depende dele.

Decisão: **arquivar, não apagar** (o histórico do git também preserva a versão
anterior). O módulo documenta como converter formatos legados (escala 0-10,
vocabulário `accept`/`minor_revision`/...) para os schemas oficiais; se algum
dia for preciso migrar dados de uma execução antiga, basta reimportá-lo para
`src/` e adicionar testes. A movimentação foi feita em commit próprio, após os
testes passarem.
