"""Validação e resiliência da ENTRADA por PDF (Grupo 1).

Esta é a camada que responde, para um documento real que entra no pipeline:
**pode seguir, precisa de nova tentativa (retry) ou deve ser bloqueado?** Ela
estende a mesma filosofia de ``validacao_retry.py`` (validar, decidir, deixar
rastro estruturado, nunca aceitar erro em silêncio) para o material que chega
ANTES dos agentes: o arquivo PDF e o texto extraído dele.

Divisão de responsabilidades (combinada com os outros grupos):

- **Grupo 3** implementa o extrator (``src/extraction/``) e a orquestração da
  fase 0 em ``pipeline.py``. Este módulo NÃO extrai PDF nem reimplementa o
  extrator — só o valida antes e depois.
- **Grupo 2** cuida de métricas/tokens. Este módulo não calcula métricas.
- **Grupo 1** (aqui) decide o destino da entrada e o registra como evento
  estruturado, reaproveitando ``eventos_validacao.py``: o MESMO ``run_id`` da
  execução, o MESMO arquivo ``validacao_events.jsonl`` e o MESMO espelhamento
  no trace do Grupo 3 (``observability.emit_event``). Não há segundo sistema
  de logs nem outro identificador.

Dois níveis de verificação, ambos DETERMINÍSTICOS (sem LLM):

1. ``classificar_arquivo_pdf(path)`` — problemas básicos ANTES da extração:
   arquivo inexistente, não-arquivo, formato incorreto, vazio, sem cabeçalho
   ``%PDF`` (corrompido) e PDF protegido/criptografado. Nenhum desses se
   resolve repetindo a extração ⇒ decisão de bloqueio, sem retry.
2. ``classificar_documento_extraido(doc)`` — qualidade do resultado da
   extração: texto vazio, muito curto, alta proporção de caracteres ilegíveis,
   baixa densidade por página e avisos do extrator.

Política de decisão (``DecisaoEntrada``):

- ``OK``       — segue normalmente.
- ``ALERTA``   — segue, mas com ressalvas que pedem revisão humana (texto curto,
                 caracteres ilegíveis, avisos do extrator). Não é silencioso:
                 vira evento categoria ``alerta_entrada``.
- ``RETRY``    — uma nova tentativa PODE resolver (texto vazio → reextrair com
                 OCR). Só é efetivada quando uma estratégia de reextração é
                 fornecida; caso contrário vira bloqueio.
- ``BLOQUEAR`` — não há como seguir (arquivo ruim, texto irrecuperável). Levanta
                 ``EntradaInvalidaError`` — o pipeline nunca aceita a entrada
                 em silêncio.

Interface de integração (o pipeline chama uma destas):

- ``validar_arquivo_pdf(path, ...)``          — pré-extração, no ``run_demo``.
- ``validar_documento_extraido(doc, ...)``    — pós-extração, no ``extract_pdf_input``.
- ``validar_entrada_com_retry(path, extrair, reextrair=...)`` — orquestra o
  ciclo completo (arquivo → extração → documento → retry), pronto para o
  Grupo 3 ligar uma estratégia de OCR quando quiser.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from eventos_validacao import (
    CATEGORIA_ALERTA_ENTRADA,
    CATEGORIA_BLOQUEADO,
    CATEGORIA_CORRIGIDO,
    CATEGORIA_FALHOU_RECUPERAVEL,
    CATEGORIA_PASSOU_APOS_CORRECAO,
    CATEGORIA_PASSOU_DE_PRIMEIRA,
    EventoValidacao,
    diff_correcao,
    emitir_evento,
    gerar_run_id,
)

# Nome da fase 0 no trace/relatório. Mantido idêntico a
# ``pipeline.EXTRACTION_PHASE`` (Grupo 3) para que os eventos desta camada caiam
# na MESMA fase da extração — sem importar pipeline.py (evita import circular).
FASE_ENTRADA = "fase_0_extracao_pdf"

# Autor/os "schemas" registrados nos eventos (paralelo a validar_review etc.).
AGENTE_ENTRADA = "entrada_pdf"
SCHEMA_ARQUIVO = "validar_arquivo_pdf"
SCHEMA_DOCUMENTO = "validar_documento_extraido"
SCHEMA_EXTRACAO = "extrair_pdf"

# Limiares determinísticos (documentados no README §4).
MIN_CARACTERES_UTIL = 200        # abaixo disto o texto é "muito curto" → alerta
MIN_CHARS_POR_PAGINA = 200       # densidade mínima antes de alertar por página
MAX_RAZAO_ILEGIVEL = 0.30        # acima disto o texto é "ilegível demais" → alerta

# Cabeçalho obrigatório de todo PDF (ISO 32000): os primeiros bytes são "%PDF-".
_ASSINATURA_PDF = b"%PDF-"


class DecisaoEntrada(str, Enum):
    """Destino de uma entrada após a validação."""

    OK = "ok"
    ALERTA = "alerta"
    RETRY = "retry"
    BLOQUEAR = "bloquear"


@dataclass
class ResultadoEntrada:
    """Resultado estruturado de uma verificação de entrada (arquivo ou documento).

    Attributes
    ----------
    decisao:
        Um de :class:`DecisaoEntrada`.
    etapa:
        ``"arquivo"`` (pré-extração) ou ``"extracao"`` (pós-extração).
    motivos:
        Explicações legíveis de por que a decisão foi tomada (uma ou mais).
    avisos:
        Ressalvas não bloqueantes (ex.: avisos repassados pelo extrator).
    requer_revisao_humana:
        ``True`` quando a entrada, mesmo seguindo ou sendo bloqueada, é candidata
        a triagem manual.
    permite_retry:
        ``True`` quando uma nova tentativa (ex.: reextração com OCR) tem chance
        real de resolver — NÃO usar retry quando isto é ``False`` (ex.: arquivo
        corrompido).
    """

    decisao: DecisaoEntrada
    etapa: str
    motivos: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    requer_revisao_humana: bool = False
    permite_retry: bool = False

    @property
    def bloqueado(self) -> bool:
        return self.decisao is DecisaoEntrada.BLOQUEAR

    @property
    def aprovado(self) -> bool:
        """Segue no pipeline (com ou sem alerta)."""
        return self.decisao in (DecisaoEntrada.OK, DecisaoEntrada.ALERTA)

    @property
    def mensagem(self) -> str:
        return "; ".join(self.motivos) if self.motivos else self.decisao.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "decisao": self.decisao.value,
            "etapa": self.etapa,
            "motivos": list(self.motivos),
            "avisos": list(self.avisos),
            "requer_revisao_humana": self.requer_revisao_humana,
            "permite_retry": self.permite_retry,
        }


class EntradaInvalidaError(RuntimeError):
    """Entrada bloqueada: o documento/texto não pode entrar no pipeline.

    Carrega o :class:`ResultadoEntrada` para inspeção do motivo sem reparsear a
    mensagem. É uma subclasse de ``RuntimeError`` — quem já tratava a falha de
    extração como ``RuntimeError`` continua funcionando.
    """

    def __init__(self, mensagem: str, resultado: ResultadoEntrada) -> None:
        super().__init__(mensagem)
        self.resultado = resultado


# ---------------------------------------------------------------------------
# Classificadores puros (sem efeitos colaterais, sem eventos) — fáceis de testar
# ---------------------------------------------------------------------------


def classificar_arquivo_pdf(pdf_path: str | Path) -> ResultadoEntrada:
    """Valida um caminho de PDF ANTES da extração (determinístico, sem LLM).

    Cobre os "problemas básicos" da atividade. Todos os casos são bloqueios sem
    retry: repetir a leitura de um arquivo inexistente/corrompido/protegido não
    muda o resultado.
    """
    path = Path(pdf_path)

    if not path.exists():
        return _bloqueio_arquivo(f"arquivo inexistente: '{path}'")
    if not path.is_file():
        return _bloqueio_arquivo(f"o caminho não é um arquivo: '{path}'")
    if path.suffix.lower() != ".pdf":
        return _bloqueio_arquivo(
            f"formato incorreto: esperado '.pdf', recebido '{path.suffix or 'sem extensão'}'"
        )

    try:
        conteudo = path.read_bytes()
    except OSError as exc:
        return _bloqueio_arquivo(f"não foi possível ler o arquivo: {exc}")

    if not conteudo:
        return _bloqueio_arquivo("arquivo vazio (0 bytes)")
    if not conteudo.startswith(_ASSINATURA_PDF):
        return _bloqueio_arquivo(
            "cabeçalho '%PDF' ausente: arquivo corrompido ou não é um PDF"
        )
    if b"/Encrypt" in conteudo:
        return _bloqueio_arquivo(
            "PDF protegido/criptografado (/Encrypt): forneça uma cópia sem senha"
        )

    return ResultadoEntrada(decisao=DecisaoEntrada.OK, etapa="arquivo")


def classificar_documento_extraido(doc: Any) -> ResultadoEntrada:
    """Valida o resultado da extração (um ``ExtractedDocument``) sem efeitos colaterais.

    Não importa o tipo concreto: usa apenas ``doc.text``, ``doc.num_pages`` e
    ``doc.warnings`` (o contrato comum do Grupo 3). Assim a mesma função serve
    para o objeto real e para exemplos/fakes de teste.
    """
    texto = getattr(doc, "text", "") or ""
    stripped = texto.strip()
    avisos_extrator = list(getattr(doc, "warnings", []) or [])
    num_pages = int(getattr(doc, "num_pages", 0) or 0)

    # 1) Texto vazio — pode ser PDF escaneado/protegido: recuperável via OCR.
    if not stripped:
        return ResultadoEntrada(
            decisao=DecisaoEntrada.RETRY,
            etapa="extracao",
            motivos=[
                "a extração não produziu texto utilizável (possível PDF escaneado "
                "ou protegido); uma nova tentativa com OCR pode recuperar o conteúdo"
            ],
            avisos=avisos_extrator,
            requer_revisao_humana=True,
            permite_retry=True,
        )

    # 2) Texto muito curto — segue, mas com alerta (extração possivelmente incompleta).
    if len(stripped) < MIN_CARACTERES_UTIL:
        return ResultadoEntrada(
            decisao=DecisaoEntrada.ALERTA,
            etapa="extracao",
            motivos=[
                f"texto extraído muito curto ({len(stripped)} caracteres, abaixo do "
                f"mínimo de {MIN_CARACTERES_UTIL}): extração possivelmente incompleta"
            ],
            avisos=avisos_extrator,
            requer_revisao_humana=True,
        )

    # 3) Caracteres ilegíveis em excesso — extração provavelmente corrompida.
    razao = _razao_ilegivel(stripped)
    if razao > MAX_RAZAO_ILEGIVEL:
        return ResultadoEntrada(
            decisao=DecisaoEntrada.ALERTA,
            etapa="extracao",
            motivos=[
                f"alta proporção de caracteres ilegíveis ({razao:.0%}): a extração "
                "pode estar corrompida — recomenda-se revisão humana"
            ],
            avisos=avisos_extrator,
            requer_revisao_humana=True,
        )

    # 4) Baixa densidade por página e/ou avisos do extrator — segue com alerta.
    motivos_alerta: list[str] = []
    if num_pages > 0:
        densidade = len(stripped) / num_pages
        if densidade < MIN_CHARS_POR_PAGINA:
            motivos_alerta.append(
                f"baixa densidade de texto ({densidade:.0f} caracteres/página em "
                f"{num_pages} página(s)): pode haver páginas sem conteúdo"
            )
    if avisos_extrator:
        motivos_alerta.extend(f"aviso do extrator: {a}" for a in avisos_extrator)

    if motivos_alerta:
        return ResultadoEntrada(
            decisao=DecisaoEntrada.ALERTA,
            etapa="extracao",
            motivos=motivos_alerta,
            avisos=avisos_extrator,
            requer_revisao_humana=True,
        )

    # 5) Sem ressalvas — segue normalmente.
    return ResultadoEntrada(decisao=DecisaoEntrada.OK, etapa="extracao")


def _bloqueio_arquivo(motivo: str) -> ResultadoEntrada:
    """Atalho para um bloqueio de arquivo (sem retry, requer atenção humana)."""
    return ResultadoEntrada(
        decisao=DecisaoEntrada.BLOQUEAR,
        etapa="arquivo",
        motivos=[motivo],
        requer_revisao_humana=True,
        permite_retry=False,
    )


def _razao_ilegivel(texto: str) -> float:
    """Fração de caracteres ilegíveis (controle/formatação e '�') no texto.

    Quebras de linha e tabulações comuns NÃO contam como ilegíveis. Símbolos,
    acentos e pontuação Unicode legítimos também não — só caracteres de
    categoria "C" (control/format/surrogate/uso privado/não atribuído) e o
    caractere de substituição U+FFFD, típicos de extração corrompida.
    """
    if not texto:
        return 0.0
    ilegiveis = 0
    for ch in texto:
        if ch in "\n\r\t":
            continue
        if ch == "�" or unicodedata.category(ch).startswith("C"):
            ilegiveis += 1
    return ilegiveis / len(texto)


# ---------------------------------------------------------------------------
# Emissão de eventos (mesma trilha de eventos_validacao.py / trace do Grupo 3)
# ---------------------------------------------------------------------------


def _emitir(
    resultado: ResultadoEntrada,
    *,
    run_id: str,
    fase: str,
    schema: str,
    categoria: str,
    tentativa: int,
    max_tentativas: int,
    correcao: dict | None = None,
) -> None:
    """Grava um EventoValidacao correspondente a ``resultado`` (JSONL + trace)."""
    status = {
        CATEGORIA_PASSOU_DE_PRIMEIRA: "sucesso",
        CATEGORIA_PASSOU_APOS_CORRECAO: "sucesso",
        CATEGORIA_ALERTA_ENTRADA: "alerta",
        CATEGORIA_FALHOU_RECUPERAVEL: "falha",
        CATEGORIA_CORRIGIDO: "correcao_aplicada",
        CATEGORIA_BLOQUEADO: "bloqueado",
    }[categoria]
    # "erro" só faz sentido em categorias problemáticas; sucesso/correção
    # não carregam mensagem de erro (a correção mostra o diff em correcao_aplicada).
    categorias_com_erro = (CATEGORIA_ALERTA_ENTRADA, CATEGORIA_FALHOU_RECUPERAVEL, CATEGORIA_BLOQUEADO)
    emitir_evento(EventoValidacao(
        run_id=run_id,
        fase=fase,
        agente=AGENTE_ENTRADA,
        schema=schema,
        tentativa=tentativa,
        max_tentativas=max_tentativas,
        categoria=categoria,
        status=status,
        erro=resultado.mensagem if categoria in categorias_com_erro else None,
        correcao_aplicada=correcao,
        requer_revisao_humana=resultado.requer_revisao_humana,
    ))


# ---------------------------------------------------------------------------
# Interface de integração 1: validação de arquivo (pré-extração)
# ---------------------------------------------------------------------------


def validar_arquivo_pdf(
    pdf_path: str | Path,
    *,
    run_id: str | None = None,
    fase: str = FASE_ENTRADA,
    exigir: bool = True,
) -> ResultadoEntrada:
    """Valida o arquivo PDF antes de extrair, emite o evento e (por padrão) bloqueia.

    Retorna o :class:`ResultadoEntrada`. Com ``exigir=True`` (padrão), levanta
    :class:`EntradaInvalidaError` se o arquivo for bloqueado — para o pipeline
    nunca seguir com um arquivo inválido em silêncio. Passe ``exigir=False``
    para apenas classificar e registrar (usado em testes/inspeção).
    """
    run_id = run_id or gerar_run_id()
    resultado = classificar_arquivo_pdf(pdf_path)
    categoria = CATEGORIA_BLOQUEADO if resultado.bloqueado else CATEGORIA_PASSOU_DE_PRIMEIRA
    _emitir(
        resultado, run_id=run_id, fase=fase, schema=SCHEMA_ARQUIVO,
        categoria=categoria, tentativa=1, max_tentativas=1,
    )
    if exigir and resultado.bloqueado:
        raise EntradaInvalidaError(resultado.mensagem, resultado)
    return resultado


# ---------------------------------------------------------------------------
# Interface de integração 2: validação do documento extraído (pós-extração)
# ---------------------------------------------------------------------------


def registrar_falha_extracao(
    pdf_path: str | Path,
    erro: Exception,
    *,
    run_id: str | None = None,
    fase: str = FASE_ENTRADA,
) -> EntradaInvalidaError:
    """Registra, como evento estruturado, uma falha ocorrida DURANTE a extração.

    O extrator (Grupo 3) levanta exceção quando o parser não consegue ler o PDF
    — o caso clássico é o **PDF protegido por senha**, mas também PDFs
    corrompidos de um jeito que só o parser detecta. Esta função garante que
    esse bloqueio vire um evento ``bloqueado`` correlacionado ao ``run_id`` (não
    fica só no traceback/console) e devolve uma :class:`EntradaInvalidaError`
    pronta para o chamador levantar. Não faz retry: um PDF protegido/corrompido
    continua igual na tentativa seguinte.

    Uso típico (em ``pipeline.py::extract_pdf_input``)::

        try:
            doc = extractor.extract(pdf_path)
        except Exception as exc:
            raise registrar_falha_extracao(pdf_path, exc, run_id=run_id, fase=fase) from exc
    """
    run_id = run_id or gerar_run_id()
    motivo = f"falha ao extrair '{Path(pdf_path).name}': {erro}"
    resultado = ResultadoEntrada(
        decisao=DecisaoEntrada.BLOQUEAR, etapa="extracao",
        motivos=[motivo], requer_revisao_humana=True,
    )
    _emitir(
        resultado, run_id=run_id, fase=fase, schema=SCHEMA_EXTRACAO,
        categoria=CATEGORIA_BLOQUEADO, tentativa=1, max_tentativas=1,
    )
    return EntradaInvalidaError(motivo, resultado)


def validar_documento_extraido(
    doc: Any,
    *,
    run_id: str | None = None,
    fase: str = FASE_ENTRADA,
    tentativa: int = 1,
    max_tentativas: int = 1,
    exigir: bool = True,
) -> ResultadoEntrada:
    """Valida o documento extraído, emite o evento e (por padrão) bloqueia se preciso.

    Este é o caminho SEM retry (usado na fase 0 do pipeline, onde não há
    estratégia de reextração ligada): uma decisão ``RETRY`` — ex.: texto vazio —
    é tratada como bloqueio, porque não há como reextrair aqui. A nuance
    "recuperável" é preservada no orquestrador :func:`validar_entrada_com_retry`.
    """
    run_id = run_id or gerar_run_id()
    resultado = classificar_documento_extraido(doc)

    if resultado.decisao is DecisaoEntrada.OK:
        categoria = (
            CATEGORIA_PASSOU_DE_PRIMEIRA if tentativa == 1 else CATEGORIA_PASSOU_APOS_CORRECAO
        )
    elif resultado.decisao is DecisaoEntrada.ALERTA:
        categoria = CATEGORIA_ALERTA_ENTRADA
    else:  # RETRY ou BLOQUEAR sem estratégia de retry aqui → bloqueio.
        categoria = CATEGORIA_BLOQUEADO

    _emitir(
        resultado, run_id=run_id, fase=fase, schema=SCHEMA_DOCUMENTO,
        categoria=categoria, tentativa=tentativa, max_tentativas=max_tentativas,
    )

    if exigir and categoria == CATEGORIA_BLOQUEADO:
        raise EntradaInvalidaError(resultado.mensagem, resultado)
    return resultado


# ---------------------------------------------------------------------------
# Interface de integração 3: orquestrador com retry (ciclo completo)
# ---------------------------------------------------------------------------


def validar_entrada_com_retry(
    pdf_path: str | Path,
    extrair: Callable[[str | Path], Any],
    *,
    reextrair: Callable[[str | Path], Any] | None = None,
    run_id: str | None = None,
    fase: str = FASE_ENTRADA,
    max_tentativas: int = 2,
) -> Any:
    """Orquestra a entrada ponta a ponta: arquivo → extração → documento → retry.

    Emite a MESMA sequência de categorias de ``validacao_retry.py`` (sucesso de
    primeira / falha recuperável / corrigido / passou após correção / bloqueado),
    ligada ao ``run_id`` da execução.

    Parameters
    ----------
    extrair:
        Função que recebe o caminho e devolve um documento extraído (o
        ``extractor.extract`` do Grupo 3, ou um fake nos testes).
    reextrair:
        Estratégia de NOVA tentativa quando o resultado é recuperável (ex.:
        reextrair com OCR). ``None`` desliga o retry: um resultado recuperável
        vira bloqueio (não se repete cegamente uma extração que não muda nada).
    max_tentativas:
        Limite de tentativas de extração (padrão 2: original + 1 reextração).

    Returns
    -------
    O documento extraído aprovado (decisão ``OK`` ou ``ALERTA``).

    Raises
    ------
    EntradaInvalidaError
        Quando o arquivo ou o documento são bloqueados.
    """
    run_id = run_id or gerar_run_id()

    # Etapa 1 — arquivo (bloqueio sem retry).
    validar_arquivo_pdf(pdf_path, run_id=run_id, fase=fase, exigir=True)

    # Etapa 2 — extração + validação do documento, com retry quando recuperável.
    doc = extrair(pdf_path)
    for tentativa in range(1, max_tentativas + 1):
        resultado = classificar_documento_extraido(doc)

        if resultado.aprovado:
            categoria = (
                CATEGORIA_PASSOU_DE_PRIMEIRA if tentativa == 1 and resultado.decisao is DecisaoEntrada.OK
                else CATEGORIA_PASSOU_APOS_CORRECAO if resultado.decisao is DecisaoEntrada.OK
                else CATEGORIA_ALERTA_ENTRADA
            )
            _emitir(
                resultado, run_id=run_id, fase=fase, schema=SCHEMA_DOCUMENTO,
                categoria=categoria, tentativa=tentativa, max_tentativas=max_tentativas,
            )
            return doc

        pode_retentar = (
            resultado.decisao is DecisaoEntrada.RETRY
            and resultado.permite_retry
            and reextrair is not None
            and tentativa < max_tentativas
        )
        if not pode_retentar:
            _emitir(
                resultado, run_id=run_id, fase=fase, schema=SCHEMA_DOCUMENTO,
                categoria=CATEGORIA_BLOQUEADO, tentativa=tentativa, max_tentativas=max_tentativas,
            )
            raise EntradaInvalidaError(resultado.mensagem, resultado)

        # Falha recuperável: registra e tenta reextrair.
        _emitir(
            resultado, run_id=run_id, fase=fase, schema=SCHEMA_DOCUMENTO,
            categoria=CATEGORIA_FALHOU_RECUPERAVEL, tentativa=tentativa, max_tentativas=max_tentativas,
        )
        novo_doc = reextrair(pdf_path)

        # Evita registrar "correção aplicada" quando os dados não mudaram de fato.
        texto_antes = (getattr(doc, "text", "") or "").strip()
        texto_depois = (getattr(novo_doc, "text", "") or "").strip()
        if texto_antes == texto_depois:
            bloqueio = ResultadoEntrada(
                decisao=DecisaoEntrada.BLOQUEAR,
                etapa="extracao",
                motivos=[
                    "a reextração não alterou o texto (mesmo resultado): não há o "
                    "que recuperar — bloqueado para revisão humana"
                ],
                requer_revisao_humana=True,
            )
            _emitir(
                bloqueio, run_id=run_id, fase=fase, schema=SCHEMA_DOCUMENTO,
                categoria=CATEGORIA_BLOQUEADO, tentativa=tentativa, max_tentativas=max_tentativas,
            )
            raise EntradaInvalidaError(bloqueio.mensagem, bloqueio)

        # Houve mudança real → registra a correção (com um resumo do que mudou).
        correcao = diff_correcao(
            {"text_chars": len(texto_antes)}, {"text_chars": len(texto_depois)}
        ) or None
        resultado_corrigido = ResultadoEntrada(
            decisao=DecisaoEntrada.OK, etapa="extracao",
            motivos=[f"reextração recuperou texto ({len(texto_depois)} caracteres)"],
        )
        _emitir(
            resultado_corrigido, run_id=run_id, fase=fase, schema=SCHEMA_DOCUMENTO,
            categoria=CATEGORIA_CORRIGIDO, tentativa=tentativa, max_tentativas=max_tentativas,
            correcao=correcao,
        )
        doc = novo_doc

    # Esgotou as tentativas sem aprovar.
    resultado_final = classificar_documento_extraido(doc)
    _emitir(
        resultado_final, run_id=run_id, fase=fase, schema=SCHEMA_DOCUMENTO,
        categoria=CATEGORIA_BLOQUEADO, tentativa=max_tentativas, max_tentativas=max_tentativas,
    )
    raise EntradaInvalidaError(resultado_final.mensagem, resultado_final)
