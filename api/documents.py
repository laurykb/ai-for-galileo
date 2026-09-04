"""Documents & ingestion : sources indexées, upload + file d'ingestion,
exploration des chunks, résumé/markdown d'un document, purge du corpus."""
from __future__ import annotations

import re
import time
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from env_config import MONGO_DB
from core import ingest_queue
from api.common import _chunks_col, read_upload_limited
from utils.mongo import get_client, get_db

router = APIRouter()

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_UPLOAD_TOTAL_BYTES = 500 * 1024 * 1024
MAX_UPLOAD_FILES = 20


def _document_artifacts(source_name: str) -> list[Path]:
    """Liste les fichiers locaux appartenant à une source indexée."""
    safe = Path(source_name).name
    stem = Path(safe).stem
    if stem.endswith("-clean"):
        stem = stem.removesuffix("-clean")

    candidates = {
        ingest_queue.DOCS_OUT / safe,
        ingest_queue.DOCS_OUT / f"{stem}.md",
        ingest_queue.DOCS_OUT.parent / "out_clean_md" / safe,
        ingest_queue.DOCS_OUT.parent / "out_clean_md" / f"{stem}-clean.md",
    }
    candidates.update(
        ingest_queue.DOCS_PDF / f"{stem}.{suffix}"
        for suffix in ingest_queue.UPLOAD_TYPES
        if suffix != "md"
    )
    return sorted(candidates)


def _candidate_source_names(upload_name: str) -> set[str]:
    """Noms que la conversion peut enregistrer pour un fichier déposé."""
    safe = Path(upload_name).name
    stem = Path(safe).stem
    if safe.lower().endswith(".md"):
        return {safe}
    return {f"{stem}.md", f"{stem}-clean.md"}


def _stale_upload_can_be_replaced(name: str, target: Path) -> bool:
    """Vrai si `target` est un résidu local sans job ni index associé.

    En cas d'indisponibilité d'un index, on échoue de manière conservative :
    un fichier potentiellement utile ne doit jamais être écrasé à l'aveugle.
    """
    resolved = target.resolve()
    for job in ingest_queue.snapshot():
        if job.get("status") in {"queued", "running"}:
            try:
                if Path(str(job.get("path", ""))).resolve() == resolved:
                    return False
            except OSError:
                return False

    sources = sorted(_candidate_source_names(name))
    try:
        db = get_client()[MONGO_DB]
        if db["chunks"].count_documents({"source": {"$in": sources}}, limit=1):
            return False
        if db["bm25_indexes"].count_documents({"source_doc": {"$in": sources}}, limit=1):
            return False
        from retrieval.vector_store import get_vector_store
        vector_counts = get_vector_store().source_counts()
        if vector_counts is not None and any(vector_counts.get(source, 0) for source in sources):
            return False
    except Exception as exc:
        raise HTTPException(
            503,
            "Impossible de vérifier si ce fichier résiduel est indexé. "
            "Vérifiez MongoDB et Chroma puis réessayez.",
        ) from exc
    return True


def _remove_stale_upload(name: str) -> None:
    """Supprime les artefacts orphelins du même document avant réinjection."""
    paths: set[Path] = set()
    for source in _candidate_source_names(name):
        paths.update(_document_artifacts(source))
    for path in paths:
        path.unlink(missing_ok=True)


def _local_orphan_files(indexed_sources: set[str]) -> list[str]:
    """Fichiers déposés présents sur disque mais absents de MongoDB."""
    active_paths = set()
    for job in ingest_queue.snapshot():
        if job.get("status") in {"queued", "running"}:
            try:
                active_paths.add(Path(str(job.get("path", ""))).resolve())
            except OSError:
                pass

    files: list[Path] = []
    if ingest_queue.DOCS_PDF.is_dir():
        files.extend(path for path in ingest_queue.DOCS_PDF.iterdir() if path.is_file())
    if ingest_queue.DOCS_OUT.is_dir():
        for path in ingest_queue.DOCS_OUT.glob("*.md"):
            # Un Markdown brut issu d'un PDF est déjà représenté par l'original.
            base_stem = path.stem.removesuffix("-clean")
            if any((ingest_queue.DOCS_PDF / f"{base_stem}.{suffix}").exists()
                   for suffix in ingest_queue.UPLOAD_TYPES if suffix != "md"):
                continue
            # Un markdown nettoyé est un dérivé du markdown brut correspondant.
            if path.stem.endswith("-clean") and (ingest_queue.DOCS_OUT / f"{base_stem}.md").exists():
                continue
            files.append(path)

    return sorted({
        path.name for path in files
        if path.resolve() not in active_paths
        and _candidate_source_names(path.name).isdisjoint(indexed_sources)
    })


