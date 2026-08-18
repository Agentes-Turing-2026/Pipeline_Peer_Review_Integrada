"""Tabela de preços oficiais por modelo — ativa o custo estimado (Grupo 2).

``adk_usage.py`` já sabia CALCULAR custo (``estimar_custo_usd``) a partir de
tokens reais e de um preço configurado, mas nada no pipeline chamava
``precos_de_ambiente()`` — sem a variável de ambiente configurada manualmente,
``custo_estimado`` ficava sempre ``None`` (ver ``docs/metricas_reference.md
§5.3`` e ``docs/benchmark_reference.md §7``). Este módulo fecha essa lacuna
com uma tabela de preços PÚBLICOS/oficiais dos modelos que ``model_provider.py``
já sabe construir (Gemini, OpenAI, Maritaca), para que rodar o pipeline em modo
``api`` já produza custo sem exigir configuração manual extra.

Precedência ao resolver o preço de uma execução (``resolver_precos``):

1. Variável de ambiente (``GRUPO2_PRECO_USD_MILHAO_TOKENS_ENTRADA``/``_SAIDA``,
   lidas por ``precos_de_ambiente()``) — SEMPRE vence quando as duas estão
   presentes. É o único jeito de corrigir um preço que as fontes abaixo não
   têm ou têm errado, sem editar código.
2. ``litellm.model_cost`` (``_preco_via_litellm``), SE o pacote ``litellm``
   estiver instalado. ``litellm`` já é dependência do projeto para os
   provedores Maritaca/OpenAI (``model_provider.py``) — aqui só REAPROVEITAMOS
   a tabela de preços que ele já mantém (milhares de modelos, atualizada a
   cada release do pacote), em vez de depender só da tabela estática abaixo.
   Sem ``litellm`` instalado (ex.: setup só-Gemini/mock), este passo é um
   no-op silencioso.
3. Tabela oficial deste módulo (``TABELA_PRECOS_OFICIAIS``), indexada por
   ``(provedor, prefixo do modelo)`` — cobre pelo menos os defaults de cada
   provedor, inclusive a Maritaca, que ``litellm.model_cost`` NÃO tem nenhuma
   entrada (verificado em 2026-08-06 — nenhuma chave "sabia*"/"maritaca*").
4. ``None`` — modelo não reconhecido em nenhuma fonte e nenhum preço
   configurado: custo permanece indisponível (nunca vira 0 por omissão, mesma
   regra de ``estimar_custo_usd``).

Fontes oficiais consultadas em 2026-08-06 (USD por milhão de tokens):

- Gemini 2.5 Flash / 2.0 Flash: https://ai.google.dev/gemini-api/docs/pricing
  (camada "Standard", paga; a entrada considerada é texto).
- OpenAI gpt-4o-mini / gpt-4o: https://openai.com/api/pricing (fetch direto
  bloqueado por 403 no momento da consulta; valores confirmados por agregadores
  independentes — OpenRouter, pricepertoken.com — que refletem a tabela
  oficial: gpt-4o-mini US$0,15/US$0,60; gpt-4o US$2,50/US$10,00).
- OpenAI gpt-5.6-luna: https://openai.com/index/advancing-the-price-performance-frontier-with-gpt-5-6/
  (fetch direto bloqueado por 403; preço padrão pós-corte de 30/07/2026,
  confirmado por CNBC, Axios, VentureBeat e por ``litellm.model_cost``, todos
  convergindo em US$0,20/US$1,20 — a OpenRouter mostra US$0,10/US$0,60, mas
  marcado como promoção própria da plataforma "50% off", não o preço-lista da
  OpenAI). GPT-5.6 é um lançamento posterior ao corte de conhecimento deste
  agente (ver nota de correção abaixo).
- Maritaca Sabiá 4 / Sabiazinho 4: https://docs.maritaca.ai/pt/precos (preços
  em R$; convertidos para USD pela cotação aproximada informada na própria
  página, US$1 ≈ R$5,14). Sabiá-3 não aparece mais na tabela oficial atual
  (só Sabiá 4 e variantes) — mantido aqui como aproximação a partir do preço
  de lançamento (R$5,00/R$10,00 por milhão de tokens). Ele deixou de ser o
  default do provedor: o Grupo 3 trocou
  ``model_provider.PROVEDORES[Provider.MARITACA].modelo_padrao`` para
  ``"sabiazinho-4"`` no PR #17, justamente por descontinuação. A entrada de
  ``sabia-3`` permanece na tabela porque execuções antigas já registradas
  citam esse modelo e precisam continuar precificáveis.

**Correção (2026-08-06): "gpt-5.6-luna" NÃO é um gateway/proxy.** Uma versão
anterior deste módulo (e de ``docs/benchmark_reference.md``/
``docs/metricas_reference.md``) especulava que ``modelo_usado="gpt-5.6-luna"``
— visto num registro real do benchmark, com o provedor configurado como
``openai`` — era um identificador de build/gateway que não batia com nenhum
modelo público. Essa suposição era baseada no corte de conhecimento deste
agente (anterior ao lançamento do GPT-5.6) e estava ERRADA: confirmado por
chamada real à API (com uma chave fornecida para teste) e por busca na
documentação oficial, "GPT-5.6 Luna" é um modelo real e atual da OpenAI (a
variante mais rápida/barata da família GPT-5.6, lançada em 2026-07-09). A
tabela abaixo já reflete isso.

**O princípio de design permanece válido, mesmo com a correção acima.** A
tabela é indexada pelo modelo CONFIGURADO (``model_provider.resolver_config``),
não pelo ``model_version`` cru que o ADK devolve no evento — os dois AINDA
PODEM divergir em outros cenários reais (um gateway/proxy que de fato
reescreve o nome do modelo na resposta continua sendo possível, só não era o
caso do "gpt-5.6-luna"). Precificar pelo nome configurado é o que faz
sentido: é o modelo que a chave de API de fato contratou, e é estável mesmo
quando o provedor devolve um identificador de build/snapshot diferente. Se o
provedor da execução for um gateway com tabela de preço PRÓPRIA (diferente da
pública), configure as variáveis de ambiente (prioridade 1 acima) em vez de
confiar neste fallback.

Câmbio e preços mudam com o tempo — trate os valores abaixo como referência
documentada, não como fonte viva. Divergiu do que a conta realmente cobra?
Configure o preço via ambiente; não edite os números aqui "para bater".
"""

