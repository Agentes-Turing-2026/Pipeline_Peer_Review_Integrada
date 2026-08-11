# Persistência e Retomada de Execuções — Grupo 1

Relatório técnico da task de confiabilidade. Documento único do assunto: a
primeira entrega (checkpoint por fase) e a segunda (confiabilidade da retomada)
estão aqui, na ordem em que aconteceram.

> **Documentação de uso** (como rodar, o que cada arquivo significa):
> [README §4.2](../README.md#42-persistência-e-retomada-de-execuções-grupo-1).
> Este documento é o registro técnico: o que foi decidido e por quê.

---

## 1. O que foi pedido

**Primeira entrega.** O pipeline interrompia corretamente em caso de erro, mas
perdia todos os resultados intermediários. Foram adicionadas três capacidades:

1. **Checkpoint por fase** — cada fase concluída tem seu resultado salvo em disco.
2. **Retomada automática** — uma execução interrompida continua do último ponto
   válido, sem repetir agentes ou fases.
3. **Resumo em caso de falha** — mesmo falhando, o sistema mostra o que foi
   concluído, onde parou e quais artefatos existem.

**Segunda entrega.** O coordenador apontou três defeitos no que ficou pronto:

| # | Defeito | Sintoma |
|---|---|---|
| 1 | Métricas não voltam ao resumo na retomada | Fase restaurada não roda, logo não emite evento — o resumo final descrevia só o pedaço re-executado |
| 2 | PDF re-extraído a cada retomada | A extração acontecia fora das fases com checkpoint |
| 3 | Execução concluída podia ser regravada com métricas vazias | Retomar um run pronto restaurava as 4 fases e sobrescrevia os artefatos completos |

Mais testes com falha simulada no meio do pipeline, demonstração curta e
documentação. Durante a correção apareceu um **quarto defeito da mesma
família**, não listado no pedido: a auditoria do veredito (fase 3 → fase 4, via
`context.config`) sumia quando a fase 3 vinha do checkpoint, deixando
`auditoria_veredito: null` no relatório sem nenhum aviso.

---

## 2. Uma lição que já custou caro duas vezes: partir da `main` errada

A **primeira entrega** foi feita numa branch aberta a partir de um ponto do
repositório 17 dias desatualizado — sem entrada por PDF, sem checagem de
coerência do editor, sem captura de tokens via ADK e sem o contrato de duração
em segundos, que já existiam na `main`. Foi preciso reconciliar tudo antes de
integrar.

O mesmo aconteceu na **segunda entrega**, num trabalho paralelo do grupo: a
branch `grupo1/fix-checkpoint-retomada` partiu de um snapshot enviado por fora do
git (commit raiz, sem ancestral comum com a `main`), em que o
`CheckpointManager` de fato **não existia**. O diagnóstico dos três defeitos
estava certo e o desenho proposto era razoável, mas reimplementava do zero
infraestrutura que já estava na `main` e, se integrada, apagaria 75 arquivos —
todo o benchmark e as métricas do Grupo 2 e a observabilidade e a extração do
Grupo 3.

Conclusão prática, registrada aqui para não haver terceira vez: **abrir a branch
a partir de `origin/main`**, e não de um zip, snapshot ou cópia local. Se um
snapshot for inevitável, conferir `git log origin/main -1` antes de escrever a
primeira linha.

---

## 3. Arquivos

| Arquivo | Entrega | O que faz |
|---|---|---|
| `src/persistencia.py` | 1ª + 2ª | `CheckpointManager`: checkpoint por fase, estado auxiliar em sidecar, escrita atômica |
| `src/pipeline_base.py` | 1ª + 2ª | Checkpoint no loop de fases; gancho `on_phase_end`; `PipelineResult.fases_restauradas` |
| `src/pipeline.py` | 1ª + 2ª | `serialize`/`deserialize` das 4 fases; fase 0 com checkpoint; restauração de métricas e auditoria; no-op da execução concluída |
| `src/metrics/coletor.py` | 2ª | `ExecutionCollector.restaurar()` (aditivo, módulo do Grupo 2) |
| `main.py` | 1ª + 2ª | Flags `--resume` e `--force` |
| `src/demos/demo_persistencia.py` | 1ª + 2ª | Demo offline: falha → retomada → retomada de execução concluída |
| `src/tests/test_persistencia.py` | 1ª | 6 cenários: quais fases são puladas |
| `src/tests/test_retomada_confiavel.py` | 2ª | 9 cenários: o que sobrevive à retomada |

---

## 4. Como funciona

### 4.1 `CheckpointManager` (`persistencia.py`)

Genérico: não conhece peer review nem schemas. Duas categorias de arquivo,
deliberadamente separadas:

```
src/logs/checkpoints/<run_id>/fase_0_extracao_pdf.json        ← ExtractedDocument completo
src/logs/checkpoints/<run_id>/fase_1_revisao_independente.json
src/logs/checkpoints/<run_id>/fase_2_leitura_cruzada.json
src/logs/checkpoints/<run_id>/fase_3_editor_chefe.json
src/logs/checkpoints/<run_id>/fase_4_relatorio_final.json
src/logs/checkpoints/<run_id>.meta.json     ← sidecar: pdf_path, mode, status
src/logs/checkpoints/<run_id>.estado.json   ← sidecar: métricas, auditoria, tempo acumulado
```

Os sidecars são **irmãos** da pasta do run, não filhos. `fases_concluidas()`
lista os `*.json` de dentro dela; um estado guardado ali viraria uma "fase
fantasma" no resumo de falha e na retomada.

```python
ckpt = CheckpointManager("src/logs/checkpoints", run_id)
ckpt.save("fase_1_revisao_independente", dados)   # atômico: .tmp + rename
ckpt.load("fase_1_revisao_independente")          # None se não existe
ckpt.fases_concluidas()                           # fases já salvas
ckpt.salvar_estado("estado", {...})               # sidecar
ckpt.carregar_estado("estado", tolerar_corrompido=True)
ckpt.limpar_fases()                               # usado pelo --force
```

### 4.2 Hooks de serialização nas fases

| Fase | `serialize_output` | `deserialize_output` |
|---|---|---|
| `IndependentReviewPhase` | `{reviews: {rid: r.model_dump()}}` | `IndependentReviews(reviews={rid: ReviewSchema(**v)})` |
| `CrossReviewPhase` | `{cross_reviews: {rid: cr.model_dump()}}` | `CrossReviews(cross_reviews={rid: CrossReviewSchema(**v)})` |
| `EditorVerdictPhase` | padrão (`model_dump()`) | `EditorVerdictSchema(**raw)` |
| `FinalReportPhase` | padrão (`dataclasses.asdict`) | `FinalReport(markdown=..., data=...)` |

O `Pipeline` genérico chama esses métodos sem saber o tipo concreto de cada saída.

### 4.3 O loop de fases (`pipeline_base.py`)

```
Para cada fase:
  ├── existe checkpoint?
  │   ├── SIM → abre span "checkpoint_restaurado" no tracer, reconstrói o objeto
  │   │         tipado, registra no contexto e em fases_restauradas → PULA
  │   └── NÃO → executa
  │             → salva o checkpoint
  │             → chama on_phase_end(fase, context)
```

`on_phase_end` é o gancho que a segunda entrega adicionou. A orquestração
continua agnóstica: ela só chama o callback depois de gravar o checkpoint, sem
saber o que o domínio persiste ali. É o que mantém checkpoint e métricas
descrevendo sempre o **mesmo ponto** da execução.

### 4.4 O que a retomada restaura (segunda entrega)

- **PDF extraído** — a fase 0 virou checkpoint como as demais. O arquivo original
  **não** é revalidado: ele já passou por `validar_arquivo_pdf` e
  `validar_documento_extraido` na execução original, e esses eventos estão no
  `validacao_events.jsonl` do mesmo `run_id`. Efeito prático: a retomada funciona
  mesmo que o PDF tenha sido movido ou apagado.
- **Métricas** — os eventos salvos voltam ao `ExecutionCollector` por
  `restaurar()`, com **timestamp e duração originais**: a duração que vale para
  uma fase é a de quando ela de fato rodou. O tempo de parede das tentativas
  anteriores também é somado, senão o resumo mostraria duração total menor que a
  soma das fases.
- **Auditoria do veredito** — viaja no sidecar de estado. Precisa vir pelo
  `context` no `on_phase_end`, e não pelo `config` de `run_demo`, porque
  `PipelineContext` **copia** o config que recebe.
- **Traces e eventos** — não precisaram de código novo: `JsonlExporter` e
  `validacao_events.jsonl` já são append-only, então a retomada acrescenta ao
  histórico em vez de substituí-lo. Há teste travando isso.

### 4.5 Execução concluída é no-op

Se todas as fases concluíram e os artefatos foram gravados, `--resume` imprime
que a execução já terminou, devolve o relatório existente e **não escreve nada**.
Para refazer de propósito, `--force` descarta checkpoints **e** métricas e roda
tudo de novo sob o mesmo `run_id` — as métricas vão junto, senão cada fase seria
contada duas vezes.

O `status: concluida` só é gravado **depois** que `final_report.md`/`.json` estão
em disco. Marcar antes deixaria um `run_id` que se diz pronto sem relatório
algum, e uma retomada dele viraria um no-op sem entrega.

### 4.6 Resumo em caso de falha

```
FALHA | run_id=run_c1c7ab8e10ea4419 | RuntimeError: ...
Fases concluídas antes da falha: ['fase_1_revisao_independente', 'fase_2_leitura_cruzada']
  checkpoint: src/logs/checkpoints/run_c1c7ab8e10ea4419/fase_1_revisao_independente.json
  checkpoint: src/logs/checkpoints/run_c1c7ab8e10ea4419/fase_2_leitura_cruzada.json
Para retomar: python main.py --resume run_c1c7ab8e10ea4419
```

---

## 5. Fluxo completo

```
1ª execução                             2ª execução (mesmo run_id)
─────────────────────────────           ─────────────────────────────────────────
Fase 0 → extrai → salva ckpt        →   Fase 0 → checkpoint restaurado → NÃO extrai
Fase 1 → executa → salva ckpt       →   Fase 1 → checkpoint restaurado → PULA
Fase 2 → executa → salva ckpt       →   Fase 2 → checkpoint restaurado → PULA
Fase 3 → FALHA                      →   Fase 3 → executa → salva ckpt
                                        Fase 4 → executa → salva ckpt
                                        → resumo com TODAS as fases → status: concluida

3ª execução (mesmo run_id)
──────────────────────────
status == concluida → devolve o relatório do disco
→ nenhuma fase roda, nenhum arquivo é regravado
```

---

## 6. Decisões técnicas

**Checkpoint no orquestrador, não nas fases.** A mecânica de salvar/restaurar
vive em `Pipeline.run()`. As fases só declaram `serialize_output`/
`deserialize_output` — métodos de dados, não de infraestrutura.

**Estado auxiliar em sidecar, fora da pasta do run.** Ver §4.1: dentro dela ele
seria confundido com uma fase.

**Métricas são persistidas só em fronteiras de sucesso** (fase 0 extraída, cada
fase concluída, fim da execução), nunca no caminho de falha. Salvar os eventos de
uma tentativa que falhou faria a retomada bem-sucedida herdar
`quantidade_falhas` e `status_final="falha"` de um problema já resolvido. O
rastro forense da tentativa que falhou continua no trace e no
`validacao_events.jsonl`.

**O schema do resumo (Grupo 2) não mudou.** A correção entrou pelo coletor
(`restaurar()`, aditivo, com default que preserva o comportamento atual), não por
`metrics/resumo.py`. A proveniência da retomada vai num bloco próprio do
relatório, `data["retomada"]` (`execucoes`, `fases_restauradas`,
`eventos_metricas_restaurados`).

**Sidecar ilegível: tolerância assimétrica, decidida pela conduta que ele
governa.** `meta` é lido de forma **estrita** — ele decide se a execução já foi
concluída, e tratá-lo como ausente faria o pipeline sobrescrever artefatos
completos, exatamente o defeito 3. `estado` é lido com
`tolerar_corrompido=True` — perder as métricas degrada o resumo, enquanto abortar
por causa delas custaria a retomada inteira. O arquivo ruim é preservado como
`.corrompido` e a lacuna aparece como alerta no resumo.

**Rede de segurança contra a regressão.** Antes de gravar, o pipeline confere se
toda fase com checkpoint aparece no resumo; se alguma faltar, um alerta explícito
entra em `resumo.alertas`. Um resumo incompleto que se denuncia é melhor que um
que passa batido — que era o defeito 1.

**Mínima invasão.** Schemas, fases concretas e modo mock não foram alterados. As
mudanças ficaram nos pontos de extensão naturais: o orquestrador
(`pipeline_base.py`) e a camada de domínio (`pipeline.py`).

---

## 7. Testes

```bash
python -m pytest src/tests/test_persistencia.py src/tests/test_retomada_confiavel.py -v
python -m pytest src -q     # suíte inteira: 233 passed, 5 skipped
```

**`test_persistencia.py` (6 cenários)** — *quais fases são puladas*: nenhum
checkpoint quando a fase 1 falha; checkpoint da fase 1 quando a fase 2 falha;
fases 1 e 2 quando a fase 3 falha; contadores provando que fase com checkpoint
não re-executa; ciclo completo falha → retomada → conclusão; `run_demo` salvando
checkpoints parciais em falha.

**`test_retomada_confiavel.py` (9 cenários)** — *o que sobrevive*. O critério é
comparativo: a retomada tem que ser **indistinguível de uma execução que nunca
falhou**.

| Cenário | O que verifica |
|---|---|
| `test_retomada_reproduz_o_resumo_de_uma_execucao_limpa` | Resumo da retomada == resumo de execução limpa (fases, validações, tools, `status_final`) |
| `test_retomada_executa_somente_o_que_faltava` | Contadores por fase + `data["retomada"]["fases_restauradas"]` |
| `test_retomada_nao_reextrai_o_pdf` | Extrator espião chamado 1×; o PDF é **apagado antes da retomada** |
| `test_retomar_execucao_concluida_nao_regrava_nem_reexecuta` | Três artefatos byte a byte idênticos; nenhuma fase roda |
| `test_retomada_preserva_trace_e_eventos_anteriores` | Linhas antigas continuam sendo prefixo das novas; erro da 1ª tentativa ainda visível |
| `test_auditoria_do_veredito_sobrevive_a_retomada` | `auditoria_veredito` não vira `null` com a fase 3 restaurada |
| `test_estado_ilegivel_nao_impede_a_retomada` | Sidecar corrompido → retomada conclui, arquivo preservado, alerta no resumo |
| `test_estado_auxiliar_nao_e_confundido_com_uma_fase` | Sidecar fora de `fases_concluidas()`; `limpar_fases` não apaga o estado |
| `test_coletor_restaurado_preserva_duracao_e_acumula_tempo` | Timestamp/duração originais; tempo de parede acumula |

Os cenários de ponta a ponta rodam o pipeline **real** em modo mock com uma
exceção injetada no meio; `LOG_DIR`, `OUTPUT_DIR` e o arquivo de eventos vão para
`tmp_path`, então nada é escrito no repositório.

**Verificação de que os testes pegam os defeitos:** com as quatro correções
desligadas, 5 dos 9 falham. Os que passam travam comportamento correto que já
existia (append-only do trace/eventos) — estão ali para ninguém quebrá-lo sem
perceber.

---

## 8. Demonstração

```bash
python src/demos/demo_persistencia.py     # offline, sem chave, reproduzível
```

Três passos: falha simulada na fase 3 → retomada com o mesmo `run_id` → retomada
da execução já concluída. O passo 2 imprime a tabela abaixo, e o passo 3 compara
o `sha256` do `final_report.json` antes e depois.

```
  Fase                              checkpoint   no resumo final
    fase_1_revisao_independente      sim          sim
    fase_2_leitura_cruzada           sim          sim
    fase_3_editor_chefe              sim          sim
    fase_4_relatorio_final           sim          sim

  Restauradas do disco (não rodaram agora): ['fase_1_revisao_independente', 'fase_2_leitura_cruzada']
  Eventos de métrica restaurados:           11
  Validações no resumo final:               7
  Status final:                             sucesso
  >>> Nenhuma fase concluída ficou de fora do resumo.
```

---

## 9. Correções encontradas durante as reconciliações

Da primeira entrega:

1. **`FinalReportPhase` sem `deserialize_output`** — "é a última fase, não
   precisa" estava errado: uma falha entre o fim da fase 4 e a gravação do
   `final_report.json` faria a retomada restaurar um **dict cru** em vez de
   `FinalReport`, quebrando com `AttributeError`.
2. **Checkpoint restaurado sem span no trace** — pular uma fase não abria span
   nenhum, deixando um buraco na timeline do Grupo 3.
3. **Teste chamando `run_demo(mode="api")`** — exigia `GOOGLE_API_KEY` e fazia
   chamadas reais ao provedor a cada rodada da suíte.

Da segunda entrega:

4. **Auditoria do veredito perdida na retomada** — o quarto defeito descrito na
   §1, que não estava no pedido.

---

## 10. Integração com os outros grupos

- **Grupo 2 (métricas).** `metrics/coletor.py` ganhou `restaurar()`: aditivo, com
  default que preserva o comportamento atual, sem tocar em `resumo.py`,
  `exportar.py` nem no schema do `ResumoExecucao`. É a única interface do Grupo 2
  alterada, e vale comunicar antes da integração.
- **Grupo 3 (observabilidade).** `run_id` compartilhado desde o início, inclusive
  em retomadas; fases restauradas geram span próprio com
  `checkpoint_restaurado=True`, e a fase 0 restaurada emite
  `documento_restaurado`.
- **Grupo 3 (entrada por PDF).** Nada removido — `validar_arquivo_pdf`,
  `extract_pdf_input` e o contrato `ExtractedDocument` continuam como estavam. A
  fase 0 apenas ganhou checkpoint.
- `checkpoint_manager=None` (padrão de `Pipeline.run`) → comportamento idêntico
  ao anterior.

---

## 11. Limitações conhecidas (deliberadas)

- **Granularidade é a fase.** Uma falha no terceiro revisor da fase 1 refaz os
  três. Retomada por revisor exigiria checkpoint dentro da fase, o que só
  compensa se as fases ficarem muito mais caras.
- **A tentativa que falhou não entra no resumo final.** É consequência direta da
  decisão de persistir métricas só em fronteiras de sucesso (§6). O histórico
  completo, incluindo o erro, continua no trace e no `validacao_events.jsonl`.

> A limitação registrada na primeira versão deste documento — "fases restauradas
> não re-alimentam o `ExecutionCollector`, e fechar isso exigiria dar a
> `pipeline_base.py` conhecimento do coletor, violando a separação de camadas" —
> **foi resolvida** na segunda entrega, e sem violar a separação: o gancho
> `on_phase_end` é genérico e a orquestração continua sem saber o que o domínio
> persiste.
