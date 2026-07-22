"""Pipeline DÉTERMINISTE de synthèse corpus-large (map-reduce), en streaming.
Pré-filtre les documents pertinents, extrait l'aspect par document (concurrence
bornée), puis réduit en une synthèse catégorisée avec attribution par source.
LLM et étapes injectables pour des tests offline déterministes."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

_REDUCE_PROMPT = """[RÔLE] Tu SYNTHÉTISES en une réponse unique les éléments « {aspect} » extraits de plusieurs documents.

[CONSIGNES]
- Appuie-toi UNIQUEMENT sur les ÉLÉMENTS fournis. Zéro invention.
- Chaque élément porte une étiquette source inline `[src: nom_du_document]` -
  NE L'INVENTE PAS et NE LA REFORMULE PAS : recopie-la VERBATIM, caractère pour
  caractère (ne corrige ni l'orthographe, ni les tirets/underscores, ni l'extension).
- DÉDUPLIQUE les éléments équivalents et REGROUPE-les par CATÉGORIE pertinente.
- Pour chaque élément dédupliqué/regroupé, CONSERVE et FUSIONNE les étiquettes
  `[src: ...]` de tous les documents où il apparaît (ex : `[src: A.md, src: B.md]`).
  N'écris jamais une citation qui ne provient pas d'une étiquette `[src: ...]` fournie.
- Rédige dans la langue des documents. Factuel, structuré, exhaustif sur le matériau fourni.

[ÉLÉMENTS EXTRAITS - aspect « {aspect} »]
{material}

[SYNTHÈSE CATÉGORISÉE de « {aspect} », avec étiquettes [src: ...] préservées]"""


def _format_material(extractions) -> str:
    """Formate le matériau MAP pour le REDUCE : chaque item porte une étiquette
    source INLINE et déterministe `[src: {document}]` (dérivée de `ex["document"]`,
    jamais du LLM) - le REDUCE n'a plus qu'à la recopier verbatim au lieu de citer
    un nom de document de mémoire (source d'hallucinations/déformations)."""
    blocks = []
    for ex in extractions:
        if ex and ex.get("items"):
            document = ex["document"]
            lines = "\n".join(f"- {it}  [src: {document}]" for it in ex["items"])
            blocks.append(f"### {document}\n{lines}")
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