from __future__ import annotations

from typing import Any

from .adk_usage import PrecosModelo, precos_de_ambiente

#: Cotação aproximada usada para converter os preços da Maritaca (R$) para USD,
#: conforme informado em https://docs.maritaca.ai/pt/precos em 2026-08-06.
_USD_POR_BRL = 1 / 5.14


def _preco_brl(entrada_brl: float, saida_brl: float) -> PrecosModelo:
    return PrecosModelo(
        usd_por_milhao_tokens_entrada=entrada_brl * _USD_POR_BRL,
        usd_por_milhao_tokens_saida=saida_brl * _USD_POR_BRL,
    )


#: (provedor.value, prefixo do id do modelo, preço, fonte documentada).
#: O prefixo é comparado com ``str.startswith`` — cobre variantes com sufixo de
#: data/build (ex.: um futuro "gemini-2.5-flash-002" ainda casa com
#: "gemini-2.5-flash"). Em caso de mais de um prefixo compatível, vence o mais
#: específico (o mais longo) — ver ``preco_oficial``.
TABELA_PRECOS_OFICIAIS: tuple[tuple[str, str, PrecosModelo, str], ...] = (
    (
        "gemini", "gemini-2.5-flash",
        PrecosModelo(usd_por_milhao_tokens_entrada=0.30, usd_por_milhao_tokens_saida=2.50),
        (
            "https://ai.google.dev/gemini-api/docs/pricing — Gemini 2.5 Flash, "
            "standard, texto (consultado em 2026-08-06)."
        ),
    ),
    (
        "gemini", "gemini-2.0-flash",
        PrecosModelo(usd_por_milhao_tokens_entrada=0.10, usd_por_milhao_tokens_saida=0.40),
        (
            "https://ai.google.dev/gemini-api/docs/pricing — Gemini 2.0 Flash, "
            "standard, texto (consultado em 2026-08-06; modelo com fim de vida "
            "anunciado para 2026-06-01, mantido para precificar execuções antigas)."
        ),
    ),
    (
        "openai", "gpt-4o-mini",
        PrecosModelo(usd_por_milhao_tokens_entrada=0.15, usd_por_milhao_tokens_saida=0.60),
        (
            "https://openai.com/api/pricing — gpt-4o-mini (consultado em "
            "2026-08-06; modelo padrão do provedor OpenAI em model_provider.py)."
        ),
    ),
    (
        "openai", "gpt-4o",
        PrecosModelo(usd_por_milhao_tokens_entrada=2.50, usd_por_milhao_tokens_saida=10.00),
        (
            "https://openai.com/api/pricing — gpt-4o (consultado em 2026-08-06; "
            "citado como exemplo de override em README.md §2.3)."
        ),
    ),
    (
        "openai", "gpt-5.6-luna",
        PrecosModelo(usd_por_milhao_tokens_entrada=0.20, usd_por_milhao_tokens_saida=1.20),
        (
            "https://openai.com/index/advancing-the-price-performance-frontier-with-gpt-5-6/ "
            "— GPT-5.6 Luna, preço padrão pós-corte de 30/07/2026 (lançamento em "
            "01/06/2026 era US$1,00/US$6,00; confirmado por CNBC/Axios/VentureBeat "
            "e por litellm.model_cost, consultado em 2026-08-06). Verificado como "
            "modelo real via chamada de teste com chave fornecida pelo usuário — "
            "NÃO é um gateway/proxy, ver nota de correção no docstring do módulo."
        ),
    ),
    (
        "maritaca", "sabiazinho-4",
        _preco_brl(1.00, 4.00),
        (
            "https://docs.maritaca.ai/pt/precos — Sabiazinho 4, R$1,00/R$4,00 por "
            "milhão de tokens, câmbio aproximado US$1≈R$5,14 (consultado em "
            "2026-08-06). É o modelo padrão do provedor 'maritaca' desde a "
            "atualização do Grupo 3 (model_provider.PROVEDORES)."
        ),
    ),
    (
        "maritaca", "sabia-4",
        _preco_brl(5.00, 20.00),
        (
            "https://docs.maritaca.ai/pt/precos — Sabiá 4, R$5,00/R$20,00 por "
            "milhão de tokens, câmbio aproximado US$1≈R$5,14 (consultado em "
            "2026-08-06)."
        ),
    ),
    (
        "maritaca", "sabia-3",
        _preco_brl(5.00, 10.00),
        (
            "Sabiá-3 não está mais na tabela oficial da Maritaca (só Sabiá 4 e "
            "variantes em 2026-08-06); valor aproximado pelo preço de lançamento "
            "do Sabiá-3 (R$5,00/R$10,00 por milhão de tokens), câmbio aproximado "
            "US$1≈R$5,14. O modelo foi DESCONTINUADO e deixou de ser o padrão do "
            "provedor 'maritaca' (o Grupo 3 trocou para sabiazinho-4); a entrada "
            "fica na tabela para precificar execuções ANTIGAS, gravadas antes da "
            "troca, que ainda aparecem em resultados/ e nos checkpoints. "
            "Configure o preço real por ambiente se souber o valor vigente."
        ),
    ),
)


