# Correção — `ExecutionCollector.registrar()` aceitava `detalhes` colidindo com campos reais

Correção pontual, feita antes de iniciar a rodada de benchmark do corpus
inteiro: era o único apontamento, dos discutidos nesta revisão, que ainda
tocava código em execução (os 33 apontamentos de Ruff/Mypy pendentes são
estilo — anotação de tipo, import não usado, f-string sem placeholder,
monkeypatch de demo — e ficaram de fora, propositalmente, por não alterarem
nenhum token medido).

| Indicador | Antes | Depois |
|---|---|---|
| Assinatura de `registrar()` | `**detalhes: Any` (kwargs soltos) | `detalhes: dict[str, Any] \| None = None` (dict explícito) |
| Colisão de chave (`duracao_s`, `chave_dedup`, ...) | Absorvida em silêncio pelo parâmetro nomeado, sem erro | `ValueError` imediato, evento não é criado |
| Call sites usando kwargs soltos | 11 (`pipeline.py` ×9, `adk_usage.py`, `llm_fallback.py`) | 0 — todos convertidos para `detalhes={...}` |
| Teste cobrindo o caso | Inexistente | `test_detalhe_com_chave_reservada_levanta_erro_em_vez_de_sobrescrever` (parametrizado) |
| Suíte completa (`pytest src`) | — | **332 passando / 0 falhas** — ver §4 |

---

## 1. O problema

`registrar()` reunia dois papéis num único `**detalhes: Any`: (a) capturar
campos livres do evento (`erro`, `agente`, `tokens_entrada`...) e (b), por
consequência do binding de keyword arguments do Python, funcionar como
fallback para os parâmetros nomeados (`duracao_s`, `chave_dedup`, `fase`,
`tipo`, `nome`, `status`).

Quando uma chamada desempacota um dicionário externo direto nesses kwargs —
único padrão já em uso no código, em `adk_usage.py` (`**usage.to_detalhes()`)
e `llm_fallback.py` (`**detalhes`) — e esse dicionário passa a conter uma
chave igual a um parâmetro nomeado, o Python entrega o valor **direto ao
parâmetro real**, não ao dict aninhado `evento.detalhes`. Não há
`TypeError`, não há warning: a duração ou a chave de dedup do evento é
sobrescrita por um valor que o chamador só queria registrar como detalhe
solto.

```python
# Antes — nenhum destes dois `duracao_s` é o mesmo, mas Python não reclama:
usage_dict = {"tokens_entrada": 120, "duracao_s": "n/d"}   # detalhe futuro, hipotético
coletor.registrar(fase=f, tipo="chamada_llm", nome=n, status=s, **usage_dict)
# duracao_s do ExecutionEvent vira "n/d" (string!) em vez de None/medido —
# e a auditoria não teria como saber que aconteceu.
```

Não havia um bug **manifestado** hoje — os dicionários que hoje alimentam
`**detalhes` (`UsageChamada.to_detalhes()`, o dict de `_registrar_metrica`)
têm chaves fixas e nenhuma colide com os nomes reservados. Era uma armadilha
latente: bastava alguém adicionar um campo mal nomeado a um desses
dicionários para corromper a métrica sem aviso nenhum, e o risco de fazer
isso durante a preparação da rodada — quando essas fontes de `detalhes`
tendem a ganhar campos novos — era real o suficiente para corrigir antes,
não durante.

## 2. A correção

`detalhes` passou a ser um parâmetro nomeado explícito
(`dict[str, Any] | None`), nunca mais um catch-all. Isso, sozinho, já
eliminaria a colisão de `duracao_s`/`chave_dedup`/etc. contra os parâmetros
reais — mas para tornar qualquer futura reincidência **ruidosa** em vez de
apenas "impossível pelo formato", `registrar()` valida o dict recebido
contra o conjunto de campos estruturais do evento e levanta `ValueError` se
houver colisão:

```python
_CHAVES_RESERVADAS_DETALHES = frozenset(
    {"run_id", "fase", "tipo", "nome", "status", "timestamp",
     "duracao_s", "detalhes", "chave_dedup"}
)
```

### Arquivos alterados

| Arquivo | O que mudou |
|---|---|
| `src/metrics/coletor.py` | Assinatura de `registrar()`; validação de colisão; `fase()`/`tool()` internos passaram a montar `detalhes={"erro": ..., "erro_tipo": ...}` |
| `src/metrics/adk_usage.py` | `registrar_usage_adk()`: `**detalhes` → `detalhes=detalhes` |
| `src/llm_fallback.py` | `_registrar_metrica()`: `evento=evento, **detalhes` → `detalhes={"evento": evento, **detalhes}` (o merge continua interno a um dict, não a kwargs — seguro) |
| `src/pipeline.py` | 9 call sites convertidos (fases 0–4: falha de fase, validação/retry/falha de agente, `validar_completude`, `auditar_decisao_final`, `checar_coerencia` ×2, veredito final, extração de PDF ×3) |
| `src/metrics/tests/test_coletor.py` | Um teste existente ajustado para o novo formato; um teste novo, parametrizado em `duracao_s`/`chave_dedup`/`fase`/`status`, prova que a colisão levanta `ValueError` e não cria evento |

Nenhum chamador ficou usando o formato antigo — confirmado por busca textual
por `**detalhes` e por `\.registrar\(` em todo `src/`.

## 3. Compatibilidade com o resto do resumo

`detalhes` sempre foi, e continua sendo, um dict aninhado dentro de
`ExecutionEvent` — a forma como ele é lido em `gerar_resumo()`,
`eventos_como_dict()` e nos testes de `test_llm_fallback.py`
(`evento.detalhes["evento"]`, `evento.detalhes["provedor_inicial"]`, etc.)
não mudou. A alteração é só na **assinatura de quem escreve**, não no
formato de quem lê.

## 4. Verificação

Na primeira passada desta correção o `.venv` do projeto estava inutilizável
(`site-packages` praticamente vazio, apontando para um interpretador Python
3.12 que não existe mais no disco — o `pyproject.toml` exige `>=3.14`), e
`uv run` falhava tentando reconstruir o próprio pacote do projeto em modo
editável (`ModuleOrPackageNotFoundError`). Esse passo de build nunca deveria
rodar: o repositório não segue layout de pacote instalável — os módulos
ficam soltos em `src/` e se importam via `sys.path.insert`, sem
`[tool.poetry.packages]`.

Correção do ambiente: `uv sync --no-install-project --group dev`, que
instala as dependências (produção + dev, incluindo `pytest`) sem tentar
instalar o projeto local. Com isso:

```
uv run --no-project pytest src/metrics/tests/test_coletor.py src/metrics/tests/test_adk_usage.py \
       src/tests/test_llm_fallback.py src/tests/test_retomada_confiavel.py
# 80 passed in 21.87s

uv run --no-project pytest src
# 332 passed in 8.04s
```

Os quatro arquivos do primeiro comando são os que exercitam `registrar()`
direta ou indiretamente (dedup, fallback de LLM, retomada de checkpoint); o
segundo comando é a suíte inteira do repositório, sem falhas.

`py_compile` nos arquivos tocados e a busca textual por `**detalhes`
remanescente (§2) continuam válidos como verificação complementar.
