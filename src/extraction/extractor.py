"""Extratores de PDF por trás de uma interface SUBSTITUÍVEL (Grupo 3).

A extração é uma etapa DETERMINÍSTICA e obrigatória do pipeline quando a entrada
é um PDF: ela não depende de decisão de LLM para ser chamada. O pipeline conhece
apenas a interface :class:`PdfExtractor` e o contrato
:class:`~extraction.document.ExtractedDocument` — trocar o LiteParse por outro
extrator no futuro exige apenas uma nova subclasse.

Extrator padrão: `LiteParse <https://pypi.org/project/liteparse/>`_ (local,
leve, com OCR opcional). A importação é adiada para dentro do método: o modo
mock e os testes offline funcionam sem o pacote instalado.

Qualidade: quando a extração não tem qualidade suficiente (sem texto, páginas
possivelmente escaneadas, densidade de texto muito baixa), o extrator NÃO decide
sozinho o destino do documento — ele preenche ``warnings`` no contrato para a
camada de validação (Grupo 1) decidir se bloqueia ou encaminha para revisão.
Apenas erros estruturais (arquivo inexistente, PDF ilegível) viram exceção.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import Path

from .document import ExtractedDocument, make_document_id

# Abaixo desta média de caracteres por página, o texto é suspeito (provável
# digitalização/imagem). Limiar deliberadamente conservador.
MIN_AVG_CHARS_PER_PAGE = 200


class PdfExtractor(ABC):
    """Interface comum de extração: caminho de PDF -> ``ExtractedDocument``."""

    #: Nome estável do extrator (registrado no contrato e no trace).
    name: str = "extractor"

    @property
    @abstractmethod
    def version(self) -> str:
        """Versão do extrator utilizado (registrada no contrato e no trace)."""

    @abstractmethod
    def extract(self, pdf_path: str | Path) -> ExtractedDocument:
        """Extrai o texto de ``pdf_path`` e devolve o contrato preenchido."""


def _quality_warnings(text: str, num_pages: int) -> list[str]:
    """Avisos determinísticos de qualidade sobre o texto extraído."""
    warnings: list[str] = []
    stripped = text.strip()
    if not stripped:
        warnings.append(
            "extração não produziu texto: o PDF pode ser escaneado (imagem) ou "
            "protegido; considere OCR ou outra fonte do documento"
        )
        return warnings
    if num_pages > 0:
        media = len(stripped) / num_pages
        if media < MIN_AVG_CHARS_PER_PAGE:
            warnings.append(
                f"baixa densidade de texto ({media:.0f} caracteres/página em "
                f"{num_pages} página(s)): partes do PDF podem ser escaneadas ou "
                "a extração pode estar incompleta"
            )
    return warnings


class LiteParseExtractor(PdfExtractor):
    """Extrator local baseado no LiteParse (primeiro extrator avaliado).

    Parameters
    ----------
    ocr_enabled:
        Habilita OCR para páginas escaneadas (requer Tesseract disponível).
        Desligado por padrão: nesta primeira versão o objetivo é texto digital;
        PDFs escaneados geram *warnings* para a camada de validação decidir.
    """

    name = "liteparse"

    def __init__(self, ocr_enabled: bool = False):
        self._ocr_enabled = ocr_enabled

    @property
    def version(self) -> str:
        import importlib.metadata

        try:
            return importlib.metadata.version("liteparse")
        except importlib.metadata.PackageNotFoundError:
            return "desconhecida"

    def extract(self, pdf_path: str | Path) -> ExtractedDocument:
        # Importação adiada: o resto do pipeline (modo mock, testes offline)
        # funciona sem o liteparse instalado.
        try:
            from liteparse import LiteParse
        except ImportError as exc:  # pragma: no cover - depende do ambiente
            raise RuntimeError(
                "O pacote 'liteparse' não está instalado. Instale as dependências "
                "com `pip install -r requirements.txt` para usar entrada por PDF."
            ) from exc

        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF não encontrado: {path}")

        content = path.read_bytes()
        parser = LiteParse(ocr_enabled=self._ocr_enabled, quiet=True)

        inicio = time.perf_counter()
        result = parser.parse(content)
        duration_s = time.perf_counter() - inicio

        text = (result.text or "").strip()
        num_pages = int(getattr(result, "num_pages", None) or len(result.pages))
        warnings = _quality_warnings(text, num_pages)

        return ExtractedDocument(
            document_id=make_document_id(content),
            filename=path.name,
            text=text,
            num_pages=num_pages,
            extractor=self.name,
            extractor_version=self.version,
            extraction_duration_s=duration_s,
            warnings=warnings,
        )


def get_default_extractor() -> PdfExtractor:
    """Extrator padrão do pipeline (hoje: LiteParse sem OCR).

    Ponto único de troca: para substituir o extrator no futuro, basta devolver
    outra implementação de :class:`PdfExtractor` aqui (ou injetar uma via
    parâmetro em ``run_demo``).
    """
    return LiteParseExtractor()
