# Persistência e Retomada de Execuções — Grupo 1

## O que foi implementado

O sistema de peer review já interrompia corretamente em caso de erro, mas perdia todos os resultados intermediários. Esta entrega adiciona três capacidades:

1. **Checkpoint por fase** — cada fase concluída tem seu resultado salvo em disco imediatamente.
2. **Retomada automática** — uma execução interrompida pode continuar do último ponto válido, sem repetir agentes ou fases.
3. **Resumo em caso de falha** — mesmo quando o pipeline falha, o sistema gera e salva um resumo com o que foi concluído, onde parou e quais artefatos estão disponíveis.

**Nota sobre esta versão do documento:** a implementação original desta task foi feita numa branch (`feature/grupo1-persistencia-retomada`) criada a partir de um ponto do repositório 17 dias desatualizado — sem a entrada real por PDF, a checagem de coerência do editor, a captura de tokens via ADK e o contrato de duração em segundos, que já existiam na `main` quando a branch foi aberta. Este documento descreve a versão **reconciliada**: a mesma capacidade de checkpoint/retomada, reaplicada sobre a `main` atual, preservando tudo que já existia — e com três correções encontradas durante a reconciliação, listadas na seção "Correções em relação à primeira versão".

---

## Arquivos modificados

```
src/
├── persistencia.py              ← NOVO — CheckpointManager
├── pipeline_base.py             ← MODIFICADO — hooks de serialização + checkpoint no Pipeline.run()
├── pipeline.py                  ← MODIFICADO — serialize/deserialize nas 4 fases + integração em run_demo()
├── demos/
│   └── demo_persistencia.py     ← NOVO — demonstração offline (falha + retomada, sem API key)
└── tests/
    └── test_persistencia.py     ← NOVO — 6 cenários de teste
main.py                          ← MODIFICADO — flag --resume
```

---

## Como funciona

### 1. CheckpointManager (`persistencia.py`)

Classe responsável exclusivamente por persistir e recuperar resultados em disco. Não conhece peer review nem schemas — é genérica.

```
src/logs/checkpoints/<run_id>/
    fase_1_revisao_independente.json
    fase_2_leitura_cruzada.json
    fase_3_editor_chefe.json
    fase_4_relatorio_final.json
src/logs/checkpoints/<run_id>.meta.json   ← sidecar (ver seção 4)
```

Um arquivo JSON por fase. A escrita é **atômica**: primeiro grava em `.tmp`, depois renomeia para o nome final. Se o processo morrer no meio da escrita, o arquivo anterior permanece intacto.

```python
ckpt = CheckpointManager("src/logs/checkpoints", run_id)
ckpt.save("fase_1_revisao_independente", dados_dict)  # grava
ckpt.load("fase_1_revisao_independente")              # lê (None se não existe)
ckpt.fases_concluidas()                               # lista fases já salvas
ckpt.caminho("fase_1_revisao_independente")           # Path do arquivo
```

### 2. Hooks de serialização nas fases (`pipeline_base.py` + `pipeline.py`)

Para salvar e restaurar resultados tipados (Pydantic, dataclass), cada fase declara como converter sua saída de/para dict:

| Fase | serialize_output | deserialize_output |
|---|---|---|
| `IndependentReviewPhase` | `{reviews: {rid: r.model_dump()}}` | `IndependentReviews(reviews={rid: ReviewSchema(**v)})` |
| `CrossReviewPhase` | `{cross_reviews: {rid: cr.model_dump()}}` | `CrossReviews(cross_reviews={rid: CrossReviewSchema(**v)})` |
| `EditorVerdictPhase` | padrão (`model_dump()`) | `EditorVerdictSchema(**raw)` |
| `FinalReportPhase` | padrão (`dataclasses.asdict`) | `FinalReport(markdown=raw["markdown"], data=raw["data"])` |

O `Pipeline` genérico chama esses métodos: não precisa saber o tipo concreto de cada saída.

### 3. Checkpoint integrado ao loop de fases (`pipeline_base.py`)

O `Pipeline.run()` aceita um `checkpoint_manager` opcional. Para cada fase:

