"""Rattachement des extraits verbatim aux documents/pages. Aucune invention."""
from __future__ import annotations


def _norm(s: str) -> str:
    return " ".join(s.split()).casefold()


def resolve_preuves(extraits: list[str], document_id: str,
                    chunks: list[dict]) -> tuple[list[dict], list[str]]:
    """Localise chaque extrait dans les chunks du document (page réelle ou None)."""
    preuves: list[dict] = []
    warnings: list[str] = []
    for extrait in extraits:
        ext = (extrait or "").strip()
        if not ext:
            continue
        page = None
        needle = _norm(ext)
        for chunk in chunks:
            if needle and needle in _norm(chunk.get("content", "")):
                page = chunk.get("page_number")
                break
        if page is None:
            warnings.append(f"Preuve non localisée dans {document_id} : « {ext[:60]} »")
        preuves.append({"document_id": document_id, "source": document_id,
                        "page": page, "extrait": ext})
    return preuves, warnings
