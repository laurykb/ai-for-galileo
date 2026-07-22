"""Pipeline DÉTERMINISTE de synthèse corpus-large (map-reduce), en streaming.
Pré-filtre les documents pertinents, extrait l'aspect par document (concurrence
bornée), puis réduit en une synthèse catégorisée avec attribution par source.
LLM et étapes injectables pour des tests offline déterministes."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

_REDUCE_PROMPT = """[RÔLE] Tu SYNTHÉTISES en une réponse unique les éléments « {aspect} » extraits de plusieurs documents.

[CONSIGNES]
- Appuie-toi UNIQUEMENT sur les ÉLÉMENTS fournis (chacun étiqueté par son document). Zéro invention.
- DÉDUPLIQUE les éléments équivalents et REGROUPE-les par CATÉGORIE pertinente.
- Pour chaque élément, cite entre parenthèses le(s) document(s) source(s).
- Rédige dans la langue des documents. Factuel, structuré, exhaustif sur le matériau fourni.

[ÉLÉMENTS EXTRAITS - aspect « {aspect} »]
{material}

[SYNTHÈSE CATÉGORISÉE de « {aspect} »]"""


def _format_material(extractions) -> str:
    blocks = []
    for ex in extractions:
        if ex and ex.get("items"):
            lines = "\n".join(f"- {it}" for it in ex["items"])
            blocks.append(f"### {ex['document']}\n{lines}")
    return "\n\n".join(blocks)


def synthesize_corpus(question, aspect=None, llm=None, prefilter=None, extract=None):
    """Générateur d'événements glass-box (voir docstring du plan)."""
    aspect = aspect or question
    if prefilter is None:
        from core.corpus import prefilter_documents
        prefilter = lambda a: prefilter_documents(a)
    if extract is None:
        from core.corpus_extract import extract_from_document
        extract = lambda d, a: extract_from_document(d, a)
    if llm is None:
        from core.model_router import build_llm
        llm = build_llm("generate").invoke
    from env_config import CORPUS_MAP_CONCURRENCY

    docs = prefilter(aspect)
    yield {"type": "stage", "stage": "prefilter", "documents": list(docs)}

    if not docs:
        yield {"type": "done", "result": {"answer": "", "documents": [], "n_items": 0}}
        return

    # MAP (concurrence bornée) — pool.map préserve l'ordre des documents.
    workers = max(1, int(CORPUS_MAP_CONCURRENCY))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        extractions = list(pool.map(lambda d: extract(d, aspect), docs))
    for i, (d, res) in enumerate(zip(docs, extractions), 1):
        n_items = len(res["items"]) if res and res.get("items") else 0
        yield {"type": "map", "index": i, "total": len(docs), "document": d, "n_items": n_items}

    # REDUCE
    yield {"type": "stage", "stage": "reduce"}
    material = _format_material(extractions)
    total_items = sum(len(e["items"]) for e in extractions if e and e.get("items"))
    if not material.strip():
        yield {"type": "done", "result": {"answer": "", "documents": list(docs), "n_items": 0}}
        return
    answer = llm(_REDUCE_PROMPT.format(aspect=aspect, material=material))
    yield {"type": "token", "text": answer}
    yield {"type": "done", "result": {"answer": answer, "documents": list(docs),
                                       "n_items": total_items}}
