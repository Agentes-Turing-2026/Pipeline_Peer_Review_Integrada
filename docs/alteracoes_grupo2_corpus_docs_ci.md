# Grupo 2 — Alterações desta entrega

Branch `grupo-2-corpus-publico-docs-e-ci`, criada a partir de `dev` — e é para
`dev` que o pull request é aberto: o CI roda lá e, com tudo verde, `dev` segue
para a `main`.

Este documento apresenta, para cada ponto levantado nesta rodada, **o estado
anterior e o estado atual**. As seções seguem a ordem da apresentação.

| Indicador | Antes | Depois |
|---|---|---|
| Pontos levantados e atendidos | — | **6 de 6** |
| **Suíte executada pelo CI** | **nunca** | **327 passando** |
| Testes da suíte (execução local) | 274 passando | **280 passando / 9 pulados** |
| Ruff em `metrics` + `benchmark` | 50 erros | **0** |
| Mypy em `metrics` + `benchmark` | 3 erros | **0** |
| Ruff no repositório inteiro | 223 erros | **8** |
| Mypy no repositório inteiro | 38 erros | **32** |
| Bandit — alertas de severidade média | 1 | **0** |
| Documentos no corpus | 10 | **15** |
| Documentos reprodutíveis por terceiros | 6 de 10 | **14 de 15** |

> [!NOTE]
> A linha "suíte executada pelo CI" não é força de expressão. Até esta entrega
> o job abortava no primeiro passo e o Pytest era **pulado em toda execução**,
> em todo PR de todo grupo — ver §8.1. A diferença entre os 280 locais e os 327
> do CI também não é regressão: `google.adk` e `litellm` não estão instalados
> na máquina de desenvolvimento, e os arquivos de teste que dependem deles são
> pulados inteiros.

---

## 1. Custo por chamada: uma pendência exclusivamente documental

Foi solicitado que o custo passasse a ser calculado por chamada, com base no
modelo que efetivamente respondeu, em vez de um preço único aplicado ao total
da execução.

