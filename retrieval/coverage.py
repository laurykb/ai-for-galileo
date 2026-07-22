"""Plancher de couverture par document : garantit qu'un document pertinent, même
petit, remonte dans le contexte final quand le périmètre est « Tous ». Appliqué
APRÈS rerank, sur des chunks triés par ce_score décroissant."""
from __future__ import annotations


def _src(item: dict):
    return (item.get("meta") or {}).get("source")


def _ce(item: dict) -> float:
    return item.get("ce_score", 0.0)


def elastic_candidate_pool(n_docs, pool_min, pool_max, per_doc):
    """Taille élastique du pool de candidats : petit corpus -> rapide (proche de
    pool_min), gros corpus -> plafonné (pool_max). Dérivée gratuitement du nombre
    de documents du corpus (aucune requête supplémentaire)."""
    return max(pool_min, min(pool_min + per_doc * max(0, n_docs), pool_max))


def apply_coverage_floor(items, base_k, per_doc_floor=1, max_chunks=24, floor_min_ce=None):
    """Complète le top `base_k` global pour garantir `per_doc_floor` chunks par
    source éligible (ce_score >= floor_min_ce si fourni), sans jamais dépasser
    `max_chunks` ni écarter un chunk planché. `items` supposé trié ce_score desc."""
    if not items:
        return items

    selected = list(items[:base_k])
    picked = {id(x) for x in selected}
    per_source = {}
    for it in selected:
        per_source[_src(it)] = per_source.get(_src(it), 0) + 1

    floored = []
    for it in items:
        s = _src(it)
        if per_source.get(s, 0) >= per_doc_floor:
            continue
        if floor_min_ce is not None and _ce(it) < floor_min_ce:
            continue
        if id(it) in picked:
            continue
        floored.append(it)
        picked.add(id(it))
        per_source[s] = per_source.get(s, 0) + 1

    result = selected + floored
    if len(result) > max_chunks:
        floored_ids = {id(x) for x in floored}
        protected = [x for x in result if id(x) in floored_ids]
        others = [x for x in result if id(x) not in floored_ids]
        others.sort(key=_ce, reverse=True)
        keep_others = others[: max(0, max_chunks - len(protected))]
        result = keep_others + protected

    result.sort(key=_ce, reverse=True)
    return result
