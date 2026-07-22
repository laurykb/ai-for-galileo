"""Utilitaires corpus-large : liste des documents indexés (données Mongo `chunks`)."""
from __future__ import annotations


def list_indexed_sources(db=None) -> list[str]:
    """Noms de documents distincts du corpus, triés. `db` injectable (tests)."""
    if db is None:
        from utils.mongo import get_db
        db = get_db()
    rows = db["chunks"].aggregate([
        {"$match": {"source": {"$ne": None}}},
        {"$group": {"_id": "$source"}},
    ])
    return sorted(r["_id"] for r in rows)


def prefilter_documents(aspect, retrieve=None, all_sources=None, topn=None, max_docs=None):
    """Documents pertinents pour `aspect` : ceux ayant un chunk dans le top-`topn`
    du retrieval (périmètre = tous), en ordre de 1re apparition (pertinence),
    plafonné à `max_docs`. Repli sur tout le corpus si le pré-filtre est vide."""
    from env_config import CORPUS_PREFILTER_TOPN, CORPUS_MAX_DOCS
    topn = topn or CORPUS_PREFILTER_TOPN
    max_docs = max_docs or CORPUS_MAX_DOCS
    if retrieve is None:
        from core.ask import retrieve_only as retrieve
    if all_sources is None:
        all_sources = list_indexed_sources()

    _q, chunks = retrieve(aspect, source_filter=None)
    ordered = []
    seen = set()
    for c in (chunks or [])[:topn]:
        src = (c.get("meta") or {}).get("source")
        if src and src not in seen:
            seen.add(src)
            ordered.append(src)
    if not ordered:                       # aspect trop générique -> tout le corpus
        ordered = list(all_sources)
    return ordered[:max_docs]
