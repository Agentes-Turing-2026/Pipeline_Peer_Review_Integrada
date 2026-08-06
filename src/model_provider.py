"""Escolha CENTRALIZADA de provedor e modelo de LLM (Gemini · Maritaca · OpenAI).

Este módulo é o ÚNICO ponto do repositório que decide *com qual serviço* o
pipeline fala. Os agentes continuam sendo os mesmos ``LlmAgent`` do Google ADK,
com os mesmos prompts, ``output_key``, ``output_schema`` e validações — o que
muda é apenas o objeto passado em ``model=``.

Princípios de projeto:

1. **Um agente por papel, não um agente por provedor.** Trocar de serviço não
   duplica revisor, leitura cruzada nem editor: ``build_model()`` devolve o
   modelo certo e o resto do código não percebe a diferença.
2. **Padrão ADK preservado.** No Gemini devolvemos a *string* do modelo, que é a
   rota nativa do ADK (comportamento idêntico ao anterior). Nos demais
   provedores devolvemos um ``LiteLlm`` — o adaptador oficial do próprio ADK
   para modelos não-Google (``google.adk.models.lite_llm``). Em nenhum dos casos
   saímos do framework.
3. **Tabela como fonte única de verdade.** Chave de API, modelo padrão, rota e
   endpoint de cada provedor vivem em ``PROVEDORES``. Adicionar um quarto
   serviço é acrescentar uma linha lá, não espalhar ``if`` pelo pipeline.
4. **Falhar explicitamente.** Provedor desconhecido ou chave ausente levantam
   erro dizendo exatamente qual variável configurar — mesma política de
   ``resolve_mode()`` em ``pipeline.py``.

Configuração por ambiente (.env), em ordem de precedência:

    LLM_PROVIDER          — gemini | maritaca | openai   (default: gemini)
    LLM_MODEL             — id do modelo (default: o do provedor escolhido)
    LLM_PROVIDER_<PAPEL>  — sobrescreve o provedor de um papel específico
    LLM_MODEL_<PAPEL>     — sobrescreve o modelo de um papel específico
    LLM_JSON_SCHEMA       — on | off: força/desliga o structured output nativo

    LLM_FALLBACK_PROVIDER / LLM_FALLBACK_MODEL — opção RESERVA usada quando o
    modelo principal falha por motivo temporário (timeout, limite de
    requisições, erro de rede ou indisponibilidade). Também admitem a variante
    por papel (LLM_FALLBACK_PROVIDER_<PAPEL> / LLM_FALLBACK_MODEL_<PAPEL>).
    Sem elas, o comportamento é o de sempre: a falha interrompe o pipeline.
    A troca em si vive em ``llm_fallback.py``.

    GOOGLE_API_KEY / MARITACA_API_KEY / OPENAI_API_KEY — chave do provedor.

Retrocompatibilidade: um ``.env`` antigo, com apenas ``GOOGLE_API_KEY`` e
``GEMINI_MODEL`` e sem nenhuma variável ``LLM_*``, continua funcionando
exatamente como antes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Provedores suportados
# ---------------------------------------------------------------------------

class Provider(str, Enum):
    """Serviços de LLM que o pipeline sabe usar."""

    GEMINI = "gemini"       # Google, via rota nativa do ADK
    MARITACA = "maritaca"   # Maritaca AI (Sabiá), via LiteLlm
    OPENAI = "openai"       # OpenAI (GPT), via LiteLlm


#: Sinônimos aceitos na configuração, para não punir quem escreve o nome comum
#: do serviço em vez do identificador exato (mesma tolerância de ``resolve_mode``).
_SINONIMOS: dict[str, Provider] = {
    "gemini": Provider.GEMINI,
    "google": Provider.GEMINI,
    "googleai": Provider.GEMINI,
    "maritaca": Provider.MARITACA,
    "maritacaai": Provider.MARITACA,
    "maritalk": Provider.MARITACA,
    "sabia": Provider.MARITACA,
    "openai": Provider.OPENAI,
    "gpt": Provider.OPENAI,
    "chatgpt": Provider.OPENAI,
}


@dataclass(frozen=True)
class ProviderSpec:
    """Tudo o que distingue um provedor dos outros.

    Attributes
    ----------
    env_api_key:
        Variável de ambiente que guarda a chave do serviço.
    modelo_padrao:
        Modelo usado quando a configuração não nomeia nenhum.
    prefixo_litellm:
        Prefixo de roteamento do LiteLLM (ex.: ``"openai/"``). ``None`` significa
        rota NATIVA do ADK — o modelo é passado como string simples.
    api_base:
        Endpoint alternativo, quando o serviço fala o protocolo de outro
        (é o caso da Maritaca, compatível com a API da OpenAI).
    suporta_json_schema:
        Se o serviço aceita ``response_format: json_schema`` (structured output
        nativo). Quando ``False``, o schema continua sendo exigido pelo prompt e
        validado pelo ADK — ver ``_classe_litellm``.
    """

    env_api_key: str
    modelo_padrao: str
    prefixo_litellm: str | None
    api_base: str | None
    suporta_json_schema: bool


#: Registro dos provedores. Para suportar um novo serviço, acrescente uma linha.
PROVEDORES: dict[Provider, ProviderSpec] = {
    Provider.GEMINI: ProviderSpec(
        env_api_key="GOOGLE_API_KEY",
        modelo_padrao="gemini-2.5-flash",
        prefixo_litellm=None,          # rota nativa do ADK
        api_base=None,
        suporta_json_schema=True,
    ),
    Provider.OPENAI: ProviderSpec(
        env_api_key="OPENAI_API_KEY",
        modelo_padrao="gpt-4o-mini",
        prefixo_litellm="openai/",
        api_base=None,
        suporta_json_schema=True,
    ),
    Provider.MARITACA: ProviderSpec(
        env_api_key="MARITACA_API_KEY",
        modelo_padrao="sabia-3",
        # A Maritaca é compatível com a API da OpenAI: mesmo protocolo, outra
        # base_url. Por isso reaproveita o roteamento "openai/" do LiteLLM.
        prefixo_litellm="openai/",
        api_base="https://chat.maritaca.ai/api",
        # Conservador: o suporte a json_schema não é documentado publicamente.
        # Se confirmarmos, basta virar esta flag (ou usar LLM_JSON_SCHEMA=on).
        suporta_json_schema=False,
    ),
}


def normalizar_provider(valor: str | Provider) -> Provider:
    """Converte o texto da configuração em ``Provider``, ou falha explicando."""
    if isinstance(valor, Provider):
        return valor
    chave = str(valor).strip().lower().replace(" ", "").replace("-", "").replace("_", "")
    if chave not in _SINONIMOS:
        aceitos = ", ".join(p.value for p in Provider)
        raise ValueError(
            f"Provedor de LLM desconhecido: {valor!r}. Use um de: {aceitos} "
            f"(via LLM_PROVIDER ou config['provider'])."
        )
    return _SINONIMOS[chave]


# ---------------------------------------------------------------------------
# Resolução da configuração (config explícita > papel > global > legado > default)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelConfig:
    """A decisão já tomada: qual provedor, qual modelo, com ou sem json_schema."""

    provider: Provider
    model: str
    structured_output: bool

    @property
    def spec(self) -> ProviderSpec:
        """Especificação do provedor escolhido."""
        return PROVEDORES[self.provider]

    @property
    def litellm_model(self) -> str:
        """Id do modelo no formato de roteamento do LiteLLM (ex.: ``openai/sabia-3``)."""
        prefixo = self.spec.prefixo_litellm or ""
        return f"{prefixo}{self.model}"

    @property
    def rotulo(self) -> str:
        """Rótulo legível para relatórios e traces (ex.: ``maritaca:sabia-3``)."""
        return f"{self.provider.value}:{self.model}"


def _ler(chave: str, config: dict[str, Any] | None, papel: str | None) -> str | None:
    """Lê um ajuste (``provider`` ou ``model``) respeitando a precedência oficial.

    A ordem é sempre: config explícita do papel, config explícita global,
    variável de ambiente do papel e, por fim, variável de ambiente global.
    """
    cfg = config or {}
    candidatos: list[Any] = []
    if papel:
        candidatos.append(cfg.get(f"{chave}_{papel}"))
    candidatos.append(cfg.get(chave))
    if papel:
        candidatos.append(os.getenv(f"LLM_{chave.upper()}_{papel.upper()}"))
    candidatos.append(os.getenv(f"LLM_{chave.upper()}"))

    for valor in candidatos:
        if valor:
            return str(valor)
    return None


def _resolver_structured_output(spec: ProviderSpec) -> bool:
    """Decide se enviamos ``response_schema`` ao provedor.

    O default vem da tabela; ``LLM_JSON_SCHEMA=on|off`` permite forçar dos dois
    lados sem editar código — útil para testar o suporte de um serviço novo.
    """
    bruto = os.getenv("LLM_JSON_SCHEMA")
    if bruto is None:
        return spec.suporta_json_schema
    chave = bruto.strip().lower()
    if chave in ("on", "true", "1", "sim", "yes"):
        return True
    if chave in ("off", "false", "0", "nao", "não", "no"):
        return False
    raise ValueError(
        f"LLM_JSON_SCHEMA inválido: {bruto!r}. Use 'on' ou 'off'."
    )


def resolver_config(
    config: dict[str, Any] | None = None, papel: str | None = None
) -> ModelConfig:
    """Resolve provedor e modelo para um papel do pipeline.

    Parameters
    ----------
    config:
        Configuração explícita do pipeline (``config['provider']``,
        ``config['model']`` e, opcionalmente, ``config['model_<papel>']``).
    papel:
        Nome do papel, quando ele admite sobrescrita própria (ex.: ``"editor"``
        habilita ``LLM_MODEL_EDITOR`` / ``LLM_PROVIDER_EDITOR``).

    Notes
    -----
    Se nada for informado, cai no ``GEMINI_MODEL`` legado e, por fim, no modelo
    padrão do Gemini — de modo que um ``.env`` antigo continue válido.
    """
    provider_bruto = _ler("provider", config, papel)
    model_bruto = _ler("model", config, papel)

    if provider_bruto is not None:
        provider = normalizar_provider(provider_bruto)
    elif model_bruto is None and os.getenv("GEMINI_MODEL"):
        # Caminho legado: .env sem nenhuma variável LLM_*, só GEMINI_MODEL.
        provider = Provider.GEMINI
        model_bruto = os.getenv("GEMINI_MODEL")
    else:
        provider = Provider.GEMINI

    spec = PROVEDORES[provider]
    return ModelConfig(
        provider=provider,
        model=model_bruto or spec.modelo_padrao,
        structured_output=_resolver_structured_output(spec),
    )


def resolver_config_fallback(
    config: dict[str, Any] | None = None, papel: str | None = None
) -> ModelConfig | None:
    """Resolve a opção RESERVA de modelo, ou ``None`` se ela não foi configurada.

    A reserva só existe quando a configuração nomeia ao menos um de
    ``fallback_provider`` / ``fallback_model`` (via ``config`` ou pelas
    variáveis ``LLM_FALLBACK_*``). Regras de preenchimento:

    - só o modelo → assume o MESMO provedor do principal (ex.: outro Gemini);
    - só o provedor → usa o modelo padrão daquele provedor.

    A precedência (config explícita > papel > global) é a mesma de
    ``resolver_config``, reutilizando ``_ler``.
    """
    provider_bruto = _ler("fallback_provider", config, papel)
    model_bruto = _ler("fallback_model", config, papel)
    if provider_bruto is None and model_bruto is None:
        return None

    if provider_bruto is not None:
        provider = normalizar_provider(provider_bruto)
    else:
        provider = resolver_config(config, papel).provider

    spec = PROVEDORES[provider]
    return ModelConfig(
        provider=provider,
        model=model_bruto or spec.modelo_padrao,
        structured_output=_resolver_structured_output(spec),
    )


def descricao_modelo(config: dict[str, Any] | None = None, papel: str | None = None) -> str:
    """Rótulo ``provedor:modelo`` da execução, para relatórios e traces."""
    return resolver_config(config, papel).rotulo


def exigir_api_key(
    config: dict[str, Any] | None = None, papel: str | None = None
) -> ModelConfig:
    """Falha de forma explícita se a chave do provedor escolhido não existir.

    Substitui a antiga checagem fixa de ``GOOGLE_API_KEY``: a mensagem agora cita
    a variável do provedor que a configuração de fato selecionou.
    """
    cfg = resolver_config(config, papel)
    if not os.getenv(cfg.spec.env_api_key):
        raise RuntimeError(
            f"{cfg.spec.env_api_key} não configurada, mas o provedor selecionado é "
            f"'{cfg.provider.value}'. Copie '.env.example' para '.env' na raiz do "
            f"projeto e preencha a chave, ou rode em modo mock "
            f"(`python main.py mock`), que funciona offline e sem chave."
        )

    # A reserva também precisa de chave: descobrir isso ANTES de rodar é melhor
    # do que só na hora em que o principal falhar e a troca for necessária.
    cfg_reserva = resolver_config_fallback(config, papel)
    if cfg_reserva is not None and not os.getenv(cfg_reserva.spec.env_api_key):
        raise RuntimeError(
            f"{cfg_reserva.spec.env_api_key} não configurada, mas o provedor RESERVA "
            f"selecionado é '{cfg_reserva.provider.value}' (via LLM_FALLBACK_*). "
            f"Preencha a chave no '.env' ou remova a configuração de fallback."
        )
    return cfg


# ---------------------------------------------------------------------------
# Construção do modelo para o ADK
# ---------------------------------------------------------------------------

def _importar_litellm_adk():
    """Importa ``LiteLlm`` do ADK, traduzindo a falta da dependência em instrução."""
    try:
        from google.adk.models.lite_llm import LiteLlm
    except ImportError as exc:  # noqa: BLE001
        raise RuntimeError(
            "Os provedores Maritaca e OpenAI usam o adaptador LiteLlm do ADK, que "
            "depende do pacote 'litellm'. Instale com 'pip install litellm' "
            "(o modo Gemini e o modo mock não precisam dele)."
        ) from exc
    return LiteLlm


#: Cache das duas variantes de classe (criadas uma única vez, sob demanda).
_CLASSES_LITELLM: dict[bool, Any] = {}


def _classe_litellm(structured_output: bool):
    """Devolve a classe ``LiteLlm`` adequada ao suporte do provedor.

    Ambas as variantes **limpam a resposta** antes de o ADK validá-la,
    reaproveitando o extrator de JSON que o Grupo 1 já mantém em
    ``validacao_retry``: mesmo um serviço que aceita ``json_schema`` pode
    devolver o JSON embrulhado em ```` ```json ````, e sem essa limpeza o
    ``validate_schema`` do ADK falharia por um detalhe de formatação. Para uma
    resposta já pura, a limpeza é inócua.

    A variante para serviços SEM structured output nativo faz uma coisa a mais:
    remove ``response_schema``/``response_mime_type`` do pedido, senão o provedor
    recusaria um parâmetro que não entende.

    O contrato NÃO é afrouxado em nenhum dos casos: o prompt continua exigindo
    JSON puro, o ADK continua validando contra o ``output_schema`` e
    ``validar_com_tentativas`` continua fazendo retry com corretor.
    """
    if structured_output in _CLASSES_LITELLM:
        return _CLASSES_LITELLM[structured_output]

    LiteLlm = _importar_litellm_adk()

    class LiteLlmDoPipeline(LiteLlm):  # type: ignore[misc, valid-type]
        """``LiteLlm`` com a resposta normalizada para o ``output_schema``."""

        #: Quando ``True``, o pedido não leva ``response_schema`` (provedor sem suporte).
        _REMOVER_RESPONSE_SCHEMA: bool = not structured_output

        async def generate_content_async(self, llm_request, stream: bool = False):
            config = getattr(llm_request, "config", None)
            if (
                self._REMOVER_RESPONSE_SCHEMA
                and config is not None
                and getattr(config, "response_schema", None) is not None
            ):
                config.response_schema = None
                config.response_mime_type = None

            async for resposta in super().generate_content_async(llm_request, stream):
                yield _limpar_json_da_resposta(resposta)

    LiteLlmDoPipeline.__name__ = (
        "LiteLlmComJsonSchema" if structured_output else "LiteLlmSemJsonSchema"
    )
    _CLASSES_LITELLM[structured_output] = LiteLlmDoPipeline
    return LiteLlmDoPipeline


def _limpar_json_da_resposta(resposta):
    """Reescreve o texto da resposta como JSON puro, quando isso for possível.

    Só age em respostas finais com texto; qualquer coisa que não seja JSON
    recuperável é devolvida intacta, para que o erro apareça no lugar certo (a
    validação) em vez de ser mascarado aqui.
    """
    if getattr(resposta, "partial", False):
        return resposta
    content = getattr(resposta, "content", None)
    partes = getattr(content, "parts", None) if content is not None else None
    if not partes:
        return resposta

    # Reaproveita o extrator do Grupo 1 (import tardio: validacao_retry importa
    # este módulo, então importar no topo criaria um ciclo).
    import json

    from validacao_retry import _extrair_json_da_resposta

    for parte in partes:
        texto = getattr(parte, "text", None)
        if not texto or not texto.strip():
            continue
        try:
            dados = _extrair_json_da_resposta(texto)
        except ValueError:
            continue  # não era JSON: deixa passar e falhar na validação
        parte.text = json.dumps(dados, ensure_ascii=False)
    return resposta


def _instanciar_modelo(cfg: ModelConfig) -> Any:
    """Instancia o ``BaseLlm`` do ADK correspondente à configuração.

    - **Gemini:** a classe ``Gemini`` nativa do ADK — o mesmo objeto que o
      próprio ADK criaria ao receber a string do modelo.
    - **Maritaca / OpenAI:** o ``LiteLlm`` do pipeline, com endpoint e chave.
    """
    if cfg.spec.prefixo_litellm is None:
        from google.adk.models.google_llm import Gemini

        return Gemini(model=cfg.model)

    Classe = _classe_litellm(cfg.structured_output)
    kwargs: dict[str, Any] = {"model": cfg.litellm_model}
    api_key = os.getenv(cfg.spec.env_api_key)
    if api_key:
        kwargs["api_key"] = api_key
    if cfg.spec.api_base:
        kwargs["api_base"] = cfg.spec.api_base
    return Classe(**kwargs)


def build_model(config: dict[str, Any] | None = None, papel: str | None = None) -> Any:
    """Devolve o que deve ir em ``LlmAgent(model=...)`` para o papel indicado.

    - **Gemini:** a string do modelo (rota nativa do ADK, sem intermediários).
    - **Maritaca / OpenAI:** um ``LiteLlm`` configurado com endpoint e chave.
    - **Com reserva configurada (LLM_FALLBACK_*):** um ``ModeloComFallback``
      que embrulha principal + reserva e troca de modelo em falha temporária
      (ver ``llm_fallback.py``).

    Em todos os casos o agente continua sendo um ``LlmAgent`` comum, com o
    mesmo prompt e o mesmo ``output_schema``.
    """
    cfg = resolver_config(config, papel)
    cfg_reserva = resolver_config_fallback(config, papel)

    if cfg_reserva is None:
        # Sem fallback, o comportamento é EXATAMENTE o anterior.
        if cfg.spec.prefixo_litellm is None:
            return cfg.model
        return _instanciar_modelo(cfg)

    from llm_fallback import criar_modelo_com_fallback

    return criar_modelo_com_fallback(
        modelo_primario=_instanciar_modelo(cfg),
        modelo_reserva=_instanciar_modelo(cfg_reserva),
        rotulo_primario=cfg.rotulo,
        rotulo_reserva=cfg_reserva.rotulo,
        papel=papel,
    )


# ---------------------------------------------------------------------------
# Chamada de texto simples (fora do ADK) — usada pelo corretor de retry
# ---------------------------------------------------------------------------

def completar_texto(
    prompt: str, config: dict[str, Any] | None = None, papel: str | None = None
) -> str:
    """Faz uma chamada única de texto ao provedor escolhido e devolve a resposta.

    Existe porque nem toda chamada do pipeline passa por um agente: o corretor de
    ``validacao_retry.corrigir_saida_api`` precisa de uma completação avulsa. Sem
    este ponto compartilhado, escolher OpenAI ou Maritaca ainda cairia no Gemini
    na hora de corrigir uma saída inválida.

    Com a reserva configurada (``LLM_FALLBACK_*``), uma falha TEMPORÁRIA do
    principal (timeout, limite de requisições, rede, indisponibilidade) troca
    para ela — mesma política dos agentes, ver ``llm_fallback.py``.
    """
    cfg = exigir_api_key(config, papel)
    cfg_reserva = resolver_config_fallback(config, papel)

    try:
        return _completar_texto_unico(cfg, prompt)
    except Exception as exc:  # noqa: BLE001 — classificado logo abaixo
        if cfg_reserva is None:
            raise
        from llm_fallback import (
            classificar_falha_temporaria,
            emitir_fallback_acionado,
            emitir_fallback_esgotado,
            emitir_fallback_respondeu,
        )

        motivo = classificar_falha_temporaria(exc)
        if motivo is None:
            raise
        emitir_fallback_acionado(
            rotulo_primario=cfg.rotulo, rotulo_reserva=cfg_reserva.rotulo,
            motivo=motivo, erro=exc, papel=papel,
        )
        try:
            texto = _completar_texto_unico(cfg_reserva, prompt)
        except Exception as exc_reserva:  # noqa: BLE001 — registra e re-levanta
            emitir_fallback_esgotado(
                rotulo_primario=cfg.rotulo, rotulo_reserva=cfg_reserva.rotulo,
                erro=exc_reserva, papel=papel,
            )
            raise
        emitir_fallback_respondeu(
            rotulo_primario=cfg.rotulo, rotulo_reserva=cfg_reserva.rotulo, papel=papel,
        )
        return texto


def _completar_texto_unico(cfg: ModelConfig, prompt: str) -> str:
    """Uma única completação de texto no provedor de ``cfg`` (sem fallback)."""
    api_key = os.getenv(cfg.spec.env_api_key)

    if cfg.provider is Provider.GEMINI:
        try:
            import google.genai as genai  # type: ignore[import]
        except ImportError as exc:  # noqa: BLE001
            raise RuntimeError(
                "google-genai não está instalado. No modo API com Gemini, instale-o "
                "com 'pip install google-genai'."
            ) from exc
        client = genai.Client(api_key=api_key)
        resposta = client.models.generate_content(model=cfg.model, contents=prompt)
        return (resposta.text or "").strip()

    try:
        import litellm  # type: ignore[import]
    except ImportError as exc:  # noqa: BLE001
        raise RuntimeError(
            f"O provedor '{cfg.provider.value}' depende do pacote 'litellm'. "
            f"Instale com 'pip install litellm'."
        ) from exc

    kwargs: dict[str, Any] = {
        "model": cfg.litellm_model,
        "messages": [{"role": "user", "content": prompt}],
        "api_key": api_key,
    }
    if cfg.spec.api_base:
        kwargs["api_base"] = cfg.spec.api_base
    resposta = litellm.completion(**kwargs)
    return (resposta.choices[0].message.content or "").strip()
