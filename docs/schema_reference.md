# Scoring — Schema de Avaliação dos Revisores

Esta pasta é responsável **exclusivamente** pela parte de notas do sistema de
peer review: o *schema de avaliação* (`review_schema.py`), os *agentes
revisores* (`reviewer_agent.py`) que produzem pareceres já no formato desse
schema, e a *leitura cruzada* (`cross_review.py`) — uma segunda fase em que os
revisores leem os argumentos uns dos outros antes do Editor-Chefe. Ela entrega
uma estrutura de dados única, validável e reutilizável para as demais partes do
projeto (`debate/`, `editor/`, `pipeline/`).

> Conteúdo desta pasta: o schema (`review_schema.py`), os agentes revisores
> adaptados ao schema (`reviewer_agent.py`), a segunda fase de leitura cruzada
> (`cross_review.py`), exemplos (`examples/`), os outputs de execução
> (`outputs/`), os logs (`logs/`) e este README.

> **Duas fases.** Seções 1–8 documentam a **Fase 1** (avaliação independente e
> estruturada). A **Fase 2** (leitura cruzada entre revisores) está na
> [seção 9](#9-fase-2--leitura-cruzada-entre-revisores).

---

## 1. Que problema o schema resolve?

No sistema anterior (`Atividade_2`), o formato de saída de cada revisor era
descrito **apenas no texto do prompt** e parseado de forma tolerante por uma
função `_extract_json`. Isso trazia uma série de fragilidades:

| Problema no sistema anterior | Como o schema resolve |
|---|---|
| O formato vivia duplicado no prompt de cada revisor (statistician, domain_expert, copyeditor), podendo divergir entre si. | Uma **única fonte de verdade** (`ReviewSchema`) importável por todos os revisores. |
| Notas eram um campo `score` inteiro livre, sem faixa garantida nem justificativa associada. | Cada critério exige **nota dentro da faixa** + **justificativa obrigatória**. |
| Um JSON sem um campo, com nota fora da escala ou com justificativa vazia passava silenciosamente. | A validação Pydantic **rejeita** o output inválido com erros explícitos. |
| Não havia critérios de avaliação padronizados; cada revisor listava `strengths`/`weaknesses` soltas. | Quatro critérios fixos e comparáveis: solidez técnica, originalidade, significância e clareza. |
| Decisões (score/recommendation) podiam vir sem explicação. | Nota geral e confiança **sempre** acompanhadas de justificativa. |

Em resumo: deixamos de confiar que o LLM "vai seguir o prompt" e passamos a
**garantir e validar** a estrutura.

---

## 2. Descrição dos campos

O schema principal é `ReviewSchema` (em [`review_schema.py`](review_schema.py)).

### Campo identificador

| Campo | Tipo | Descrição |
|---|---|---|
| `revisor` | `str` (não vazio) | Identifica quem emitiu o parecer (`"statistician"`, `"domain_expert"`, `"copyeditor"`). |

### Critérios de avaliação

Cada um dos quatro critérios é um objeto `CriterionEvaluation` com **nota** e
**justificativa**:

| Critério | O que avalia |
|---|---|
| `solidez_tecnica` | Validade dos métodos, rigor científico, suporte empírico ou teórico das afirmações. |
| `originalidade` | Novidade da contribuição, diferenciação do estado da arte. |
| `significancia` | Impacto potencial, relevância para a comunidade, avanço real do campo. |
| `clareza` | Qualidade da escrita, organização, reprodutibilidade. |

Cada critério tem:

- `nota` — inteiro na **escala 1–4**:
  - `4` = Excelente · `3` = Bom · `2` = Regular · `1` = Fraco
- `justificativa` — texto **obrigatório e não vazio** explicando a nota.

### Síntese do parecer

| Campo | Tipo | Escala / regra |
|---|---|---|
| `nota_geral` | `OverallEvaluation` (`nota` + `justificativa`) | **1–4**: `4`=Aceitar, `3`=Aceitar com ressalvas, `2`=Rejeitar com ressalvas, `1`=Rejeitar. A justificativa deve explicar a decisão **considerando os quatro critérios**. |
| `confianca` | `ConfidenceEvaluation` (`nota` + `justificativa`) | **1–3**: `3`=Confiante, `2`=Moderadamente confiante, `1`=Pouco confiante. A justificativa deve explicar **o porquê do nível de confiança**. |

### Regras de validação garantidas

- Cada critério tem obrigatoriamente `nota` (inteiro na faixa) **e** `justificativa`.
- Nenhuma justificativa pode ser vazia ou conter apenas espaços — caso contrário o output é **inválido**.
- `nota_geral` e `confianca` têm justificativa obrigatória.
- Notas fora das faixas (`1–4` para critérios e nota geral, `1–3` para confiança) são rejeitadas.
- Campos extras não previstos são rejeitados (`extra="forbid"`).

As escalas também estão disponíveis como constantes reutilizáveis:
`ESCALA_CRITERIOS`, `ESCALA_NOTA_GERAL`, `ESCALA_CONFIANCA`.

---

## 3. Exemplo de uso

### 3.1 Validando um output (editor / pipeline)

```python
from review_schema import validar_review

parecer = {
    "revisor": "statistician",
    "solidez_tecnica": {"nota": 3, "justificativa": "Métodos adequados, mas n pequeno."},
    "originalidade":   {"nota": 2, "justificativa": "Combina técnicas já consolidadas."},
    "significancia":   {"nota": 3, "justificativa": "Relevante para a prática clínica."},
    "clareza":         {"nota": 4, "justificativa": "Texto bem organizado e reprodutível."},
    "nota_geral":      {"nota": 3, "justificativa": "Aceitar com ressalvas: ampliar amostra."},
    "confianca":       {"nota": 3, "justificativa": "Tema dentro da minha especialidade."},
}

review = validar_review(parecer)   # levanta ValidationError se inválido
print(review.nota_geral.nota)       # 3
```

### 3.2 Acoplando a um agente revisor (Google ADK)

O schema é um modelo Pydantic, então integra-se ao padrão **`output_key`** já
usado no projeto através do parâmetro **`output_schema`** do `LlmAgent`. O ADK
passa a forçar o modelo a responder nessa estrutura e a valida automaticamente:

```python
from google.adk.agents import LlmAgent
from review_schema import ReviewSchema

statistician_agent = LlmAgent(
    name="statistician_reviewer",
    model="gemini-2.0-flash",
    output_key="statistician_review",   # grava no session state (padrão do projeto)
    output_schema=ReviewSchema,          # força + valida a estrutura de notas
    instruction=STATISTICIAN_PROMPT,
)
```

> Observação para quem for integrar: no ADK, ao definir `output_schema`, o agente
> produz JSON estruturado e **não** usa ferramentas/transfer no mesmo passo —
> o que é adequado para os revisores, que apenas emitem o parecer.

### 3.3 Exemplos prontos

- [`examples/example_valid_output.json`](examples/example_valid_output.json) — parecer completo e válido.
- [`examples/example_invalid_output.json`](examples/example_invalid_output.json) — viola de propósito 4 regras: nota fora da faixa (`originalidade.nota = 5`), justificativa vazia (`significancia`), confiança fora da faixa (`confianca.nota = 0`) e justificativa de confiança ausente.

---

## 4. O que esta parte entrega para as outras

| Consumidor | O que recebe daqui |
|---|---|
| **`editor/`** (editor-chefe) | `ReviewSchema` validado de cada revisor, com critérios comparáveis e justificativas garantidas para sintetizar o veredito final. |
| **`debate/`** | Estrutura padronizada de notas + justificativas que serve de base para os revisores confrontarem posições de forma objetiva. |
| **`pipeline/`** | A função `validar_review(data)` para checar saídas antes de avançar, e `ReviewSchema` para acoplar via `output_schema` aos `LlmAgent`. |

**Contrato público deste módulo** (importável de `review_schema`):

- `ReviewSchema` — modelo principal do parecer de um revisor.
- `CriterionEvaluation`, `OverallEvaluation`, `ConfidenceEvaluation` — blocos `nota` + `justificativa`.
- `validar_review(data: dict) -> ReviewSchema` — valida um dicionário.
- `json_schema() -> dict` — JSON Schema (útil para documentação/prompts).
- `ESCALA_CRITERIOS`, `ESCALA_NOTA_GERAL`, `ESCALA_CONFIANCA` — escalas legíveis.

---

## 5. Agentes revisores (`reviewer_agent.py`)

O arquivo [`reviewer_agent.py`](reviewer_agent.py) adapta os três revisores da
atividade anterior (`statistician`, `domain_expert`, `copyeditor`) para o novo
schema. As **personas** foram preservadas; o que mudou foram os **prompts** e o
acoplamento ao schema.

### 5.1 O que foi alterado nos prompts

| Antes (`Atividade_2`) | Agora (`reviewer_agent.py`) |
|---|---|
| Cada revisor tinha um JSON livre próprio com `strengths`, `weaknesses`, `critical_issues`, um `score` inteiro solto e `recommendation`. | Um **prompt único** (`REVIEWER_PROMPT_TEMPLATE`) com a persona injetada, avaliando as **quatro dimensões** na ordem fixa: Solidez Técnica → Originalidade → Significância → Clareza. |
| A nota era um número sem justificativa associada. | Cada dimensão exige **nota 1–4** (1=Fraco … 4=Excelente) **+ justificativa de no mínimo 2 frases**, ancorada no conteúdo real do artigo. |
| Não havia decisão editorial padronizada nem confiança. | Exige **nota geral 1–4** (1=Rejeitar … 4=Aceitar) justificada a partir dos quatro critérios, e **confiança 1–3** (1=Pouco … 3=Confiante) justificada. |
| O formato só "existia" no texto do prompt; nada validava a saída. | O `LlmAgent` usa `output_schema=ReviewSchema` + `output_key`, então o ADK **força e valida** a estrutura. |
| — | Regras explícitas no prompt: **não inventar** dados ausentes (tratar ausência como limitação), **coerência** entre nota geral e critérios, e **manter a persona** do início ao fim. |

O prompt mantém a convenção do projeto: `{article_text}` é injetado pelo ADK a
partir do *state* da sessão, e as chaves `{{ }}` do exemplo JSON são literais.

### 5.2 Contrato público de `reviewer_agent.py`

- `build_reviewer_prompt(reviewer_id)` — monta o prompt final de um revisor.
- `build_reviewer_agent(reviewer_id)` — cria o `LlmAgent` (com `output_schema`).
- `build_all_reviewers()` — retorna os três agentes prontos para orquestração.
- `run_demo()` — executa os três revisores sobre o artigo de exemplo e salva o resultado validado.
- `REVIEWERS`, `MODEL`, `REVIEWER_PROMPT_TEMPLATE` — configuração e personas.

---

## 6. Configuração da API key

O sistema usa as APIs **reais** do Google ADK / Gemini — não há mocks nem
fallbacks. Se a `GOOGLE_API_KEY` não estiver configurada, a demonstração falha
com uma mensagem clara.

1. Na **raiz do projeto**, copie o template e preencha a sua chave:

   ```bash
   cp .env.example .env
   ```

2. Edite o `.env` e informe a chave do Gemini:

   ```env
   GOOGLE_API_KEY=coloque_sua_chave_real_aqui
   GOOGLE_GENAI_USE_VERTEXAI=FALSE
   GEMINI_MODEL=gemini-2.0-flash
   ```

   O `.env` está no `.gitignore` (não é versionado); o `.env.example` é
   versionado como template.

---

## 7. Como rodar a demonstração

Após configurar o `.env` (seção 6), a partir desta pasta:

```bash
python reviewer_agent.py
```

A demonstração:
1. carrega o artigo em [`examples/example_article.txt`](examples/example_article.txt);
2. roda os três revisores em paralelo (`ParallelAgent`) sobre o artigo;
3. **valida cada parecer** contra o `ReviewSchema` (`validar_review`);
4. salva o resultado em `outputs/sample_run_output.json`.

Sem a chave configurada, o comando interrompe com:

```
RuntimeError: GOOGLE_API_KEY não configurada. Copie '.env.example' para '.env' ...
```

> `outputs/sample_run_output.json` é **gerado ao rodar** com uma chave válida —
> por isso ainda não está presente no repositório.

### 7.1 Exemplo do output esperado

Cada revisor produz um objeto no formato de
[`examples/example_valid_output.json`](examples/example_valid_output.json), e o
arquivo final agrupa os três pareceres por `output_key`:

```json
{
  "article_file": "examples/example_article.txt",
  "model": "gemini-2.0-flash",
  "reviews": {
    "statistician_review": {
      "revisor": "statistician",
      "solidez_tecnica": { "nota": 2, "justificativa": "..." },
      "originalidade":   { "nota": 3, "justificativa": "..." },
      "significancia":   { "nota": 3, "justificativa": "..." },
      "clareza":         { "nota": 4, "justificativa": "..." },
      "nota_geral":      { "nota": 3, "justificativa": "..." },
      "confianca":       { "nota": 3, "justificativa": "..." }
    },
    "domain_expert_review": { "...": "..." },
    "copyeditor_review":    { "...": "..." }
  }
}
```

O artigo de exemplo (uma *LightRetinaNet* para triagem de retinopatia diabética)
foi escrito de propósito com pontos fortes (eficiência, clareza, limitações
reconhecidas) e fraquezas (dados de um único hospital, rótulos de um único
especialista, sem validação externa, sem intervalos de confiança) — material
suficiente para que cada persona atribua notas diferenciadas e bem justificadas.

---

## 8. Como o output é consumido a jusante

Esta parte (`scoring/`) é a **fonte dos pareceres individuais**. Os pareceres
validados seguem para:

- **`debate/`** — recebe os pareceres dos três revisores (notas + justificativas
  por critério) como base para confrontar divergências. Por serem comparáveis
  dimensão a dimensão, fica direto identificar onde os revisores discordam (ex.:
  `originalidade` 2 vs. 3) e alimentar uma rodada de debate.
- **`editor/`** (editor-chefe) — recebe os pareceres já estruturados e validados
  para sintetizar o veredito final, sem precisar reparsear texto livre nem
  adivinhar campos. As escalas padronizadas permitem agregar as notas de forma
  consistente.

Em ambos os casos, o consumidor pode reaproveitar `validar_review(data)` para
garantir que o que chega está bem-formado antes de processar.

---

## 9. Fase 2 — Leitura cruzada entre revisores

> Código: [`cross_review.py`](cross_review.py) · Schema: `CrossReviewSchema` em
> [`review_schema.py`](review_schema.py) · Exemplo:
> [`examples/example_cross_review_output.json`](examples/example_cross_review_output.json) ·
> Log: [`logs/cross_review.log`](logs/cross_review.log).

### 9.1 Que problema a leitura cruzada resolve?

Até a Fase 1, cada revisor gera o seu parecer de forma **completamente isolada**
e o passa adiante. Não há nenhuma troca entre os revisores antes do
Editor-Chefe. Isso traz duas fragilidades:

| Problema do isolamento total | Como a leitura cruzada resolve |
|---|---|
| Cada revisor enxerga só uma fatia do artigo; um ponto cego de um nunca é confrontado pelo argumento do outro. | Cada revisor lê os **argumentos** dos colegas e pode corrigir uma avaliação feita por falta de informação. |
| Divergências (ex.: `significância` 3 vs. 4) chegam cruas ao Editor-Chefe, que tem de adivinhar quem tem razão. | As divergências passam por uma rodada de confronto **antes** do editor; o que sobrevive já vem com a posição testada. |
| Não há rastro de *por que* um revisor pensa o que pensa frente aos demais. | A saída registra explicitamente se houve mudança e **qual argumento** foi decisivo. |

O objetivo **não** é forçar consenso. É aumentar a **consistência**: notas que
mudam, mudam por um argumento concreto; notas que ficam, ficam por resistência
fundamentada.

### 9.2 Como funciona

Depois da avaliação independente, roda-se uma segunda fase (também um
`ParallelAgent` do ADK) em que cada revisor recebe:

1. **o seu próprio parecer original** (com notas e justificativas);
2. **os argumentos dos outros revisores** — *apenas as justificativas, nunca as
   notas*.

Esconder as notas dos colegas é deliberado: evita ancoragem numérica ("vou
chegar perto da média") e força a mudança a ser motivada por um **argumento**.

O prompt aplica **resistência controlada**: o revisor é instruído a *não* ceder
por pressão social nem para fechar consenso — só revisa uma nota diante de um
argumento concreto, ancorado no artigo, que ele não havia considerado. Essa
postura segue **Du et al. (2023)**, que mostra que, no debate multiagente, a
divergência produtiva melhora a qualidade quando os agentes resistem a concordar
sem fundamento.

Cada revisor grava o parecer atualizado no estado da sessão via `output_key`
(`statistician_cross_review`, `domain_expert_cross_review`,
`copyeditor_cross_review`), exatamente como a Fase 1 grava `*_review`.

```python
# cross_review.py (resumo)
LlmAgent(
    name="statistician_reviewer_cross",
    model=MODEL,
    output_key="statistician_cross_review",  # parecer revisado no state
    output_schema=CrossReviewSchema,          # força + valida a estrutura
    instruction=build_cross_reviewer_prompt("statistician"),
)
```

### 9.3 Schema da saída (`CrossReviewSchema`)

| Campo | Tipo | Regra |
|---|---|---|
| `revisor` | `str` | Identifica o revisor; deve casar com o do parecer revisado. |
| `parecer_revisado` | `ReviewSchema` | Parecer **final** no mesmo formato da Fase 1 — o Editor-Chefe consome igual. |
| `mudou_posicao` | `bool` | `True` sse ao menos uma nota foi revisada. |
| `mudancas` | `list[CriterionRevision]` | Uma entrada por critério alterado: `criterio`, `nota_anterior`, `nota_nova`, `argumento_decisivo`, `justificativa`. Vazia quando nada mudou. |
| `resposta_aos_pares` | `str` | Texto obrigatório respondendo aos colegas (o que acatou / rejeitou). |

Validações garantidas (rejeitam a saída se violadas):

- `mudou_posicao=True` ⇒ `mudancas` não vazia; `mudou_posicao=False` ⇒ `mudancas` vazia.
- Em cada mudança, `nota_anterior != nota_nova` (critérios mantidos não entram em `mudancas`).
- A `nota_nova` de cada mudança **bate** com a nota correspondente em `parecer_revisado` (impede registrar uma mudança que não se reflete no parecer final).
- Não há duas mudanças para o mesmo critério; `argumento_decisivo` e `justificativa` não podem ser vazios.

### 9.4 Exemplo de entrada e saída (mudança de posição)

**Entrada da Fase 2** (o que o `statistician` recebe — argumentos dos colegas, *sem* as notas):

```
REVISOR 'copyeditor' argumenta:
  - Clareza: A escrita do artigo é clara, concisa e mantém um tom acadêmico
    adequado, com boa coesão entre os parágrafos e seções. ...
  - ...
```

**Saída** (trecho de [`example_cross_review_output.json`](examples/example_cross_review_output.json)):

```json
"statistician_cross_review": {
  "revisor": "statistician",
  "parecer_revisado": { "...": "clareza agora 3, demais notas mantidas" },
  "mudou_posicao": true,
  "mudancas": [
    {
      "criterio": "clareza",
      "nota_anterior": 2,
      "nota_nova": 3,
      "argumento_decisivo": "O copyeditor argumentou que, do ponto de vista textual, a escrita é clara e coesa ... — separando clareza de ESCRITA de completude ESTATÍSTICA.",
      "justificativa": "Eu havia misturado a falta de detalhe estatístico (que pertence à solidez técnica) com a clareza do texto. Por isso subo clareza de 2 para 3, sem mexer na solidez técnica."
    }
  ],
  "resposta_aos_pares": "Acato o argumento do copyeditor sobre clareza textual ... mas mantenho minha recomendação geral de rejeitar com ressalvas."
}
```

No mesmo exemplo, `domain_expert` e `copyeditor` têm `mudou_posicao=false`:
o `domain_expert` **resiste** ao argumento do estatístico (mantém
`significância` em 4, explicando que rigor metodológico e relevância do problema
são dimensões distintas). É a resistência controlada em ação.

### 9.5 Estado da sessão antes e depois (logs)

[`logs/cross_review.log`](logs/cross_review.log) registra o estado das notas
antes e depois da Fase 2. No exemplo acima:

```
ESTADO DA SESSÃO — ANTES da leitura cruzada (fase 1)
  statistician: {'solidez_tecnica': 2, 'originalidade': 3, 'significancia': 3, 'clareza': 2, 'nota_geral': 2, 'confianca': 3}
  ...
ESTADO DA SESSÃO — DEPOIS da leitura cruzada (fase 2)
  statistician: {'solidez_tecnica': 2, 'originalidade': 3, 'significancia': 3, 'clareza': 3, 'nota_geral': 2, 'confianca': 3}
  ...
  statistician: mudou_posicao=True | mudancas=['clareza']
  domain_expert: mudou_posicao=False | mudancas=[]
  copyeditor: mudou_posicao=False | mudancas=[]
```

A única nota que mudou foi `statistician.clareza` (2 → 3), e o log diz
exatamente quem mudou e em qual critério.

### 9.6 Como rodar

```bash
python cross_review.py
```

A demonstração roda a Fase 1 (três revisores independentes) e, em seguida, a
Fase 2 (leitura cruzada), logando o estado antes/depois em
`logs/cross_review.log` e salvando o resultado validado em
`outputs/sample_cross_review_output.json`. Assim como na Fase 1, requer
`GOOGLE_API_KEY` configurada (sem mocks).

> O exemplo `examples/example_cross_review_output.json` é **ilustrativo** (escrito
> à mão, como `example_valid_output.json`) e serve para demonstrar o formato e um
> caso de mudança de posição sem depender de uma chave de API.

### 9.7 Como a Fase 2 se conecta ao Editor-Chefe

O Editor-Chefe passa a ler o **`parecer_revisado`** de cada
`*_cross_review` no lugar do `*_review` da Fase 1 — e nada mais muda para ele,
porque `parecer_revisado` é um `ReviewSchema` idêntico ao da Fase 1
(`validar_review` continua valendo). Em termos de orquestração:

```
ParallelAgent (Fase 1)        ParallelAgent (Fase 2)            Editor-Chefe
 *_review no state    ─────►   lê argumentos dos pares   ─────►  lê *_cross_review
                               grava *_cross_review              .parecer_revisado
```

Ganho para o editor: ele recebe pareceres **já confrontados entre si**. Onde
houve convergência, a decisão final fica mais segura; onde a divergência
persistiu, ela vem acompanhada do registro de *que argumento foi oferecido e por
que foi rejeitado* (`resposta_aos_pares` + `mudancas`), o que torna a síntese
final mais transparente e auditável do que partir de três pareceres isolados.