def preco_oficial(provider: str, modelo: str | None) -> PrecosModelo | None:
    """Preço oficial documentado para ``provider``/``modelo``, ou ``None``.

    Casa por prefixo (``modelo`` normalizado começa com o prefixo da tabela);
    entre prefixos compatíveis, vence o mais específico (o mais longo).
    """
    if not modelo:
        return None
    provider_norm = str(provider).strip().lower()
    modelo_norm = modelo.strip().lower()

    melhor: tuple[int, PrecosModelo] | None = None
    for prov, prefixo, precos, _fonte in TABELA_PRECOS_OFICIAIS:
        if prov != provider_norm:
            continue
        if modelo_norm.startswith(prefixo) and (melhor is None or len(prefixo) > melhor[0]):
            melhor = (len(prefixo), precos)
    return melhor[1] if melhor else None


def fonte_oficial(provider: str, modelo: str | None) -> str | None:
    """Fonte (URL + nota) do preço devolvido por ``preco_oficial`` para o mesmo par."""
    if not modelo:
        return None
    provider_norm = str(provider).strip().lower()
    modelo_norm = modelo.strip().lower()

    melhor: tuple[int, str] | None = None
    for prov, prefixo, _precos, fonte in TABELA_PRECOS_OFICIAIS:
        if prov != provider_norm:
            continue
        if modelo_norm.startswith(prefixo) and (melhor is None or len(prefixo) > melhor[0]):
            melhor = (len(prefixo), fonte)
    return melhor[1] if melhor else None


