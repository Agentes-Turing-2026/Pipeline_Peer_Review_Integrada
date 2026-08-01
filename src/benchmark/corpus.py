"""Corpus de PDFs reais para o benchmark de diagnóstico — Grupo 2.

Um `DocumentoCorpus` descreve UM artigo do corpus (id, área, características)
sem se comprometer com onde o PDF mora: `fonte_url` (baixável) e
`caminho_local` (já em disco) são independentes um do outro, e podem os DOIS
ser `None`. Isso não é um estado inválido — é o caso "smoke test": o
executor roda o pipeline com `pdf_path=None`, que usa o artigo de exemplo
embutido (`examples/example_article.txt`), permitindo validar a ferramenta
(e o pipeline) sem depender de nenhum PDF real ainda não coletado.

`carregar_corpus` falha explicitamente (ValueError) em vez de inventar
defaults silenciosos para um campo obrigatório ausente ou tolerar ids
duplicados. Um id duplicado quebraria o UPSERT por doc_id em
`executar.py` (dois documentos sobrescrevendo o mesmo registro sem
aviso) e um campo ausente (ex.: `area`) quebraria silenciosamente a
segmentação do comparativo mais adiante — melhor travar aqui, na leitura
do manifesto, apontando exatamente qual documento e qual campo faltam, do
que descobrir tarde num relatório incompleto.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

CAMPOS_OBRIGATORIOS = ("id", "titulo", "area", "caracteristicas")


@dataclass
class DocumentoCorpus:
    """Um documento do corpus de benchmark.

    `fonte_url` e `caminho_local` podem ambos ser `None` (ver docstring do
    módulo) — nesse caso não há PDF real associado e o executor deve rodar
    com `pdf_path=None`.
    """

    id: str
    titulo: str
    area: str
    fonte_url: str | None = None
    caminho_local: str | None = None
    caracteristicas: list[str] = field(default_factory=list)
    observacoes: str | None = None


def carregar_corpus(caminho: Path) -> list[DocumentoCorpus]:
    """Lê `{"documentos": [...]}` de `caminho` e valida o manifesto.

    Campos obrigatórios: id, titulo, area, caracteristicas. Ausência de
    qualquer um deles levanta ValueError apontando o documento (pelo id,
    quando presente, ou pelo índice na lista) e o(s) campo(s) faltante(s).
    ids duplicados também levantam ValueError, listando os ids repetidos —
    o manifesto inteiro é rejeitado, nenhum documento é carregado
    parcialmente.
    """
    bruto = json.loads(Path(caminho).read_text(encoding="utf-8"))
    documentos_brutos = bruto.get("documentos", [])

    documentos: list[DocumentoCorpus] = []
    ids_vistos: set[str] = set()
    ids_duplicados: set[str] = set()

    for indice, item in enumerate(documentos_brutos):
        identificador = item.get("id")
        rotulo_doc = identificador if identificador else f"índice {indice}"
        campos_ausentes = [campo for campo in CAMPOS_OBRIGATORIOS if campo not in item]
        if campos_ausentes:
            raise ValueError(
                f"Documento do corpus ({rotulo_doc}) sem campo(s) obrigatório(s): "
                f"{', '.join(campos_ausentes)}."
            )

        if identificador in ids_vistos:
            ids_duplicados.add(identificador)
        ids_vistos.add(identificador)

        documentos.append(
            DocumentoCorpus(
                id=item["id"],
                titulo=item["titulo"],
                area=item["area"],
                fonte_url=item.get("fonte_url"),
                caminho_local=item.get("caminho_local"),
                caracteristicas=list(item["caracteristicas"]),
                observacoes=item.get("observacoes"),
            )
        )

    if ids_duplicados:
        raise ValueError(
            "ids duplicados no corpus: " + ", ".join(sorted(ids_duplicados))
        )

    return documentos


def resolver_pdf_local(doc: DocumentoCorpus, cache_dir: Path) -> Path | None:
    """Resolve o PDF em disco para `doc`, baixando de `fonte_url` se preciso.

    Ordem: `caminho_local` (se existir de fato no disco) vence; senão,
    baixa de `fonte_url` para `cache_dir/{doc.id}.pdf` (reaproveitando o
    cache se já baixado); se nenhum dos dois estiver definido, retorna
    `None` — caso smoke test, o chamador deve passar `pdf_path=None` para
    `run_demo`. Erro de rede/HTTP propaga como exceção clara: um download
    que falha não pode virar silenciosamente um "documento sem PDF real",
    que é um caso válido e diferente (mascarar um teria o benchmark
    reportando o doc errado como smoke test).
    """
    if doc.caminho_local:
        caminho_existente = Path(doc.caminho_local)
        if caminho_existente.exists():
            return caminho_existente

    if not doc.fonte_url:
        return None

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    destino = cache_dir / f"{doc.id}.pdf"
    if destino.exists():
        return destino

    try:
        with urllib.request.urlopen(doc.fonte_url) as resposta:
            conteudo = resposta.read()
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(
            f"Falha ao baixar o PDF do documento '{doc.id}' em '{doc.fonte_url}': {exc}"
        ) from exc

    destino.write_bytes(conteudo)
    return destino
