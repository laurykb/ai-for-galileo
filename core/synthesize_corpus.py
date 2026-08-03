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
{output_contract}

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
{output_contract}

[SYNTHÈSES PARTIELLES - aspect « {aspect} »]
{material}

[SYNTHÈSE FINALE FUSIONNÉE de « {aspect} », avec étiquettes [src: ...] préservées]"""


_COVERAGE_PROMPT = """[RÔLE] Tu contrôles la COUVERTURE d une analyse multi-documentaire.
Pour chaque axe obligatoire, décide s il est couvert par au moins un élément factuel et sourcé.
Renvoie UNIQUEMENT un JSON strict : {{\"missing\": [\"axe absent ou insuffisant\"]}}.
N évalue pas le style et n invente rien.

AXES OBLIGATOIRES :
{dimensions}

ANALYSE :
{answer}
"""

_REPAIR_PROMPT = """[RÔLE] Tu CORRIGES une analyse structurée dont certains axes sont absents.
- Appuie-toi uniquement sur l analyse et les compléments fournis.
- Conserve les lignes valides et leurs étiquettes [src: ...].
- Ajoute les faits complémentaires sans invention ni doublon.
{output_contract}

AXES À COMPLÉTER : {missing}

ANALYSE ACTUELLE :
{answer}

COMPLÉMENTS EXTRAITS :
{material}

ANALYSE CORRIGÉE :
"""

def _parse_missing_axes(raw: str, dimensions: list[str]) -> list[str]:
    import json
    try:
        start, end = raw.find("{"), raw.rfind("}") + 1
        data = json.loads(raw[start:end])
        requested = {d.casefold(): d for d in dimensions}
        out = []
        for value in data.get("missing", []):
            key = str(value).strip().casefold()
            if key in requested and requested[key] not in out:
                out.append(requested[key])
        return out
    except (ValueError, TypeError, AttributeError):
        return list(dimensions)

def _judge_coverage(answer: str, dimensions: list[str], llm=None) -> list[str]:
    if llm is None:
        from core.model_router import build_llm
        llm = build_llm("judge").invoke
    from core.prompt_registry import get_prompt
    raw = llm(get_prompt("judge.coverage", _COVERAGE_PROMPT).format(
        dimensions="\n".join(f"- {d}" for d in dimensions), answer=answer[:12000]))
    return _parse_missing_axes(raw, dimensions)

_DEFAULT_OUTPUT_CONTRACT = "- Organise la réponse en sections et listes lisibles."
_STRUCTURED_OUTPUT_CONTRACT = """
- Produis un TABLEAU MARKDOWN avec exactement les colonnes : Axe | Catégorie | Élément | Caractérisation | Objectif lié | Sources.
- Une ligne par élément atomique ; ne fusionne pas deux relations différentes.
- La colonne Sources recopie toutes les étiquettes [src: ...] qui soutiennent la ligne.
- Après le tableau, ajoute une section « Couverture » listant chaque axe demandé et indique : couvert, partiel ou absent.
- N invente jamais une catégorie, un objectif ou une relation pour remplir une case vide.
"""

def _structured_analysis_contract(question: str) -> dict | None:
    """Décompose les demandes structurées fréquentes en axes stables et auditables."""
    from core.router import select_query_strategy
    if select_query_strategy(question)["query_type"] != "structured_aggregate":
        return None
    from core.router import _norm
    q = _norm(question)
    axes = []
    if "source" in q and ("risque" in q or "menace" in q):
        axes += ["catégories de sources de risque", "sources de risque"]
    if "objectif" in q or "target objective" in q:
        axes += ["catégories d objectifs visés", "objectifs visés",
                 "relations source de risque vers objectif visé"]
    if "attaquant" in q or "acteur" in q:
        axes += ["types d attaquants ou acteurs de menace"]
    if not axes:
        axes = ["catégories", "éléments", "caractérisation", "relations"]
    axes = list(dict.fromkeys(axes))
    extraction_aspect = (
        question + "\n\nAXES OBLIGATOIRES : " + "; ".join(axes) +
        ". Pour chaque fait, renvoie une ligne au format : "
        "AXE | CATÉGORIE | ÉLÉMENT | CARACTÉRISATION | OBJECTIF_LIÉ. "
        "Écris INCONNU lorsqu une relation n est pas explicitement étayée."
    )
    return {"dimensions": axes, "extraction_aspect": extraction_aspect,
            "output_contract": _STRUCTURED_OUTPUT_CONTRACT}


def _document_coverage(documents, extractions) -> dict:
    """Mesure déterministe du balayage MAP, sans confondre document vide et échec."""
    attempted = list(documents or [])
    with_evidence = [e.get("document") for e in (extractions or [])
                     if e and e.get("items")]
    empty = [d for d in attempted if d not in set(with_evidence)]
    return {"attempted": len(attempted), "with_evidence": len(with_evidence),
            "ratio": (len(with_evidence) / len(attempted) if attempted else None),
            "documents_with_evidence": with_evidence, "documents_without_evidence": empty}


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


def synthesize_corpus(question, aspect=None, llm=None, prefilter=None, extract=None,
                      coverage_check=None, force_coverage=False, map_only=False):
    """Générateur d'événements glass-box (voir docstring du plan)."""
    aspect = aspect or question
    contract = _structured_analysis_contract(question)
    extraction_aspect = contract["extraction_aspect"] if contract else aspect
    output_contract = contract["output_contract"] if contract else _DEFAULT_OUTPUT_CONTRACT
    if prefilter is None:
        from core.corpus import prefilter_documents
        prefilter = lambda a: prefilter_documents(a)
    if extract is None:
        from core.corpus_extract import extract_from_document
        extract = lambda d, a: extract_from_document(d, a)
    if llm is None:
        from core.model_router import build_llm
        llm = build_llm("synthesize").invoke
    from env_config import CORPUS_MAP_CONCURRENCY

    docs = prefilter(aspect)
    yield {"type": "stage", "stage": "prefilter", "documents": list(docs),
           "structured": bool(contract),
           "dimensions": contract["dimensions"] if contract else []}

    if not docs:
        yield {"type": "done", "result": {"answer": "", "documents": [], "n_items": 0}}
        return

    def _safe_extract(d):
        try:
            return extract(d, extraction_aspect)
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
        yield {"type": "map", "index": i, "total": len(docs), "document": d, "n_items": n_items,
               "coverage_status": "covered" if n_items else "no_evidence"}

    document_coverage = _document_coverage(docs, extractions)
    total_items = sum(len(e["items"]) for e in extractions if e and e.get("items"))
    if map_only:
        yield {"type": "stage", "stage": "map_complete",
               "items": total_items, "documents": len(docs)}
        yield {"type": "done", "result": {
            "answer": "", "documents": list(docs), "n_items": total_items,
            "extractions": extractions,
            "document_coverage": document_coverage,
        }}
        return

    # REDUCE
    from core.summarize import _MAX_MATERIAL_CHARS

    material = _format_material(extractions)
    if not material.strip():
        yield {"type": "stage", "stage": "reduce"}
        yield {"type": "done", "result": {"answer": "", "documents": list(docs), "n_items": 0}}
        return

    if len(material) <= _MAX_MATERIAL_CHARS:
        # Cas courant (petit corpus) : un unique REDUCE, comportement inchangé.
        yield {"type": "stage", "stage": "reduce"}
        from core.prompt_registry import get_prompt
        answer = llm(get_prompt("synthesize.reduce", _REDUCE_PROMPT).format(aspect=aspect, material=material,
                                           output_contract=output_contract))
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
            from core.prompt_registry import get_prompt
            partial = llm(get_prompt("synthesize.reduce", _REDUCE_PROMPT).format(aspect=aspect, material=batch_material,
                                                output_contract=output_contract))
            partials.append(partial)
        merged_material = "\n\n".join(partials)
        answer = llm(get_prompt("synthesize.merge", _REDUCE_MERGE_PROMPT).format(aspect=aspect, material=merged_material,
                                                  output_contract=output_contract))

    coverage = {"checked": False, "missing": [], "repair_attempts": 0}
    from env_config import COVERAGE_REPAIR_ENABLED
    coverage_enabled = COVERAGE_REPAIR_ENABLED or force_coverage
    if contract and coverage_enabled:
        checker = coverage_check or _judge_coverage
        missing = checker(answer, contract["dimensions"])
        coverage = {"checked": True, "missing": list(missing), "repair_attempts": 0}
        yield {"type": "stage", "stage": "coverage", "missing": list(missing)}
        if missing:
            yield {"type": "stage", "stage": "repair", "missing": list(missing),
                   "attempt": 1, "max_attempts": 1}
            repair_aspect = ("COMPLÉMENT CIBLÉ. Extrais uniquement les axes manquants : " +
                             "; ".join(missing) + ". Format : AXE | CATÉGORIE | "
                             "ÉLÉMENT | CARACTÉRISATION | OBJECTIF_LIÉ.")
            def _safe_repair(d):
                try:
                    return extract(d, repair_aspect)
                except Exception as e:
                    logger.warning("[synthesize] réparation MAP échec sur %s : %s", d, e)
                    return {"document": d, "items": [], "raw": ""}
            with ThreadPoolExecutor(max_workers=workers) as pool:
                repair_extractions = list(pool.map(_safe_repair, docs))
            repair_material = _format_material(repair_extractions)
            if repair_material.strip():
                from core.prompt_registry import get_prompt
                answer = llm(get_prompt("synthesize.repair", _REPAIR_PROMPT).format(
                    output_contract=output_contract, missing="; ".join(missing),
                    answer=answer, material=repair_material))
                extra_items = sum(len(e.get("items") or []) for e in repair_extractions if e)
                total_items += extra_items
                quality_repair = [e for e in repair_extractions if e and e.get("items")]
                extractions.extend(quality_repair)
            remaining = checker(answer, contract["dimensions"])
            coverage = {"checked": True, "missing": list(remaining),
                        "repair_attempts": 1}
            yield {"type": "stage", "stage": "coverage",
                   "missing": list(remaining), "final": True}

    if contract and not coverage_enabled:
        coverage = {"checked": False, "missing": [], "repair_attempts": 0,
                    "disabled_reason": "configuration"}
        yield {"type": "stage", "stage": "coverage", "disabled": True}

    yield {"type": "token", "text": answer}
    quality_chunks = [
        {"doc": "\n".join(ex.get("items") or []),
         "meta": {"source": ex.get("document", ""),
                  "chunk_type": "synthesis_evidence"}}
        for ex in extractions if ex and ex.get("items")
    ]
    yield {"type": "done", "result": {"answer": answer, "documents": list(docs),
                                       "n_items": total_items, "chunks": quality_chunks,
                                       "coverage": coverage,
                                       "document_coverage": document_coverage}}