def _orphan_path(name: str) -> Path:
    """Résout un nom orphelin vers un fichier autorisé, sans traversée."""
    safe = Path(name).name
    if safe != name or safe in {"", ".", ".."}:
        raise HTTPException(400, "Nom de fichier invalide.")
    candidates = [ingest_queue.DOCS_PDF / safe, ingest_queue.DOCS_OUT / safe]
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        raise HTTPException(404, "Fichier résiduel introuvable.")
    if not _stale_upload_can_be_replaced(safe, path):
        raise HTTPException(409, "Ce fichier est déjà indexé ou en cours d’ingestion.")
    return path


@router.get("/api/sources")
def sources() -> dict:
    """Documents ingérés : nom + nombre de chunks (depuis Mongo).

    Renvoie `available: false` si Mongo est injoignable — le front affiche
    alors un état dégradé au lieu d'une erreur.
    """
    try:
        rows = list(_chunks_col().aggregate([
            {"$match": {"source": {"$ne": None}}},
            {"$group": {"_id": "$source", "chunks": {"$sum": 1},
                    "accepted": {"$sum": {"$cond": [{"$eq": [{"$ifNull": ["$quality_status", "accepted"]}, "accepted"]}, 1, 0]}},
                    "degraded": {"$sum": {"$cond": [{"$eq": ["$quality_status", "degraded"]}, 1, 0]}},
                    "quarantined": {"$sum": {"$cond": [{"$eq": ["$quality_status", "quarantined"]}, 1, 0]}}}},
            {"$sort": {"_id": 1}},
        ]))
    except Exception:
        return {"available": False, "sources": []}
    from core.index_consistency import rag_index_consistency
    consistency = rag_index_consistency()
    by_name = {row["name"]: row for row in consistency.get("sources", [])}
    indexed_sources = {str(r["_id"]) for r in rows}
    return {
        "available": True,
        "in_sync": consistency.get("in_sync", False),
        "consistency_available": consistency.get("available", False),
        "orphans": consistency.get("orphans", []),
        "local_orphans": _local_orphan_files(indexed_sources),
        "sources": [{
            "name": r["_id"], "chunks": r["chunks"],
            "quality": {"accepted": r.get("accepted", 0),
                        "degraded": r.get("degraded", 0),
                        "quarantined": r.get("quarantined", 0)},
            "index": by_name.get(r["_id"]),
        } for r in rows],
    }


@router.get("/api/index/consistency")
def index_consistency() -> dict:
    """Compare les sources présentes dans MongoDB, BM25 et ChromaDB."""
    from core.index_consistency import rag_index_consistency
    return rag_index_consistency()


@router.post("/api/documents/{name}/summary")
def document_summary(name: str) -> dict:
    """Résumé global d'un document (réutilise les résumés de section RAPTOR)."""
    from core.summarize import summarize_document
    return summarize_document(name)


@router.get("/api/documents/{name}/markdown")
def document_markdown(name: str) -> dict:
    """Texte markdown source du document (visionneuse page blanche).
    Cherche dans docs/out ET dans le dossier des markdown nettoyés (les PDF
    convertis y vivent sous <nom>-clean.md)."""
    safe = name.replace("/", "_").replace("\\", "_")
    candidates = [ingest_queue.DOCS_OUT / safe,
                  ingest_queue.DOCS_OUT.parent / "out_clean_md" / safe]
    for path in candidates:
        if path.exists():
            return {"name": safe, "markdown": path.read_text(encoding="utf-8")}
    raise HTTPException(404, "Markdown source introuvable.")

@router.get("/api/ingest/defaults")
def ingest_defaults() -> dict:
    """Options d'ingestion par défaut (.env) + types de fichiers acceptés."""
    return {"params": ingest_queue.default_params(),
            "upload_types": list(ingest_queue.UPLOAD_TYPES)}


@router.post("/api/documents/orphans/{name}/reindex")
def reindex_orphan(name: str) -> dict:
    """Remet un fichier local orphelin dans la file d'ingestion."""
    path = _orphan_path(name)
    added = ingest_queue.enqueue(
        [{"name": path.name, "path": str(path)}], ingest_queue.default_params()
    )
    return {"added": added, "name": path.name}