```
Para cada fase:
  ├── existe checkpoint em disco?
  │   ├── SIM → abre span "checkpoint restaurado" (se houver tracer),
  │   │         reconstrói objeto tipado, registra no contexto → pula
  │   └── NÃO → executa a fase normalmente
  │               → ao concluir com sucesso: salva checkpoint em disco
  │               → só então passa para a próxima fase
```

**O parâmetro é opcional.** Sem ele, o comportamento é exatamente o anterior — nada muda para quem não usa checkpoint.

### 4. Sidecar de metadados da retomada (`pipeline.py` — `run_demo()`)

Como a `main` atual (diferente da branch original) tem entrada por PDF, retomar uma execução sem repassar o mesmo `--pdf`/modo geraria um relatório inconsistente sem erro visível. Para evitar isso, a primeira execução grava um arquivo irmão da pasta de checkpoints, `<run_id>.meta.json`, com `{"pdf_path": ..., "mode": ...}`. Numa retomada, se `pdf_path`/`mode` não forem passados explicitamente, são lidos desse arquivo.

### 5. Resumo em caso de falha (`pipeline.py` — `run_demo()`)

```python
try:
    ...
except EntradaInvalidaError:
    raise  # bloqueio de PDF não é retomável — propaga direto
except Exception as exc:
    fases_ok = ckpt.fases_concluidas()
    print(f"FALHA | run_id={run_id} | {type(exc).__name__}: {exc}")
    print(f"Fases concluídas antes da falha: {fases_ok or 'nenhuma'}")
    for fase in fases_ok:
        print(f"  checkpoint: {ckpt.caminho(fase)}")
    resumo_parcial = gerar_resumo(coletor.eventos, run_id=run_id, duracao_total_s=coletor.duracao_execucao_s)
    imprimir_resumo(resumo_parcial)
    salvar_resumo_json(resumo_parcial, caminho=out_dir / "resumo_execucao_parcial.json")
    print(f"Para retomar: python main.py --resume {run_id}")
    raise
```

O `ExecutionCollector` (Grupo 2) acumulou os eventos das fases que rodaram nesta tentativa. O `gerar_resumo()` não muda — agrega o que tiver.

---

## Como usar

### Execução nova

```bash
python main.py mock
python main.py --pdf caminho/artigo.pdf
```

### Retomar execução interrompida

Ao falhar, o sistema imprime o `run_id` e o comando exato para retomar:

```
FALHA | run_id=run_c1c7ab8e10ea4419 | RuntimeError: ...
Fases concluídas antes da falha: ['fase_1_revisao_independente', 'fase_2_leitura_cruzada']
  checkpoint: src/logs/checkpoints/run_c1c7ab8e10ea4419/fase_1_revisao_independente.json
  checkpoint: src/logs/checkpoints/run_c1c7ab8e10ea4419/fase_2_leitura_cruzada.json
Para retomar: python main.py --resume run_c1c7ab8e10ea4419
```

```bash
python main.py --resume run_c1c7ab8e10ea4419
```

Modo e `--pdf` (se houve) são recuperados automaticamente do sidecar — não precisam ser repetidos. As Fases 1 e 2 são carregadas do disco; só a Fase 3 em diante roda de fato.

### Via código

```python
from pipeline import run_demo

report = run_demo(mode="mock")                              # nova execução
report = run_demo(mode="mock", run_id="run_c1c7ab8e10ea4419")  # retomada
```

---

## Fluxo completo com retomada (verificado ao vivo nesta reconciliação)

```
1ª execução                              2ª execução (mesmo run_id)
──────────────────────                    ─────────────────────────────
Fase 1 → executa → salva ckpt        →    Fase 1 → checkpoint restaurado → PULA
Fase 2 → executa → salva ckpt        →    Fase 2 → checkpoint restaurado → PULA
Fase 3 → FALHA                        →    Fase 3 → executa → salva ckpt
                                            Fase 4 → executa → salva ckpt → relatório final
```

---

## Demonstração (`src/demos/demo_persistencia.py`)

```bash
python src/demos/demo_persistencia.py
```

Roda offline (modo mock, sem `GOOGLE_API_KEY`), reproduzível a qualquer momento (limpa o checkpoint da execução anterior antes de começar). Mostra os dois passos que a atividade pede para demonstrar, com um `run_id` fixo:

