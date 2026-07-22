"""MAP du pipeline de synthèse corpus : extraction d'un aspect depuis UN document,
à partir de son matériau (résumés RAPTOR d'abord). LLM injectable (tests offline)."""
from __future__ import annotations

_EXTRACT_PROMPT = """[RÔLE] Tu EXTRAIS d'un document technique tous les éléments correspondant à : {aspect}.

[CONSIGNES]
- Appuie-toi UNIQUEMENT sur le MATÉRIAU fourni. Zéro invention.
- Renvoie une LISTE : un élément par ligne, préfixé « - ». Rien d'autre (pas d'intro, pas de conclusion).
- Chaque élément est factuel, concis, dans la LANGUE du document. Conserve les identifiants/valeurs exacts.
- Si le document ne contient AUCUN élément pour « {aspect} », renvoie une liste vide (aucune ligne).

[MATÉRIAU - document « {document} »]
{material}

[ÉLÉMENTS EXTRAITS pour « {aspect} »]"""


def _parse_items(text: str) -> list[str]:
    items = []
    for line in (text or "").splitlines():
        s = line.strip().lstrip("-*•").strip()
        if s:
            items.append(s)
    return items


def extract_from_document(document, aspect, llm=None, gather=None):
    """Extrait `aspect` du document. Retourne {document, items, raw}."""
    if gather is None:
        from core.summarize import _gather_material as gather
    if llm is None:
        from core.model_router import build_llm
        llm = build_llm("generate").invoke

    material, _n, _basis = gather(document)
    if not (material or "").strip():
        return {"document": document, "items": [], "raw": ""}
    from core.summarize import _MAX_MATERIAL_CHARS
    prompt = _EXTRACT_PROMPT.format(aspect=aspect, document=document,
                                    material=material[:_MAX_MATERIAL_CHARS])
    raw = llm(prompt)
    return {"document": document, "items": _parse_items(raw), "raw": raw}
