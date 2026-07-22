"""Pipeline DÉTERMINISTE de synthèse corpus-large (map-reduce), en streaming.
Pré-filtre les documents pertinents, extrait l'aspect par document (concurrence
bornée), puis réduit en une synthèse catégorisée avec attribution par source.
LLM et étapes injectables pour des tests offline déterministes."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from utils.logging_config import get_logger

logger = get_logger("rag.synthesize")

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

_REDUCE_MERGE_PROMPT = """[RÔLE] Tu FUSIONNES plusieurs synthèses PARTIELLES (déjà catégorisées) de l'aspect « {aspect} » en UNE SEULE synthèse finale.

[CONSIGNES]
- Appuie-toi UNIQUEMENT sur les SYNTHÈSES PARTIELLES fournies. Zéro invention.
- Chaque élément porte une ou plusieurs étiquettes source `[src: ...]` héritées des
  synthèses partielles - NE LES INVENTE PAS et NE LES REFORMULE PAS : recopie-les
  VERBATIM, caractère pour caractère.
- DÉDUPLIQUE les éléments équivalents apparaissant dans plusieurs synthèses
  partielles et REGROUPE-les par CATÉGORIE pertinente (harmonise les catégories si
  elles diffèrent d'une synthèse partielle à l'autre).
- Pour chaque élément dédupliqué/regroupé qui apparaît dans plusieurs synthèses
  partielles, CONSERVE et FUSIONNE ses étiquettes `[src: ...]` (ex : `[src: A.md, src: B.md]`).
- Rédige dans la langue des synthèses partielles. Factuel, structuré, exhaustif sur le matériau fourni.

[SYNTHÈSES PARTIELLES - aspect « {aspect} »]
{material}

[SYNTHÈSE FINALE FUSIONNÉE de « {aspect} », avec étiquettes [src: ...] préservées]"""


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


def _batch_extractions(extractions, budget: int):
    """Partitionne gloutonnement les extractions NON VIDES en lots consécutifs dont
    le matériau `_format_material` tient sous `budget` caractères (préserve l'ordre).
    Un document dont le matériau, à lui seul, dépasse déjà le budget part quand même
    dans son propre lot (jamais tronqué ni écarté silencieusement)."""
    non_empty = [ex for ex in extractions if ex and ex.get("items")]
    batches = []
    current = []
    for ex in non_empty:
        candidate = current + [ex]
        if current and len(_format_material(candidate)) > budget:
            batches.append(current)
            current = [ex]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


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

    def _safe_extract(d):
        try:
            return extract(d, aspect)
        except Exception as e:
            logger.warning("[synthesize] MAP échec sur %s : %s", d, e)
            return {"document": d, "items": [], "raw": ""}

    # MAP (concurrence bornée) — pool.map préserve l'ordre des documents. Un échec
    # sur UN document ne fait pas échouer tout le run (dégrade en items vides).
    workers = max(1, int(CORPUS_MAP_CONCURRENCY))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        extractions = list(pool.map(_safe_extract, docs))
    for i, (d, res) in enumerate(zip(docs, extractions), 1):
        n_items = len(res["items"]) if res and res.get("items") else 0
        yield {"type": "map", "index": i, "total": len(docs), "document": d, "n_items": n_items}

    # REDUCE
    from core.summarize import _MAX_MATERIAL_CHARS

    material = _format_material(extractions)
    total_items = sum(len(e["items"]) for e in extractions if e and e.get("items"))
    if not material.strip():
        yield {"type": "stage", "stage": "reduce"}
        yield {"type": "done", "result": {"answer": "", "documents": list(docs), "n_items": 0}}
        return

    if len(material) <= _MAX_MATERIAL_CHARS:
        # Cas courant (petit corpus) : un unique REDUCE, comportement inchangé.
        yield {"type": "stage", "stage": "reduce"}
        answer = llm(_REDUCE_PROMPT.format(aspect=aspect, material=material))
    else:
        # Corpus volumineux : le matériau dépasserait la fenêtre de contexte et
        # serait tronqué SILENCIEUSEMENT par le modèle (perte d'exhaustivité). On
        # partitionne en lots bornés, réduit chaque lot, puis fusionne les
        # synthèses partielles - jamais de troncature silencieuse (glass-box).
        batches = _batch_extractions(extractions, _MAX_MATERIAL_CHARS)
        yield {"type": "stage", "stage": "reduce", "batched": True, "batches": len(batches)}
        partials = []
        for batch in batches:
            batch_material = _format_material(batch)
            partial = llm(_REDUCE_PROMPT.format(aspect=aspect, material=batch_material))
            partials.append(partial)
        merged_material = "\n\n".join(partials)
        answer = llm(_REDUCE_MERGE_PROMPT.format(aspect=aspect, material=merged_material))

    yield {"type": "token", "text": answer}
    yield {"type": "done", "result": {"answer": answer, "documents": list(docs),
                                       "n_items": total_items}}