1. **Falha simulada na fase 3** — imprime o resumo de falha (fases concluídas, caminhos dos checkpoints, comando de retomada).
2. **Retomada com o mesmo run_id** — fases 1 e 2 aparecem como `(checkpoint restaurado)` e não rodam de novo; só fase 3 em diante executa, até o relatório final.

---

## Testes (`src/tests/test_persistencia.py`)

6 cenários usando as fases reais com `monkeypatch` para controle de falhas — todos offline, nenhum requer `GOOGLE_API_KEY`:

| Cenário | O que verifica |
|---|---|
| `test_sem_checkpoint_quando_fase_1_falha` | Nenhum checkpoint salvo quando Fase 1 falha |
| `test_checkpoint_fase1_quando_fase2_falha` | Ckpt Fase 1 existe; Fase 2 não tem ckpt |
| `test_checkpoints_fases1_2_quando_fase3_falha` | Ckpts Fases 1 e 2; Fase 3 sem ckpt |
| `test_retomada_pula_fases_com_checkpoint` | Contadores provam que Fases 1 e 2 não são re-executadas |
| `test_retomada_completa_apos_falha` | Ciclo completo: falha Fase 3 → retoma → conclui |
| `test_checkpoints_parciais_em_falha_run_demo` | `run_demo(mode="mock")` salva checkpoints parciais mesmo em falha |

```bash
pytest src/tests/test_persistencia.py -v   # 6/6, sem chave, sem rede
pytest src -v                              # suíte inteira do repo — 192 passed, 1 skipped (pré-existente)
```

---

## Correções em relação à primeira versão (encontradas durante a reconciliação)

1. **`FinalReportPhase` sem `deserialize_output`** — a primeira versão não implementava isso ("é a última fase, não precisa"). Está errado: uma falha entre o fim da Fase 4 e o fim de `_run_and_save()` (ex.: erro ao gravar `final_report.json`) faria uma retomada restaurar um **dict cru** em vez de `FinalReport`, quebrando com `AttributeError`. Corrigido.
2. **Checkpoint restaurado sem span no trace** — na primeira versão, pular uma fase via checkpoint não abria span nenhum no tracer do Grupo 3, deixando um buraco na timeline. Corrigido: o skip agora abre um span com `checkpoint_restaurado=True`.
3. **Teste 6 chamava `run_demo(mode="api")`** — exigia `GOOGLE_API_KEY` e fazia chamadas reais ao Gemini a cada rodada da suíte, destoando da convenção do resto do repo. Trocado para `mode="mock"`.

## Limitação conhecida (deliberada, não corrigida)

Fases restauradas de checkpoint **não re-alimentam o `ExecutionCollector`** (Grupo 2): como o registro de métricas acontece dentro do `Phase.run()` de cada fase, e uma fase restaurada nunca chama `run()`, o `resumo_execucao.json` de uma execução retomada não inclui duração/tokens/validações das fases puladas — só das que rodaram de fato na tentativa em curso. Fechar essa lacuna exigiria dar a `pipeline_base.py` conhecimento do `ExecutionCollector` (hoje confinado a `pipeline.py`, camada de domínio), o que violaria a separação de camadas atual. Avaliado e deixado como limitação documentada, não como bug.

## Integração com os outros grupos

- **Grupo 2 (métricas):** `ExecutionCollector` e `gerar_resumo()` são chamados tanto no caminho de sucesso quanto no de falha. Nenhuma interface alterada. Ver limitação acima sobre fases restauradas.
- **Grupo 3 (observabilidade):** `run_id` compartilhado desde o início (inclusive em retomadas); tracer e checkpoints apontam para a mesma execução; fases restauradas geram span próprio.
- **Grupo 3 (entrada por PDF) / Grupo 2 (tokens, coerência, duração em segundos):** nada removido — `validar_arquivo_pdf`, `extract_pdf_input`, `definir_coletor_adk`, a checagem de coerência e o contrato `duracao_s` continuam exatamente como estavam.
- `checkpoint_manager=None` (padrão) → comportamento idêntico ao anterior.
