"""Contrato COMUM de documento extraído (Grupo 3 — entrada real por PDF).

Este é o "contrato de entrada" do pipeline: o resultado estruturado que QUALQUER
extrator de PDF deve produzir antes de alimentar os agentes. Ele é deliberadamente
simples e independente do LiteParse (ou de qualquer outra biblioteca), para que o
extrator possa ser trocado no futuro sem reescrever o pipeline.

Campos (mínimo pedido pela atividade):
    - ``document_id``            identificador do documento (estável por conteúdo);
    - ``filename``               nome do arquivo original;
    - ``text``                   texto extraído (o que alimenta os agentes);
    - ``num_pages``              número de páginas do documento;
    - ``extractor``              nome do extrator utilizado;
    - ``extractor_version``      versão do extrator utilizado;
    - ``extraction_duration_s``  duração da extração em SEGUNDOS;
    - ``warnings``               lista de avisos de qualidade (texto vazio, página
                                 possivelmente escaneada etc.) — a camada de
                                 validação (Grupo 1) decide se bloqueia ou segue.

Um exemplo versionado vive em ``src/examples/example_extracted_document.json``
para os Grupos 1 e 2 trabalharem com dados de teste sem esperar a implementação.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ExtractedDocument:
    """Resultado estruturado da extração de um documento (contrato comum)."""

    document_id: str                  # identificador do documento
    filename: str                     # nome do arquivo original
    text: str                         # texto extraído
    num_pages: int                    # número de páginas
    extractor: str                    # extrator utilizado (ex.: "liteparse")
    extractor_version: str            # versão do extrator (ex.: "2.6.0")
    extraction_duration_s: float      # duração da extração em segundos
    warnings: list[str] = field(default_factory=list)  # avisos de qualidade

    def to_dict(self) -> dict[str, Any]:
        """Serializa o contrato para dict (JSON-compatível)."""
        return asdict(self)

    def to_metadata(self) -> dict[str, Any]:
        """Como ``to_dict``, mas SEM o texto completo (para logs/trace/relatório).

        A atividade pede para não incluir desnecessariamente o conteúdo completo
        do artigo nos logs — este é o payload seguro para anexar a eventos.
        """
        data = self.to_dict()
        data.pop("text")
        data["text_chars"] = len(self.text)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExtractedDocument":
        """Reconstrói o contrato a partir de um dict (ignora campos extras)."""
        campos = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in campos})

    @property
    def has_text(self) -> bool:
        """Indica se a extração produziu algum texto útil."""
        return bool(self.text.strip())


def make_document_id(content: bytes) -> str:
    """Gera um identificador estável para o documento a partir do seu conteúdo.

    Mesmo arquivo -> mesmo id (idempotente entre execuções), o que permite
    correlacionar várias execuções sobre o mesmo documento.
    """
    return f"doc_{hashlib.sha256(content).hexdigest()[:16]}"
