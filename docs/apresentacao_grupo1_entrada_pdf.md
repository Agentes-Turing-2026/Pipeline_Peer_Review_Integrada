# Grupo 1 — Validação e Resiliência da Entrada por PDF

## 1. Contexto da atividade

Com a entrada real por PDF (extração do Grupo 3), a camada de confiabilidade do
Grupo 1 passa a agir **antes dos agentes**: dado um documento real, responder
**pode seguir, precisa de nova tentativa (retry) ou deve ser bloqueado?** — sem
nunca aceitar em silêncio um arquivo ou texto inadequado.

Encaixe entre os grupos, sem invadir território:

- **Grupo 3** implementa o extrator (`src/extraction/`) e a orquestração da
  fase 0. Não reimplementamos nada disso — só validamos o arquivo antes e o
  `ExtractedDocument` depois.
- **Grupo 2** cuida de métricas/tokens. Não calculamos métricas.
- **Grupo 1** (aqui) decide o destino da entrada e registra o porquê,
  reaproveitando `eventos_validacao.py`: o MESMO `run_id`, o MESMO
  `validacao_events.jsonl` e o MESMO espelhamento no trace do Grupo 3. Nada de
  segundo sistema de logs nem outro identificador.

## 2. Checklist — o que foi pedido × o que foi entregue

| Pedido no PDF | Como foi atendido |
|---|---|
| Validar problemas básicos antes da extração (inexistente, formato, protegido, corrompido, ilegível). | `classificar_arquivo_pdf` em [`src/validacao_entrada.py`](../src/validacao_entrada.py): existência, é-arquivo, extensão `.pdf`, bytes > 0, cabeçalho `%PDF`, `/Encrypt`. |
| Validar o resultado da extração (texto vazio, muito curto, ilegível, páginas sem conteúdo, avisos). | `classificar_documento_extraido`: vazio, `< 200` caracteres, `> 30%` ilegíveis, densidade `< 200` car./página, avisos do extrator. |
| Definir o que permite retry, o que gera alerta e o que bloqueia. | Enum `DecisaoEntrada` (OK / ALERTA / RETRY / BLOQUEAR) + tabela de política no README §4.1. |
| Retry só quando resolve; não repetir arquivo corrompido. | Bloqueios de arquivo têm `permite_retry=False`. Retry só para texto vazio **e** com estratégia de reextração; sem estratégia → bloqueio. |
| Conectar ao trace preservando `run_id` e as categorias. | Eventos via `emitir_evento` (mesmo arquivo/`run_id`/trace); categorias `passou_de_primeira`, `alerta`, `falhou_recuperavel`, `corrigido`, `passou_apos_correcao`, `bloqueado`. |
| Exemplos automatizados (válido, insuficiente, recuperável, bloqueante). | `src/examples/example_extracted_document*.json` + os quatro cenários em [`src/demos/demo_entrada_pdf.py`](../src/demos/demo_entrada_pdf.py) e em [`src/tests/test_validacao_entrada.py`](../src/tests/test_validacao_entrada.py). |
| Organizar as demos em pasta própria. | Demos do Grupo 1 movidas para [`src/demos/`](../src/demos/). |
| Verificar código legacy sem uso; remover/arquivar em commit separado. | `legacy_adapter.py` não era importado por ninguém → removido em commit próprio, após os testes passarem. |
| Não registrar correção quando os dados não mudaram. | No orquestrador, a reextração só vira `corrigido` se o texto realmente mudar; caso contrário, bloqueio. |
| Falha importante não pode ficar só no console. | Bloqueio vira evento estruturado **e** `EntradaInvalidaError`; `main.py` encerra com mensagem clara e código ≠ 0. |

## 3. O que apresentar (os quatro casos, no mesmo `run_id`)

`python src/demos/demo_entrada_pdf.py` mostra, ligados à mesma execução:

1. **Passa** — documento com texto suficiente → `passou_de_primeira`.
2. **Alerta** — texto curto / aviso do extrator → `alerta` (segue, revisão humana).
3. **Recuperável** — texto vazio → reextração (OCR) recupera → `falhou_recuperavel`
   → `corrigido` (diff `text_chars: 0 → N`) → `passou_apos_correcao`.
4. **Bloqueado** — arquivo corrompido (sem `%PDF`) e texto irrecuperável sem OCR
   → `bloqueado` + `EntradaInvalidaError`, com motivo claro.

No pipeline real: `python main.py mock --pdf arquivo.pdf`. Um arquivo corrompido
é barrado com `[ENTRADA BLOQUEADA] cabeçalho '%PDF' ausente ...` e saída ≠ 0.

## 4. Decisões de projeto (curtas)

- **Determinístico, fora da LLM.** Checagens de arquivo e texto são regras
  simples — não viram agente nem tool (como a atividade permite).
- **Reuso do sistema de eventos.** Zero logs paralelos: mesma trilha,
  `run_id` e trace já existentes. Categoria `alerta` foi acrescentada ao
  vocabulário do Grupo 1 para o caso "segue com ressalvas".
- **Integração mínima com o Grupo 3.** Dois ganchos no `pipeline.py` (arquivo
  em `run_demo`, documento em `extract_pdf_input`) e o orquestrador
  `validar_entrada_com_retry` exposto para ligar OCR depois — sem redesenhar o
  pipeline.
- **Retry honesto.** Só onde ajuda; nunca repete arquivo corrompido; não marca
  "correção" sem mudança real.

## 5. Verificação

- `pytest src` — **132 testes** (110 anteriores + 22 novos de entrada), incluindo
  os testes de PDF do Grupo 3, que continuam verdes.
- `python main.py mock` — execução mock preservada, ponta a ponta.
- `python main.py mock --pdf <corrompido>.pdf` — bloqueio com motivo claro, saída ≠ 0.
- `python src/demos/demo_entrada_pdf.py` — os quatro cenários no mesmo `run_id`.
