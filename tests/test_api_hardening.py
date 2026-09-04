"""Régressions des garde-fous API ajoutés pendant l'audit d'export."""
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.main import app
from api.system import EnvUpdates, ModelAction, save_settings, set_generation_model


def test_upload_rejects_out_of_range_enrichment_before_writing(tmp_path, monkeypatch):
    from core import ingest_queue

    docs = tmp_path / "PDF"
    out = tmp_path / "out"
    monkeypatch.setattr(ingest_queue, "DOCS_PDF", docs)
    monkeypatch.setattr(ingest_queue, "DOCS_OUT", out)
    response = TestClient(app).post(
        "/api/documents",
        files={"files": ("sample.md", b"content", "text/markdown")},
        data={"nkw": "11", "nq": "0", "mode": "technical", "raptor": "false"},
    )
    assert response.status_code == 422
    assert not (out / "sample.md").exists()


def test_upload_replaces_unindexed_orphan_file(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from api import documents
    from core import ingest_queue
    import retrieval.vector_store as vector_store

    docs = tmp_path / "PDF"
    out = tmp_path / "out"
    out.mkdir()
    (out / "sample.md").write_text("original", encoding="utf-8")
    monkeypatch.setattr(ingest_queue, "DOCS_PDF", docs)
    monkeypatch.setattr(ingest_queue, "DOCS_OUT", out)
    monkeypatch.setattr(ingest_queue, "snapshot", lambda: [])
    monkeypatch.setattr(ingest_queue, "enqueue", lambda items, _params: len(items))

    class Collection:
        def count_documents(self, _query, **_kwargs):
            return 0

    monkeypatch.setattr(documents, "get_client", lambda: {documents.MONGO_DB: {
        "chunks": Collection(), "bm25_indexes": Collection(),
    }})
    monkeypatch.setattr(vector_store, "get_vector_store",
                        lambda: SimpleNamespace(source_counts=lambda: {}))
    response = TestClient(app).post(
        "/api/documents",
        files={"files": ("sample.md", b"replacement", "text/markdown")},
        data={"nkw": "1", "nq": "1", "mode": "technical", "raptor": "false"},
    )
    assert response.status_code == 200
    assert (out / "sample.md").read_text(encoding="utf-8") == "replacement"


def test_upload_keeps_indexed_duplicate(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from api import documents
    from core import ingest_queue
    import retrieval.vector_store as vector_store

    docs = tmp_path / "PDF"
    out = tmp_path / "out"
    out.mkdir()
    (out / "sample.md").write_text("original", encoding="utf-8")
    monkeypatch.setattr(ingest_queue, "DOCS_PDF", docs)
    monkeypatch.setattr(ingest_queue, "DOCS_OUT", out)
    monkeypatch.setattr(ingest_queue, "snapshot", lambda: [])

    class Collection:
        def __init__(self, count): self.count = count
        def count_documents(self, _query, **_kwargs): return self.count

    monkeypatch.setattr(documents, "get_client", lambda: {documents.MONGO_DB: {
        "chunks": Collection(1), "bm25_indexes": Collection(0),
    }})
    monkeypatch.setattr(vector_store, "get_vector_store",
                        lambda: SimpleNamespace(source_counts=lambda: {}))
    response = TestClient(app).post(
        "/api/documents",
        files={"files": ("sample.md", b"replacement", "text/markdown")},
        data={"nkw": "1", "nq": "1", "mode": "technical", "raptor": "false"},
    )
    assert response.status_code == 409
    assert "déjà indexé" in response.json()["detail"]
    assert (out / "sample.md").read_text(encoding="utf-8") == "original"


def test_delete_document_removes_upload_and_conversion_artifacts(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from api import documents
    from core import ingest_queue
    import retrieval.vector_store as vector_store

    pdf = tmp_path / "PDF"
    out = tmp_path / "out"
    clean = tmp_path / "out_clean_md"
    for directory in (pdf, out, clean):
        directory.mkdir()
    for path in (pdf / "sample.pdf", out / "sample.md", clean / "sample-clean.md"):
        path.write_text("content", encoding="utf-8")
    monkeypatch.setattr(ingest_queue, "DOCS_PDF", pdf)
    monkeypatch.setattr(ingest_queue, "DOCS_OUT", out)
    monkeypatch.setattr(ingest_queue, "snapshot", lambda: [])

    class Collection:
        def delete_many(self, _query):
            return SimpleNamespace(deleted_count=1)

    monkeypatch.setattr(documents, "get_client", lambda: {documents.MONGO_DB: {
        "chunks": Collection(), "bm25_indexes": Collection(),
    }})
    monkeypatch.setattr(vector_store, "get_vector_store",
                        lambda: SimpleNamespace(delete_source=lambda _name: None))

    response = TestClient(app).delete("/api/documents/sample-clean.md")
    assert response.status_code == 200
    assert response.json()["deleted_files"] == [
        "PDF/sample.pdf", "out/sample.md", "out_clean_md/sample-clean.md",
    ]
    assert not any(path.exists() for path in (pdf / "sample.pdf", out / "sample.md",
                                               clean / "sample-clean.md"))


def test_deleted_document_can_be_uploaded_again(tmp_path, monkeypatch):
    from core import ingest_queue

    pdf = tmp_path / "PDF"
    out = tmp_path / "out"
    pdf.mkdir()
    out.mkdir()
    monkeypatch.setattr(ingest_queue, "DOCS_PDF", pdf)
    monkeypatch.setattr(ingest_queue, "DOCS_OUT", out)
    monkeypatch.setattr(ingest_queue, "enqueue", lambda items, _params: len(items))
    response = TestClient(app).post(
        "/api/documents",
        files={"files": ("sample.pdf", b"new content", "application/pdf")},
        data={"nkw": "1", "nq": "1", "mode": "technical", "raptor": "false"},
    )
    assert response.status_code == 200
    assert response.json() == {"added": 1}
    assert (pdf / "sample.pdf").read_bytes() == b"new content"


def test_orphan_can_be_reindexed_or_deleted(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from api import documents
    from core import ingest_queue
    import retrieval.vector_store as vector_store

    pdf = tmp_path / "PDF"
    out = tmp_path / "out"
    pdf.mkdir(); out.mkdir()
    orphan = pdf / "orphan.pdf"
    orphan.write_bytes(b"pdf")
    monkeypatch.setattr(ingest_queue, "DOCS_PDF", pdf)
    monkeypatch.setattr(ingest_queue, "DOCS_OUT", out)
    monkeypatch.setattr(ingest_queue, "snapshot", lambda: [])
    queued = []
    monkeypatch.setattr(ingest_queue, "enqueue", lambda items, params: queued.extend(items) or len(items))
    monkeypatch.setattr(ingest_queue, "default_params", lambda: {"nkw": 0, "nq": 0, "mode": "technical", "raptor": False, "enh_model": ""})

    class Collection:
        def count_documents(self, _query, **_kwargs): return 0

    monkeypatch.setattr(documents, "get_client", lambda: {documents.MONGO_DB: {
        "chunks": Collection(), "bm25_indexes": Collection(),
    }})
    monkeypatch.setattr(vector_store, "get_vector_store",
                        lambda: SimpleNamespace(source_counts=lambda: {}))

    client = TestClient(app)
    response = client.post("/api/documents/orphans/orphan.pdf/reindex")
    assert response.status_code == 200
    assert queued[0]["name"] == "orphan.pdf"
    assert orphan.exists()

    queued.clear()
    response = client.delete("/api/documents/orphans/orphan.pdf")
    assert response.status_code == 200
    assert not orphan.exists()


def test_failed_ingestion_cleanup_removes_source_and_derived_files(tmp_path, monkeypatch):
    from core import ingest_queue
    import retrieval.vector_store as vector_store
    import utils.mongo as mongo

    pdf = tmp_path / "PDF"
    out = tmp_path / "out"
    clean = tmp_path / "out_clean_md"
    for directory in (pdf, out, clean):
        directory.mkdir()
    original = pdf / "broken.pdf"
    raw = out / "broken.md"
    cleaned = clean / "broken-clean.md"
    for path in (original, raw, cleaned):
        path.write_text("partial", encoding="utf-8")
    monkeypatch.setattr(ingest_queue, "DOCS_PDF", pdf)
    monkeypatch.setattr(ingest_queue, "DOCS_OUT", out)
    monkeypatch.setattr(mongo, "get_client", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(vector_store, "get_vector_store", lambda: (_ for _ in ()).throw(RuntimeError("offline")))

    ingest_queue._discard_failed_job({"path": str(original), "source_name": "broken-clean.md"})

    assert not original.exists()
    assert not raw.exists()
    assert not cleaned.exists()


def test_upload_rejects_too_many_documents_explicitly():
    files = [("files", (f"doc-{index}.md", b"x", "text/markdown")) for index in range(21)]
    response = TestClient(app).post(
        "/api/documents", files=files,
        data={"nkw": "1", "nq": "1", "mode": "technical", "raptor": "false"},
    )
    assert response.status_code == 413
    assert "20 fichiers" in response.json()["detail"]
    assert "Fractionnez" in response.json()["detail"]


def test_upload_total_limit_rolls_back_written_files(tmp_path, monkeypatch):
    from api import documents
    from core import ingest_queue

    docs = tmp_path / "PDF"
    out = tmp_path / "out"
    monkeypatch.setattr(ingest_queue, "DOCS_PDF", docs)
    monkeypatch.setattr(ingest_queue, "DOCS_OUT", out)
    monkeypatch.setattr(documents, "MAX_UPLOAD_TOTAL_BYTES", 5)
    response = TestClient(app).post(
        "/api/documents",
        files=[("files", ("a.md", b"abc", "text/markdown")),
               ("files", ("b.md", b"def", "text/markdown"))],
        data={"nkw": "1", "nq": "1", "mode": "technical", "raptor": "false"},
    )
    assert response.status_code == 413
    assert "Fractionnez" in response.json()["detail"]
    assert not (out / "a.md").exists()
    assert not (out / "b.md").exists()


def test_settings_reject_newline_injection():
    with pytest.raises(HTTPException) as exc:
        save_settings(EnvUpdates(updates={"GEN_MODEL": "safe\nMONGO_URI=attacker"}))
    assert exc.value.status_code == 422


def test_model_action_rejects_unknown_action():
    with pytest.raises(HTTPException) as exc:
        set_generation_model(ModelAction(model="llama3.2:3b", action="delete"))
    assert exc.value.status_code == 422


def test_model_action_propagates_ollama_http_error(monkeypatch):
    import requests

    class FailedResponse:
        def raise_for_status(self):
            raise requests.HTTPError("model missing")

    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: FailedResponse())
    with pytest.raises(HTTPException) as exc:
        set_generation_model(ModelAction(model="missing", action="unload"))
    assert exc.value.status_code == 502
