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
