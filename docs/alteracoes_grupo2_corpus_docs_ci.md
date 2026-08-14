# Grupo 2 — Alterações desta entrega

Branch `grupo-2-corpus-publico-docs-e-ci`, criada a partir de `dev` — e é para
`dev` que o pull request é aberto: o CI roda lá e, com tudo verde, `dev` segue
para a `main`.

Este documento apresenta, para cada ponto levantado nesta rodada, **o estado
anterior e o estado atual**. As seções seguem a ordem da apresentação.

| Indicador | Antes | Depois |
|---|---|---|
| Pontos levantados e atendidos | — | **6 de 6** |
| Testes da suíte | 274 passando | **280 passando** |
| Ruff em `metrics` + `benchmark` | 50 erros | **0** |
| Mypy em `metrics` + `benchmark` | 3 erros | **0** |
| Bandit — alertas de severidade média | 1 | **0** |
| Documentos no corpus | 10 | **15** |
| Documentos reprodutíveis por terceiros | 6 de 10 | **14 de 15** |

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

---

## 9. Estado atual

| Passo do CI | Resultado | Atribuição |
|---|---|---|
| Pytest | Aprovado — 280 passando, 9 pulados | — |
| Bandit | Aprovado | Corrigido nesta entrega |
| Ruff | **167 erros** | 0 no Grupo 2 · 99 compartilhado · 42 Grupo 1 · 26 Grupo 3 |
| Mypy | **73 erros** | 0 no Grupo 2 · 39 compartilhado · 25 Grupo 1 · 9 Grupo 3 |

Comparação medida com o Ruff `0.16.2` — a versão exata do `poetry.lock` —
executado sobre as duas árvores:

| Árvore | Apontamentos do Ruff |
|---|---|
| `origin/dev` | **223** |
| Esta branch | **167** |

Os 223 coincidem com o número informado pelo supervisor quando o CI foi
introduzido. Esta entrega reduz 56 apontamentos e não acrescenta nenhum.

> [!WARNING]
> Ruff e Mypy permanecem reprovados, **e isso não decorre desta entrega**: os
> arquivos do Grupo 2 estão zerados nas quatro ferramentas. Os erros restantes
> concentram-se em arquivos compartilhados — `pipeline.py` (21 Ruff + 16
> Mypy), `tests/test_retomada_confiavel.py` (15), `llm_fallback.py` (7 Mypy),
> `demos/demo_validacao.py` (7 Mypy) — e nos módulos dos Grupos 1 e 3. A
> definição de responsabilidade sobre os arquivos compartilhados depende de
> coordenação entre os grupos.

### Pendências

- [ ] Revisão pelo Grupo 2 e abertura do PR — destino **`dev`**, não `main`
- [ ] Coordenação sobre Ruff e Mypy nos arquivos compartilhados e dos Grupos 1 e 3
- [ ] Execução dos 9 documentos novos do corpus em modo `api` (implica custo)

---

## 10. Observação de ambiente

Com o repositório situado em pasta sincronizada pelo OneDrive no Windows,
aproximadamente 1 execução da suíte em 5 falha com
`PermissionError: [WinError 5]` no `os.replace` de `src/persistencia.py:67`,
durante a escrita atômica do checkpoint.

A condição foi verificada, não presumida: com as alterações desta branch
guardadas em stash, a `origin/dev` limpa apresenta a mesma taxa de falha.
**Não se trata de regressão desta entrega** e não afeta o CI, que executa em
`ubuntu-latest`. Recomenda-se que a depuração de persistência no Windows seja
feita fora de diretórios sincronizados.
