"""Entrada real por PDF (Grupo 3): contrato de documento extraído + extratores.

Ponto único de importação::

    from extraction import ExtractedDocument, PdfExtractor, get_default_extractor
"""

from __future__ import annotations

from .document import ExtractedDocument, make_document_id
from .extractor import (
    MIN_AVG_CHARS_PER_PAGE,
    LiteParseExtractor,
    PdfExtractor,
    get_default_extractor,
)

__all__ = [
    "ExtractedDocument",
    "make_document_id",
    "PdfExtractor",
    "LiteParseExtractor",
    "get_default_extractor",
    "MIN_AVG_CHARS_PER_PAGE",
]