def _preco_via_litellm(provider: str, modelo: str | None) -> PrecosModelo | None:
    """Preço a partir de ``litellm.model_cost``, se o pacote estiver instalado.

    Import guardado: sem ``litellm`` (setup só-Gemini/mock, que não precisa
    dele — ver ``model_provider.py``), devolve ``None`` e ``resolver_precos``
    cai para a tabela oficial estática deste módulo.

    Tenta o nome do modelo puro (``"gpt-4o-mini"``, ``"gemini-2.5-flash"`` —
    é assim que ``litellm.model_cost`` guarda a maioria dos modelos conhecidos)
    e, se não achar, o nome prefixado pelo provedor (``"gemini/gemini-2.5-
    flash"``, mesmo formato que ``ModelConfig.litellm_model`` monta para
    Maritaca/OpenAI em ``model_provider.py``). A Maritaca não tem NENHUMA
    entrada na tabela do litellm (nem por ``"openai/"``, o prefixo de
    roteamento que ela reaproveita) — para esse provedor, este passo sempre
    devolve ``None`` e quem precifica é a tabela oficial estática.
    """
    if not modelo:
        return None
    try:
        import litellm
    except ImportError:
        return None

    tabela = getattr(litellm, "model_cost", None)
    if not tabela:
        return None

    provider_norm = str(provider).strip().lower()
    modelo_norm = modelo.strip()
    candidatos = [modelo_norm, f"{provider_norm}/{modelo_norm}"]
    if provider_norm == "maritaca":
        candidatos.append(f"openai/{modelo_norm}")

    for chave in candidatos:
        entrada = tabela.get(chave)
        if not entrada:
            continue
        preco_entrada = entrada.get("input_cost_per_token")
        preco_saida = entrada.get("output_cost_per_token")
        if preco_entrada is None or preco_saida is None:
            continue
        return PrecosModelo(
            usd_por_milhao_tokens_entrada=preco_entrada * 1_000_000,
            usd_por_milhao_tokens_saida=preco_saida * 1_000_000,
        )
    return None


def _preco_via_litellm_modelo(modelo: str | None) -> PrecosModelo | None:
    """Preço em ``litellm.model_cost`` pelo NOME PURO do modelo, sem provedor.

    Usado por ``resolver_precos_por_modelo`` para precificar POR CHAMADA: o
    evento ``tipo="chamada_llm"`` só carrega o modelo que respondeu
    (``model_version`` do ADK), não o provedor configurado para o papel — os
    dois podem divergir (ver ``preco_por_modelo``). ``litellm.model_cost`` já
    guarda a maioria dos modelos pelo nome puro (mesma constatação de
    ``_preco_via_litellm``); o prefixo por provedor só existe ali para
    desambiguar quando necessário, o que não é o caso das famílias suportadas
    aqui (gemini-/gpt-/sabia- não colidem entre provedores).
    """
    if not modelo:
        return None
    try:
        import litellm
    except ImportError:
        return None

    tabela = getattr(litellm, "model_cost", None)
    if not tabela:
        return None

    entrada = tabela.get(modelo.strip())
    if not entrada:
        return None
    preco_entrada = entrada.get("input_cost_per_token")
    preco_saida = entrada.get("output_cost_per_token")
    if preco_entrada is None or preco_saida is None:
        return None
    return PrecosModelo(
        usd_por_milhao_tokens_entrada=preco_entrada * 1_000_000,
        usd_por_milhao_tokens_saida=preco_saida * 1_000_000,
    )