@router.delete("/api/documents/orphans/{name}")
def delete_orphan(name: str) -> dict:
    """Supprime un fichier local et ses dérivés uniquement s'ils sont orphelins."""
    path = _orphan_path(name)
    _remove_stale_upload(path.name)
    return {"deleted": True, "name": path.name}


@router.post("/api/documents")
async def upload_documents(
    files: list[UploadFile] = File(...),
    nkw: int = Form(...),
    nq: int = Form(...),
    mode: str = Form(...),
    raptor: bool = Form(...),
    enh_model: str = Form(""),
) -> dict:
    """Dépose un LOT de documents et le met en file d'ingestion séquentielle.
    Les options sont choisies AU MOMENT de l'upload (règle produit) et
    partagées par le lot."""
    if not files or len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(
            413,
            f"Un lot doit contenir au maximum {MAX_UPLOAD_FILES} fichiers. "
            "Fractionnez le dépôt en plusieurs lots.",
        )
    if not 0 <= nkw <= 10 or not 0 <= nq <= 10:
        raise HTTPException(422, "nkw et nq doivent être compris entre 0 et 10.")
    if len(enh_model.strip()) > 100:
        raise HTTPException(422, "Nom de modèle trop long.")

    prepared = []
    seen = set()
    for up in files:
        name = (up.filename or "document").replace("/", "_").replace("\\", "_")
        if name in {"", ".", ".."} or name in seen:
            raise HTTPException(400, f"Nom de fichier invalide ou dupliqué : {name}")
        seen.add(name)
        if name.split(".")[-1].lower() not in ingest_queue.UPLOAD_TYPES:
            raise HTTPException(400, f"Type non accepté : {name}")
        target = (ingest_queue.DOCS_OUT / name if name.lower().endswith(".md")
                  else ingest_queue.DOCS_PDF / name)
        if target.exists():
            if not _stale_upload_can_be_replaced(name, target):
                raise HTTPException(
                    409,
                    f"Le document est déjà indexé ou en cours d’ingestion : {name}",
                )
            _remove_stale_upload(name)
        prepared.append((up, name, target))

    ingest_queue.DOCS_OUT.mkdir(parents=True, exist_ok=True)
    ingest_queue.DOCS_PDF.mkdir(parents=True, exist_ok=True)
    items = []
    written = []
    total_bytes = 0
    try:
        for up, name, target in prepared:
            content = await read_upload_limited(up, MAX_UPLOAD_BYTES)
            total_bytes += len(content)
            if total_bytes > MAX_UPLOAD_TOTAL_BYTES:
                raise HTTPException(
                    413,
                    "Le lot dépasse 500 Mo. Fractionnez le dépôt en plusieurs lots.",
                )
            target.write_bytes(content)
            written.append(target)
            items.append({"name": name, "path": str(target)})
    except Exception:
        for target in written:
            target.unlink(missing_ok=True)
        raise
    params = {"nkw": nkw, "nq": nq,
              "mode": mode if mode in ("technical", "naive") else "technical",
              "raptor": raptor, "enh_model": enh_model.strip()}
    return {"added": ingest_queue.enqueue(items, params)}


@router.get("/api/ingest/status")
def ingest_status() -> dict:
    """File d'ingestion : une entrée par document, dans l'ordre de lancement."""
    jobs = []
    for j in ingest_queue.snapshot():
        res = j.get("result") or {}
        if j["status"] == "running" and j["t0"]:
            elapsed = int(time.time() - j["t0"])
        elif j["t_end"] and j["t0"]:
            elapsed = int(j["t_end"] - j["t0"])
        else:
            elapsed = None
        jobs.append({
            "id": j["id"], "name": j["name"], "status": j["status"],
            "pct": j["pct"], "step": j["step"],
            "elapsed": elapsed,
            # Nom de source RÉEL en base (un PDF devient <nom>-clean.md) :
            # c'est lui que l'UI doit cibler pour interroger le document.
            "source_name": j.get("source_name"),
            "num_chunks": res.get("num_chunks"),
            "quality": res.get("quality"),
            "message": res.get("message"),
        })
    return {"active": ingest_queue.active(), "jobs": jobs}