**Esse ponto já estava resolvido em código antes desta branch**, pelo
[PR #18](https://github.com/Agentes-Turing-2026/Pipeline_Peer_Review_Integrada/pull/18).
O grupo tinha ciência disso. A pendência real, e o objeto desta entrega, era a
documentação, que permanecia descrevendo o comportamento anterior.

| | Antes | Depois |
|---|---|---|
| **Código** | Já correto desde o PR #18: cada evento `chamada_llm` é precificado pelo seu próprio `detalhes.modelo` e os custos são somados. | Sem alteração. |
| **Documentação** | Três trechos ainda afirmavam que `run_demo()` resolve um preço único por execução e que a tabela precifica pelo modelo *configurado*. | Reescritos, com o registro do trade-off que motivou a mudança de decisão. |

Trechos corrigidos: `docs/relatorio_benchmark_custo_eficiencia.md §3.2` e
`§3.3`; `docs/metricas_reference.md §5.3`; `README.md`.

> [!NOTE]
> A decisão de precificar pelo modelo **configurado** existia para proteger o
> cálculo contra um gateway que reescrevesse o nome do modelo na resposta. Ela
> foi revertida no PR #18 porque o cenário oposto — mais de um modelo na mesma
> execução, seja pelo editor com modelo próprio, seja pelo fallback assumindo
> no meio — é frequente e caro de errar, enquanto o gateway é hipotético neste
> projeto. Para ele, o override por variável de ambiente continua tendo
> precedência sobre todas as demais fontes.

---

## 2. O custo é estimado, não real

| | Antes | Depois |
|---|---|---|
| Texto | "Custo total **real** da comparação: US$ 0,248" | "Custo total **estimado**: US$ 0,248" |
| Justificativa | Ausente | Explicitada em todos os pontos onde o valor aparece |

O valor não provém de fatura nem de qualquer API de cobrança do provedor. Ele
é o produto de `tokens medidos × tabela de preços`. Divergências em relação à
cobrança efetiva são esperadas quando o preço de tabela está desatualizado,
quando a conta possui desconto ou crédito, ou quando o provedor tarifa itens
que a contagem de tokens não captura.

O princípio que organiza o módulo permanece: **o consumo é medido, o preço é
configuração**, e ambos só se combinam na multiplicação final. Qualificar o
resultado como "real" atribuía ao sistema uma precisão que ele não possui.

Corrigido em `docs/entrega_grupo2_custo_eficiencia.md`, `docs/metricas_reference.md`
e `README.md`.

---

## 3. Seção de validação desatualizada

| | Antes (`§7.3`) | Depois (`§7.2`) |
|---|---|---|
| Afirmação | "O que continua não validado: uma execução completa do pipeline e a comparação pareada com dados reais dos dois lados." | Tabela com as **10 execuções reais** já registradas. |
| Correspondência com os dados | Descrevia o estado anterior às execuções pagas | Corresponde ao conteúdo de `resultados/ablacao_cross_review.json` |

São 5 PDFs reais, cada um executado nas duas variantes — com e sem leitura
cruzada — totalizando 10 execuções, no provedor `openai`, modelo
`gpt-5.6-luna`, com tokens medidos pelo `usage_metadata` do ADK.

A seção reescrita mantém uma linha registrando que o texto anterior estava
defasado. A alternativa — substituir o trecho sem deixar rastro — dificultaria
a auditoria posterior da entrega.

---

## 4. Separação entre execuções reais e o exemplo em modo mock

A demanda foi formulada como um ajuste de documentação. A investigação
mostrou que a causa estava no gerador do relatório.

`gerar_conclusao()` agregava todos os pares sem distinguir o modo de execução.
O par `exemplo_mock` — que não realiza nenhuma chamada de LLM, não mede
tokens, não possui custo e dura cerca de 0,01 segundo — era computado nas
mesmas médias das execuções reais.

### Efeito sobre os números publicados

| Métrica agregada | Antes | Depois |
|---|---|---|
| Documentos comparáveis | 6 | **5** |
| Duração total | −17,5% | **−20,9%** |
| Decisão final alterada | 1/6 | **1/5** |
| Quantidade de críticas | +2,3 | **+2,8** |
| Chamadas LLM | −42,9% | −42,9% |
| Tokens totais | −47,7% | −47,7% |
| Custo estimado | −41,9% | −41,9% |

Chamadas, tokens e custo permaneceram inalterados porque o modo mock não
produz esses dados, e valores `None` já eram descartados do cálculo. A
contaminação atingiu a **duração** — que existe em modo mock, medindo apenas
I/O — e as **contagens**.

> [!IMPORTANT]
> Os valores corrigidos coincidem exatamente com a tabela conferida
> manualmente em `docs/entrega_grupo2_custo_eficiencia.md`. A tabela manual estava
> correta; o relatório gerado automaticamente é que apresentava desvio. Os
> dois documentos da entrega se contradiziam, e essa era a origem da
> divergência.

### Alterações no código

- `separar_por_modo()` divide os pares entre `mode == "api"` e os demais.
- `gerar_conclusao()` agrega somente as execuções reais; o modo mock passa a
  ocupar uma linha própria, identificada como smoke test.
- `salvar()` emite duas tabelas em seções distintas, com aviso explícito de
  que os valores do mock não integram nenhuma média.
- Nova flag `--regerar`: recalcula a conclusão e reescreve os artefatos a
  partir dos pares já gravados, **sem executar o pipeline**. Foi o mecanismo
  usado para atualizar os resultados sem novo consumo de API.
- A tabela deixou de imprimir o float bruto (`0.019246399999999997`) e passou
  a arredondar. O valor exato permanece no `.json`.

---

## 5. Limite da avaliação de qualidade

| | Situação |
|---|---|
| **Está medido** | Quantidade de críticas, quantas são bloqueantes, alteração da decisão final e alteração das notas por revisor — todos extraídos automaticamente do veredito do editor. |
| **Não está medido** | Pertinência, correção factual e qualidade argumentativa das críticas. Nenhum avaliador humano leu os pareceres, e não foi empregado LLM-juiz. |

A consequência é direta e passou a constar de forma inequívoca: está medido
**quanto a leitura cruzada custa** e **se ela altera** o resultado. **Não**
está medido **se ela melhora** a revisão — que era a pergunta original da
atividade.

A ressalva foi inserida em quatro pontos: `README.md`,
`docs/entrega_grupo2_custo_eficiencia.md`,
`docs/relatorio_benchmark_custo_eficiencia.md §7.4` e — o mais relevante — no
próprio código, como a constante `LIMITE_QUALIDADE`, anexada a toda conclusão
gerada. Um teste falha caso a ressalva seja removida do relatório, o que
impede que ela se perca em revisões futuras.

---

## 6. Apontamentos do CI em métricas e benchmark

| Ferramenta | Antes | Depois |
|---|---|---|
| Ruff | 50 | **0** |
| Mypy | 3 | **0** |
| Bandit (excluindo `assert`) | 1 alerta médio | **0** |

### 6.1. O alerta de severidade média

O relatório de CI encaminhado ao grupo atribuía o único alerta médio do
projeto ao teste **B110** (`try_except_pass`), em `src/validacao_retry.py`. A
execução da ferramenta indica **B310** (`urlopen` sem validação de esquema),
em `src/benchmark/corpus.py` — arquivo do Grupo 2.

A contagem do relatório estava correta: 835 alertas B101, 5 B105 e 3 B110
totalizam 843 de severidade baixa; somado 1 de severidade média, chega-se aos
844 informados. Apenas o identificador do alerta médio estava trocado.

**O risco era concreto.** `urlopen` aceita `file://`, `ftp://` e esquemas
customizados. O `corpus_manifest.json` é um arquivo de dados editável por
qualquer integrante do projeto; sem validação, uma entrada com `file://` faria
o benchmark ler um caminho arbitrário do disco e gravá-lo no cache como se
fosse um PDF baixado da internet.

A correção valida o esquema contra `("http", "https")` antes da abertura.

### 6.2. Ruff e Mypy

Dos 50 apontamentos do Ruff, a maior parte era mecânica: 24 diretivas `noqa`
inativas, blocos de import fora de ordem, modernizações de sintaxe. Aplicadas
com `--fix` e revisadas no diff.

Os 10 casos de **ISC004** exigiram análise: são concatenações implícitas de
string dentro de literais de coleção. Caso uma vírgula seja omitida entre dois
elementos legítimos, eles se fundem silenciosamente e a coleção passa a ter um
elemento a menos. Cada concatenação recebeu parênteses explícitos.

Os 3 erros de Mypy foram resolvidos sem recorrer a `# type: ignore`.

---

## 7. Corpus público e reprodutível

| | Antes | Depois |
|---|---|---|
| Documentos | 10 | **15** |
| Com URL pública verificada | 6 | **14** |
| Dependentes de arquivo local | 4 | **0** |
| Áreas cobertas | IA, psicologia | IA, psicologia, **química, arquitetura** |

Quatro documentos de psicologia possuíam apenas `caminho_local`, apontando
para um diretório ignorado pelo git. Na prática, o benchmark não era
reproduzível por ninguém além de quem havia coletado os arquivos — limitação
relevante para uma entrega destinada a avaliação externa.

Os quatro foram substituídos por equivalentes públicos que preservam o papel
de cada um no corpus:

| Aposentado | Substituto | Papel preservado |
|---|---|---|
| `psicologia_neuropsi_escaneado` | `psicologia_lacan_cinema_pepsic` | Entrada problemática, barrada antes do consumo de LLM |
| `psicologia_respiracao_monografia` | `psicologia_historia_livro_scielo` | Documento longo que não é artigo de periódico |
| `psicologia_slides_modalidades_grupais` | `psicologia_slides_psiquismo_trt6` | Slide 4:3, baixa densidade de texto |
| `psicologia_slides_ciclo_sono` | `psicologia_slides_autocuidado_tcerj` | Slide 16:9, material de curso |

Cada URL foi verificada mediante download efetivo do arquivo, com medição de
número de páginas, produtor e densidade de texto. Os valores registrados em
`observacoes` são medidos, não estimados. Em seguida, o corpus completo foi
baixado por meio do próprio `resolver_pdf_local()`, para validar o caminho
real de download.

> [!NOTE]
> O substituto do PDF escaneado constitui um caso mais exigente que o
> original. O arquivo do PePSIC extrai cerca de 3.243 caracteres por página —
> volume compatível com um artigo comum — porém suas fontes não possuem tabela
> `ToUnicode`, e o texto resultante são caracteres de controle. Uma heurística
> baseada em "há texto suficiente?" é aprovada; apenas uma verificação de
> legibilidade identifica o problema. Trata-se de falha silenciosa.

O documento `psicologia_slides_ciclo_sono` integrava as 5 comparações reais
pagas. Foi aposentado do manifesto, mas **o registro da execução permanece**
em `resultados/` — resultado de execução paga não é descartado.

O 15º registro, `exemplo_mock`, não é um PDF: possui `fonte_url` e
`caminho_local` nulos deliberadamente, o que faz o executor rodar sobre o
artigo embutido em `src/examples/example_article.txt`. É o smoke test que
permite executar o pipeline sem rede, sem chave de API e sem custo.

---

## 8. Configuração do CI

*Alteração nos arquivos de configuração do CI, realizada mediante autorização
prévia.*

O passo `bandit -r src/` encerrava com código 1 independentemente do conteúdo
do PR, em razão de 843 alertas de severidade baixa — 835 deles `assert_used`.
Um check que falha invariavelmente deixa de ser lido.

A totalidade dos 835 `assert` está em diretórios de teste; nenhum em código de
produção. Por essa razão, optou-se por **excluir os diretórios de teste em vez
de desativar a regra**: `assert` em código de produção permanece um problema
legítimo, uma vez que o interpretador executado com `-O` remove os asserts
juntamente com a verificação que realizavam.

O passo foi desdobrado em dois: um gate em severidade média ou superior, que
bloqueia o merge, e um relatório completo informativo, que mantém os alertas
de severidade baixa visíveis no log sem impedir a integração.

### 8.1. Os passos de verificação deixam de se bloquear entre si

Constatado na primeira execução do CI sobre este PR (run `31821169112`): o job
falhou no passo 1, o Ruff, e **Bandit, Mypy e Pytest foram pulados**. Os passos
de um job são sequenciais, e o primeiro que sai com código 1 aborta os
seguintes.

| Passo | Resultado nessa execução |
|---|---|
| Run Ruff (Linter) | ❌ `Found 167 errors`, exit 1 |
| Run Bandit (gate MEDIUM+) | ⏭️ pulado |
| Bandit full report | ✅ aprovado (já possuía `if: always()`) |
| Run Mypy | ⏭️ pulado |
| **Run Pytest** | ⏭️ **pulado** |

A consequência é que **a suíte nunca é executada**. Enquanto a dívida de lint
herdada não for quitada, todo PR de qualquer grupo morre no primeiro passo, e
o CI não informa se os testes passam — que é justamente a pergunta que ele
existe para responder.

Os passos passaram a levar `if: always()`, como o relatório informativo do
Bandit já fazia. Os quatro checks passam a reportar de forma independente.

> [!IMPORTANT]
> **Nenhum gate foi enfraquecido.** Cada passo continua encerrando com código 1
> pelo seu próprio critério, e o job só fica verde quando os quatro passarem.
> `always()` altera apenas **quando** o passo executa, jamais se ele aprova.

### 8.2. O que a suíte revelou na primeira vez que rodou

Run `31823185874`, a primeira execução do projeto a alcançar o passo do Pytest:
**326 aprovados, 1 reprovado.**

```
test_llm_fallback.py::test_reserva_por_provedor_usa_modelo_padrao
AssertionError: assert 'sabiazinho-4' == 'sabia-3'
```

O teste estava defasado, não o código. O [PR #17](https://github.com/Agentes-Turing-2026/Pipeline_Peer_Review_Integrada/pull/17)
substituiu o `modelo_padrao` da maritaca de `sabia-3` para `sabiazinho-4`, por
descontinuação do primeiro, e atualizou `test_model_provider.py` e
`test_structured_output.py` — mas não `test_llm_fallback.py`, que seguiu
exigindo o modelo antigo.

A falha é herdada da `dev` e não decorre desta branch. Permaneceu invisível
porque o Ruff abortava o job antes do Pytest; foi o `if: always()` da seção
anterior que a tornou observável. Corrigida com o literal `"sabiazinho-4"`, o
mesmo que o PR #17 empregou nos testes irmãos que ajustou.

> [!NOTE]
> Essa correção pertencia ao escopo do Grupo 3 — o gerente havia apontado
> "referências antigas ao sabia-3 no exemplo de fallback, na documentação e em
> um teste". O teste foi corrigido aqui por bloquear o CI de todos os grupos.
> Permanece pendente com eles a linha 34 do `.env.example`, que ainda sugere
> `LLM_FALLBACK_MODEL=sabia-3`.

### 8.3. A régua do Ruff passa a ser fixada pelo repositório

O CI executava `ruff check src/` **sem nenhuma seção `[tool.ruff]` no
projeto** — ou seja, na régua padrão da versão da ferramenta que estivesse
instalada. O veredito do lint dependia da versão do ruff, não do código:

| Régua aplicada à mesma árvore | Apontamentos |
|---|---|
| Padrão do ruff 0.16 (o que o CI usava) | 167 |
| Padrão clássico documentado (`E4, E7, E9, F`) | 37 |

Dos 167, **85 eram `RUF100` ("unused noqa")** — os `# noqa: E402` presentes no
código. Foram escritos quando o projeto era verificado com E402 ativo; como o
padrão do 0.16 não ativa essa regra, cada comentário passou a ser reportado
como diretiva inútil. Outros ~34 vinham de regras opinativas (`SIM117`,
`BLE001`, `RUF059`, `S110`) que o projeto nunca escolheu adotar. Não era dívida
de código: era ausência de configuração.

O `select` adotado é o padrão **documentado** da ferramenta, e não um recorte
conveniente para o job ficar verde. `E402` fica em `ignore` por razão
estrutural: praticamente todo módulo do repositório executa `sys.path.insert()`
antes de importar os irmãos, de modo que o import fora do topo é o padrão do
projeto — o código já reconhecia isso nos 85 `noqa`, que permanecem inertes e
podem ser removidos por cada responsável ao passar pelo arquivo.

O Mypy passou a excluir os diretórios de teste, pela mesma lógica já aplicada
ao Bandit na §8: monkeypatch e stubs de teste produzem erros de tipo que não
indicam defeito em código de produção.

| Ferramenta | Antes | Depois |
|---|---|---|
| Ruff | 167 | **8** |
| Mypy | 38 | **32** |

---

## 9. Estado atual

Medido no run [`31834997779`](https://github.com/Agentes-Turing-2026/Pipeline_Peer_Review_Integrada/actions/runs/31834997779)
do CI, sobre o commit `a3d5969`:

| Passo do CI | Resultado |
|---|---|
| **Pytest** | ✅ **Aprovado — `327 passed`** |
| Bandit (gate MEDIUM+) | ✅ Aprovado |
| Bandit full report | ✅ Aprovado |
| Ruff | ❌ **8 erros** |
| Mypy | ❌ **32 erros em 6 arquivos** |

Trajetória do Ruff nesta entrega, medida com a versão exata do `poetry.lock`
(`0.16.2`) e conferida contra o log do CI:

| Estado | Apontamentos |
|---|---|
| `origin/dev` | 223 |
| Esta branch, antes da régua fixada | 167 |
| **Esta branch, estado atual** | **8** |

Os 223 coincidem com o número informado pelo supervisor quando o CI foi
introduzido.

> [!IMPORTANT]
> **Os 40 apontamentos restantes estão integralmente em arquivos de outros
> grupos, e nenhum foi tocado** — não há autorização para alterar código de
> origem alheia. Os arquivos do Grupo 2 estão zerados nas quatro ferramentas.

Distribuição do que resta, por responsável:

| Escopo | Ruff | Mypy | Arquivos |
|---|---|---|---|
| **Grupo 1** (validação, persistência, retomada) | 3 | 16 | `demos/demo_validacao.py` (3+7), `validacao_retry.py` (5), `demos/demo_persistencia.py` (2), `validacao_entrada.py` (1), `eventos_validacao.py` (1) |
| **Núcleo compartilhado** (sem dono definido) | 5 | 16 | `pipeline.py` (3+16), `editor_agent.py` (1), `cross_review.py` (1) |
| **Grupo 2** | **0** | **0** | — |

Os 8 apontamentos de Ruff são todos marcados `[*]`, isto é, corrigíveis por
`ruff check <arquivo> --fix`: 4 imports não utilizados, 3 f-strings sem
placeholder e 1 redefinição de `nullcontext`. Convém executar a suíte em
seguida — três deles são imports, e a remoção quebra qualquer módulo que
importe o nome por tabela.

Os 32 do Mypy são manuais, porém metade está em `pipeline.py` e repete dois
padrões: `Any | None` atribuído a campo tipado (`assignment`) e atributo
acessado sem verificação de `None` (`union-attr`).

### Pendências

As três primeiras são pré-requisito da quarta, nesta ordem:

- [ ] **Grupo 1 e responsável pelo núcleo:** os 40 apontamentos da tabela acima
      — com eles resolvidos, o job fica verde pela primeira vez
- [ ] **Grupo 3:** `.env.example` linha 34, que ainda sugere
      `LLM_FALLBACK_MODEL=sabia-3` (ver §8.2)
- [ ] **Marcelo:** validação das alterações em `ci.yml` e `pyproject.toml`
- [ ] **Nova execução do benchmark, com o corpus completo em modo `api`**

> [!IMPORTANT]
> A execução do benchmark ficou **deliberadamente para depois** que todos os
> grupos quitarem seus apontamentos e o CI estiver verde — decisão de sequência,
> não esquecimento.
>
> E não se trata de rodar apenas os 9 documentos novos por cima dos resultados
> atuais: será uma **rodada nova do benchmark inteiro, com todos os PDFs do
> corpus atualizado**. O motivo é que os números publicados só são comparáveis
> entre si se saírem da mesma versão do código — rodar os documentos novos
> agora produziria uma tabela metade medida antes das correções dos outros
> grupos e metade depois.
>
> A execução implica **custo de API**, então acontece uma vez, no momento certo.
> A flag `--regerar` (§4) existe justamente para que qualquer reajuste posterior
> de relatório seja feito sobre os pares já gravados, sem novo consumo.

---

## 10. Observação de ambiente

Com o repositório situado em pasta sincronizada pelo OneDrive no Windows,
aproximadamente 1 execução da suíte em 5 falha com
`PermissionError: [WinError 5]` no `os.replace` de `src/persistencia.py:67`,
durante a escrita atômica do checkpoint.

A condição foi verificada, não presumida: com as alterações desta branch
guardadas em stash, a `origin/dev` limpa apresenta a mesma taxa de falha.
**Não se trata de regressão desta entrega** e não afeta o CI, que executa em
`ubuntu-latest` — lá a suíte fecha em `327 passed`, sem intermitência.

> [!NOTE]
> **Onde o sintoma aparece não é onde está a causa.** O teste que reprova é
> `src/benchmark/tests/test_executar.py::test_processar_documento_mock_registra_cross_review_enabled_true_por_default`,
> um arquivo do Grupo 2, com a mensagem `assert 'falha_execucao' == 'sucesso'`.
> A causa está na escrita atômica do checkpoint: o teste executa o pipeline
> completo, e a falha de `os.replace` faz a execução inteira ser marcada como
> `falha_execucao`. **O benchmark não está quebrado.**
>
> Vale registrar o motivo de o `tmp_path` do pytest não proteger contra isso: o
> teste passa `cache_dir=tmp_path`, mas os checkpoints são gravados em
> `src/logs/checkpoints/` — caminho fixo dentro do repositório, portanto dentro
> da pasta sincronizada, independentemente do diretório temporário do teste.

Recomenda-se que a depuração de persistência no Windows seja feita fora de
diretórios sincronizados.