def preco_por_modelo(modelo: str | None) -> PrecosModelo | None:
    """Preço oficial pelo MODELO sozinho, sem exigir o provedor.

    Casa por prefixo em TODAS as entradas de ``TABELA_PRECOS_OFICIAIS``,
    indiferente ao provedor — necessário para precificar POR CHAMADA, já que
    o evento ``tipo="chamada_llm"`` só carrega o modelo que respondeu, não o
    provedor configurado para o papel. Os prefixos das três famílias
    suportadas (``gemini-``, ``gpt-``, ``sabia``) não se sobrepõem entre
    provedores, então casar só pelo modelo é seguro; entre prefixos
    compatíveis, vence o mais específico (o mais longo), igual a
    ``preco_oficial``.
    """
    if not modelo:
        return None
    modelo_norm = modelo.strip().lower()

    melhor: tuple[int, PrecosModelo] | None = None
    for _prov, prefixo, precos, _fonte in TABELA_PRECOS_OFICIAIS:
        if modelo_norm.startswith(prefixo) and (melhor is None or len(prefixo) > melhor[0]):
            melhor = (len(prefixo), precos)
    return melhor[1] if melhor else None


def resolver_precos_por_modelo(modelo: str | None) -> PrecosModelo | None:
    """Preço para UMA chamada específica, a partir do modelo que de fato respondeu.

    Diferente de ``resolver_precos`` (que resolve o preço da execução INTEIRA
    a partir do provedor/modelo CONFIGURADO para um papel, antes de qualquer
    chamada acontecer), esta função precifica pelo campo ``modelo`` do evento
    ``tipo="chamada_llm"`` — o ``model_version`` que o ADK devolveu na
    resposta real. É o que evita aplicar um preço único e possivelmente
    errado quando mais de um modelo responde na mesma execução: o editor
    configurado com um modelo diferente do resto do pipeline, ou o principal
    falhando e o fallback (``llm_fallback.py``) assumindo a resposta —
    nesses casos o modelo CONFIGURADO para o papel e o modelo que de fato
    respondeu divergem, e só o segundo está correto para o custo.

    Precedência: ambiente (``GRUPO2_PRECO_USD_MILHAO_TOKENS_*``) sempre
    vence — é um override manual do operador e se aplica a TODAS as chamadas
    propositalmente, igual a ``resolver_precos``; senão ``litellm.model_cost``
    pelo nome puro do modelo; senão a tabela oficial estática deste módulo
    (``preco_por_modelo``); senão ``None`` — modelo não reconhecido em
    nenhuma fonte, custo dessa chamada específica fica indisponível, nunca 0.
    """
    de_ambiente = precos_de_ambiente()
    if de_ambiente is not None:
        return de_ambiente
    via_litellm = _preco_via_litellm_modelo(modelo)
    if via_litellm is not None:
        return via_litellm
    return preco_por_modelo(modelo)


def resolver_precos(config: dict[str, Any] | None = None, papel: str | None = None) -> PrecosModelo | None:
    """Preço a usar em ``gerar_resumo(precos=...)`` para a execução atual.

    Ambiente configurado (as DUAS variáveis) sempre vence. Na ausência dele,
    resolve o provedor/modelo já decidido para a execução
    (``model_provider.resolver_config``) e procura em ``litellm.model_cost``
    (se instalado) antes de cair na tabela oficial estática deste módulo —
    ver a precedência completa no docstring do módulo. Devolve ``None`` sem
    preço configurado E sem entrada reconhecida em nenhuma fonte — custo
    permanece indisponível, nunca 0.

    Import de ``model_provider`` é local (não no topo do módulo): ``metrics/``
    é um pacote sem dependências externas por design (roda offline, sem ADK
    instalado) e ``model_provider`` vive fora do pacote — se por algum motivo
    não puder ser importado, esta função degrada para o comportamento de
    ``precos_de_ambiente()`` sozinho, em vez de quebrar o resumo.
    """
    de_ambiente = precos_de_ambiente()
    if de_ambiente is not None:
        return de_ambiente

    try:
        from model_provider import resolver_config
    except ImportError:
        return None

    escolha = resolver_config(config, papel)
    via_litellm = _preco_via_litellm(escolha.provider.value, escolha.model)
    if via_litellm is not None:
        return via_litellm
    return preco_oficial(escolha.provider.value, escolha.model)