@router.post("/api/ingest/clear")
def ingest_clear() -> dict:
    ingest_queue.clear_finished()
    return {"ok": True}


@router.delete("/api/documents/{name}")
def delete_document(name: str) -> dict:
    """Supprime index, fichier déposé et Markdown dérivés pour permettre une réingestion propre."""
    safe = Path(name).name
    if safe != name or safe in {"", ".", ".."}:
        raise HTTPException(400, "Nom de document invalide.")
    for job in ingest_queue.snapshot():
        if job["status"] in ("queued", "running") and job.get("source_name") == safe:
            raise HTTPException(409, "Suppression impossible : ingestion du document en cours.")
    try:
        client = get_client()
        n = client[MONGO_DB]["chunks"].delete_many({"source": name}).deleted_count
        client[MONGO_DB]["bm25_indexes"].delete_many({"source_doc": name})
    except Exception as e:
        raise HTTPException(503, f"Mongo injoignable : {e}")
    try:
        from retrieval.vector_store import get_vector_store
        get_vector_store().delete_source(name)
    except Exception:
        pass  # Chroma indisponible : les vecteurs orphelins partiront à la réingestion
    deleted_files = []
    for artifact in _document_artifacts(safe):
        try:
            if artifact.is_file():
                artifact.unlink()
                deleted_files.append(str(artifact.relative_to(ingest_queue.DOCS_OUT.parent)))
        except OSError as exc:
            raise HTTPException(500, f"Index supprimé, mais fichier local non supprimable : {artifact.name} ({exc})") from exc
    try:
        from core.ask import clear_retrieval_caches
        clear_retrieval_caches()
    except Exception:
        pass
    return {"deleted": n, "deleted_files": deleted_files}


@router.post("/api/corpus/reset")
def corpus_reset() -> dict:
    """Vide l'index documentaire local (Chroma + chunks/BM25/graphe Mongo)
    SANS toucher aux sessions ni aux traces. Même geste que le Streamlit."""
    report = []
    try:
        db = get_db()
        for cn in ("chunks", "bm25_indexes", "entity_graph"):
            try:
                report.append(f"{cn}: -{db[cn].delete_many({}).deleted_count}")
            except Exception as e:
                report.append(f"{cn}: erreur ({e})")
    except Exception as e:
        raise HTTPException(503, f"Mongo injoignable : {e}")
    try:
        from retrieval.vector_store import get_vector_store
        get_vector_store().reset()
        report.append("vecteurs réinitialisés")
    except Exception as e:
        report.append(f"vecteurs: erreur ({e})")
    try:
        from core.ask import clear_retrieval_caches
        clear_retrieval_caches()
    except Exception:
        pass
    return {"ok": True, "report": " - ".join(report)}


@router.get("/api/documents/{name}/chunks")
def document_chunks(name: str, search: str = "", chunk_type: str = "tous",
                    limit: int = 200) -> dict:
    """Exploration d'un document : ses passages indexés, filtrables (boîte de
    verre de l'indexation — ce que la base contient réellement)."""
    q: dict = {"source": name}
    if chunk_type == "texte":
        q["chunk_type"] = {"$nin": ["summary", "table", "figure", "mixed"]}
    elif chunk_type == "resumes":
        q["chunk_type"] = "summary"
    elif chunk_type == "tabfig":
        q["chunk_type"] = {"$in": ["table", "figure", "mixed"]}
    if search:
        q["content"] = {"$regex": re.escape(search), "$options": "i"}
    try:
        rows = list(_chunks_col().find(q).sort([("section_idx", 1), ("chunk_idx", 1)])
                    .limit(max(1, min(limit, 500))))
    except Exception as e:
        raise HTTPException(503, f"Mongo injoignable : {e}")
    chunks = [{
        "content": r.get("content", ""),
        "heading": r.get("heading"), "breadcrumb": r.get("breadcrumb"),
        "section_idx": r.get("section_idx"), "page_number": r.get("page_number"),
        "chunk_type": r.get("chunk_type"),
        "quality_status": r.get("quality_status", "accepted"),
        "quality_reasons": r.get("quality_reasons", []),
        "content_provenance": r.get("content_provenance", "raw"),
        "keywords_str": r.get("keywords_str"), "questions_str": r.get("questions_str"),
        "entities_str": r.get("entities_str"),
    } for r in rows]
    return {"total": len(chunks), "chunks": chunks}
