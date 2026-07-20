"""Demo offline da validação e resiliência da ENTRADA por PDF (Grupo 1).

Roda sem internet, sem GOOGLE_API_KEY e sem o extrator real (liteparse): usa
o contrato comum ``ExtractedDocument`` (Grupo 3) com fakes e arquivos de
fixture temporários — exatamente o que a atividade autoriza enquanto a entrada
real não está ligada.

Demonstra os quatro cenários que a atividade pede para apresentar, todos sob
um único ``run_id`` (como aconteceria dentro de uma execução real), e ao final
lê de volta o arquivo de eventos para mostrar que tudo ficou ligado à mesma
execução:

  1. Documento que PASSA normalmente.
  2. Caso que gera ALERTA (segue, mas pede revisão humana).
  3. Falha RECUPERÁVEL: texto vazio → reextração (OCR) → recuperado.
  4. Caso BLOQUEADO com motivo claro (arquivo corrompido e texto irrecuperável).

Uso:
    python src/demos/demo_entrada_pdf.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]  # .../src (as demos vivem em src/demos/)
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from extraction.document import ExtractedDocument  # noqa: E402
from eventos_validacao import formatar_linha_do_tempo, gerar_run_id, ler_eventos  # noqa: E402
from validacao_entrada import (  # noqa: E402
    EntradaInvalidaError,
    validar_arquivo_pdf,
    validar_documento_extraido,
    validar_entrada_com_retry,
)

LARGURA = 70


def _sep(titulo: str = "") -> None:
    if titulo:
        print(f"\n{'=' * LARGURA}")
        print(f"  {titulo}")
        print("=" * LARGURA)
    else:
        print("-" * LARGURA)


def _doc(text: str, *, warnings=None, num_pages: int = 4) -> ExtractedDocument:
    """Monta um ExtractedDocument de teste (sem depender do extrator real)."""
    return ExtractedDocument(
        document_id="doc_demo",
        filename="artigo_demo.pdf",
        text=text,
        num_pages=num_pages,
        extractor="fake",
        extractor_version="0.0-demo",
        extraction_duration_s=0.03,
        warnings=list(warnings or []),
    )


_TEXTO_VALIDO = (
    "Deep Learning Approaches for Automated Peer Review Quality Assessment. "
    "Abstract: peer review is the cornerstone of scientific quality control, "
    "yet it faces mounting challenges of scale and consistency. In this work we "
    "investigate whether large language models can support—not replace—human "
    "reviewers by auditing structured aspects of a review. We describe a "
    "multi-agent pipeline, its data contracts, and a reliability layer that "
    "validates every hand-off between phases. Results indicate that deterministic "
    "checks catch a meaningful fraction of malformed outputs before they "
    "propagate. " * 3
)


def main() -> None:
    run_id = gerar_run_id()

    print("=" * LARGURA)
    print("  DEMO: Validação e Resiliência da Entrada por PDF — Grupo 1")
    print(f"  run_id da execução: {run_id}")
    print("  (offline — sem GOOGLE_API_KEY · sem internet · sem extrator real)")
    print("=" * LARGURA)

    # -----------------------------------------------------------------------
    _sep("CENÁRIO 1 — Documento válido: PASSA normalmente")
    doc = _doc(_TEXTO_VALIDO)
    resultado = validar_documento_extraido(
        doc, run_id=run_id, fase="cenario_1_valido", exigir=True,
    )
    print(f"  decisão : {resultado.decisao.value.upper()}")
    print(f"  texto   : {len(doc.text)} caracteres em {doc.num_pages} páginas")
    print("  → segue para os agentes (evento categoria=passou_de_primeira).")

    # -----------------------------------------------------------------------
    _sep("CENÁRIO 2 — Extração fraca: gera ALERTA (segue, revisão humana)")
    doc = _doc("Resumo muito curto do artigo.", warnings=["página 3 possivelmente escaneada"])
    resultado = validar_documento_extraido(
        doc, run_id=run_id, fase="cenario_2_alerta", exigir=True,
    )
    print(f"  decisão : {resultado.decisao.value.upper()}")
    for m in resultado.motivos:
        print(f"    • {m}")
    print("  → segue, MAS marcado para revisão humana (evento categoria=alerta).")

    # -----------------------------------------------------------------------
    _sep("CENÁRIO 3 — Falha RECUPERÁVEL: texto vazio → reextração (OCR) → recuperado")

    def extrair_vazio(_path):
        # 1ª tentativa: PDF escaneado, sem texto digital.
        return _doc("", warnings=["extração não produziu texto: PDF provavelmente escaneado"])

    def reextrair_com_ocr(_path):
        # 2ª tentativa: OCR recupera o conteúdo.
        return _doc(_TEXTO_VALIDO)

    doc = validar_entrada_com_retry(
        _pdf_fixture_valido(),
        extrair=extrair_vazio,
        reextrair=reextrair_com_ocr,
        run_id=run_id,
        fase="cenario_3_recuperavel",
        max_tentativas=2,
    )
    print(f"  recuperado: {len(doc.text)} caracteres após reextração com OCR")
    print("  → eventos: falhou_recuperavel → corrigido → passou_apos_correcao.")

    # -----------------------------------------------------------------------
    _sep("CENÁRIO 4 — BLOQUEIO com motivo claro (arquivo corrompido)")
    corrompido = _pdf_fixture_corrompido()
    try:
        validar_arquivo_pdf(corrompido, run_id=run_id, fase="cenario_4_bloqueio", exigir=True)
    except EntradaInvalidaError as exc:
        print(f"  EntradaInvalidaError: {exc}")
        print("  → NÃO extrai, NÃO retenta (retry não conserta arquivo corrompido).")

    _sep("CENÁRIO 4b — BLOQUEIO: texto irrecuperável sem estratégia de OCR")
    try:
        validar_entrada_com_retry(
            _pdf_fixture_valido(),
            extrair=lambda _p: _doc("", warnings=["sem texto"]),
            reextrair=None,  # nenhuma estratégia de recuperação disponível
            run_id=run_id,
            fase="cenario_4b_bloqueio",
        )
    except EntradaInvalidaError as exc:
        print(f"  EntradaInvalidaError: {exc}")
        print("  → texto vazio + sem OCR ⇒ bloqueado (evento categoria=bloqueado).")

    # -----------------------------------------------------------------------
    _sep("LINHA DO TEMPO — eventos desta execução (mesmo run_id)")
    eventos = ler_eventos(run_id=run_id)
    print(f"  {len(eventos)} evento(s) ligados a run_id={run_id[:8]}…:\n")
    print(formatar_linha_do_tempo(eventos))

    _sep()
    print(f"  Arquivo de eventos: {SRC / 'logs' / 'validacao_events.jsonl'}")
    print("  Todos os eventos compartilham o run_id — aparecem na mesma linha")
    print("  do tempo do trace do Grupo 3 e alimentam as métricas do Grupo 2.")
    print("  Demo concluída.")
    print("-" * LARGURA)


def _pdf_fixture_valido() -> Path:
    """Cria um arquivo .pdf temporário com cabeçalho válido (passa na etapa de arquivo)."""
    caminho = Path(tempfile.gettempdir()) / "entrada_demo_valido.pdf"
    caminho.write_bytes(b"%PDF-1.7\n% conteudo de demonstracao\n")
    return caminho


def _pdf_fixture_corrompido() -> Path:
    """Cria um .pdf temporário SEM cabeçalho %PDF (corrompido)."""
    caminho = Path(tempfile.gettempdir()) / "entrada_demo_corrompido.pdf"
    caminho.write_bytes(b"isto nao e um PDF de verdade")
    return caminho


if __name__ == "__main__":
    main()
