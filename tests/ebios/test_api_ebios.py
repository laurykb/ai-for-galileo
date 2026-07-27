import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.ebios as ebios_api
from core.ebios.repository import EbiosRepository
from tests.ebios.fixtures import make_study


@pytest.fixture()
def client(fake_db, monkeypatch):
    repo = EbiosRepository(db=fake_db)
    monkeypatch.setattr(ebios_api, "_repo", lambda: repo)
    app = FastAPI()
    app.include_router(ebios_api.router)
    return TestClient(app)


def _project(client, docs=("doc-a.md",)):
    r = client.post("/api/ebios/projects", json={"name": "Étude", "document_ids": list(docs)})
    assert r.status_code == 200
    return r.json()


def test_creation_et_listing(client):
    p = _project(client)
    assert client.get("/api/ebios/projects").json()["projects"][0]["project_id"] == p["project_id"]
    detail = client.get(f"/api/ebios/projects/{p['project_id']}").json()
    assert detail["latest"] is None and detail["project"]["status"] == "created"


def test_put_atelier_et_conflit(client):
    p = _project(client)
    r = client.put(f"/api/ebios/projects/{p['project_id']}/atelier-1",
                   json={"study": make_study(), "expected_version": 0, "author": "ingénieur"})
    assert r.status_code == 200 and r.json()["version"] == 1
    r2 = client.put(f"/api/ebios/projects/{p['project_id']}/atelier-1",
                    json={"study": make_study(), "expected_version": 0, "author": "ingénieur"})
    assert r2.status_code == 409


def test_get_expose_warnings_du_controle_borne(client):
    p = _project(client)
    data = make_study()
    data["atelier_1"]["valeurs_metier"][0]["preuves"] = []
    client.put(f"/api/ebios/projects/{p['project_id']}/atelier-1",
               json={"study": data, "expected_version": 0, "author": "ingénieur"})
    detail = client.get(f"/api/ebios/projects/{p['project_id']}").json()
    assert any("preuve" in w.lower() for w in detail["warnings"])


def test_get_conserve_les_warnings_du_pipeline(client):
    p = _project(client)
    data = make_study()
    pipeline_warning = ("ER « X » : gravité invalide « 9 » — ramenée à 1 (Mineure), "
                        "à corriger")
    data["warnings"] = [pipeline_warning]
    data["atelier_1"]["valeurs_metier"][0]["preuves"] = []
    client.put(f"/api/ebios/projects/{p['project_id']}/atelier-1",
               json={"study": data, "expected_version": 0, "author": "ingénieur"})
    detail = client.get(f"/api/ebios/projects/{p['project_id']}").json()
    assert pipeline_warning in detail["warnings"]
    assert any("preuve" in w.lower() for w in detail["warnings"])
    assert len(detail["warnings"]) == len(set(detail["warnings"]))


def test_validation_puis_export_xlsx(client, tmp_path):
    p = _project(client)
    client.put(f"/api/ebios/projects/{p['project_id']}/atelier-1",
               json={"study": make_study(), "expected_version": 0, "author": "ingénieur"})
    r = client.post(f"/api/ebios/projects/{p['project_id']}/atelier-1/validate",
                    json={"expected_version": 1, "validated_by": "Laury"})
    assert r.status_code == 200 and r.json()["version"] == 2
    r = client.post(f"/api/ebios/projects/{p['project_id']}/atelier-1/exports",
                    json={"format": "xlsx"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert "atelier1_v2" in r.headers["content-disposition"]
    assert r.content[:2] == b"PK"


def test_export_etude_54_vm_reussit(client):
    """Le classeur s'étend automatiquement : plus de refus de capacité côté API."""
    data = make_study()
    vm = data["atelier_1"]["valeurs_metier"][0]
    data["atelier_1"]["valeurs_metier"] = [dict(vm, id=f"vm-{i}") for i in range(1, 55)]
    p = _project(client)
    client.put(f"/api/ebios/projects/{p['project_id']}/atelier-1",
               json={"study": data, "expected_version": 0, "author": "ingénieur"})
    r = client.post(f"/api/ebios/projects/{p['project_id']}/atelier-1/validate",
                    json={"expected_version": 1, "validated_by": "Laury"})
    assert r.status_code == 200
    r = client.post(f"/api/ebios/projects/{p['project_id']}/atelier-1/exports",
                    json={"format": "xlsx"})
    assert r.status_code == 200
    assert r.content[:2] == b"PK"


def test_export_refuse_avant_validation(client):
    p = _project(client)
    client.put(f"/api/ebios/projects/{p['project_id']}/atelier-1",
               json={"study": make_study(), "expected_version": 0, "author": "ingénieur"})
    r = client.post(f"/api/ebios/projects/{p['project_id']}/atelier-1/exports",
                    json={"format": "xlsx"})
    assert r.status_code == 409


def test_docx_pdf_explicitement_indisponibles(client):
    p = _project(client)
    client.put(f"/api/ebios/projects/{p['project_id']}/atelier-1",
               json={"study": make_study(), "expected_version": 0, "author": "ingénieur"})
    client.post(f"/api/ebios/projects/{p['project_id']}/atelier-1/validate",
                json={"expected_version": 1, "validated_by": "Laury"})
    for fmt in ("docx", "pdf"):
        r = client.post(f"/api/ebios/projects/{p['project_id']}/atelier-1/exports",
                        json={"format": fmt})
        assert r.status_code == 409 and "indisponible" in r.json()["detail"].lower()


def test_projet_inconnu_404(client):
    assert client.get("/api/ebios/projects/nope").status_code == 404


def _events(text):
    import json as _json
    out = []
    for line in text.splitlines():
        if line.startswith("data: "):
            out.append(_json.loads(line[len("data: "):]))
    return out


def test_generate_sse_stream_et_persistance(client, monkeypatch):
    from tests.ebios.fixtures import make_study as _mk

    def fake_generate(project_id, document_ids, progress=None, **kw):
        progress("extraction", {"document": document_ids[0], "domaine": "vm"})
        progress("consolidation", {})
        progress("validation", {})
        return _mk(project_id=project_id)

    monkeypatch.setattr(ebios_api, "_generate", lambda: fake_generate)
    p = _project(client)
    r = client.post(f"/api/ebios/projects/{p['project_id']}/atelier-1/generate")
    assert r.status_code == 200
    events = _events(r.text)
    types = [e["type"] for e in events]
    assert types.count("stage") == 3 and "draft" in types and types[-1] == "done"
    draft = next(e for e in events if e["type"] == "draft")
    assert draft["version"] == 1
    detail = client.get(f"/api/ebios/projects/{p['project_id']}").json()
    assert detail["project"]["status"] == "draft_generated"
    assert detail["latest"]["origin"] == "generation"


def test_generate_sans_document_erreur(client, monkeypatch):
    monkeypatch.setattr(ebios_api, "_generate", lambda: (lambda *a, **k: {}))
    p = _project(client, docs=())
    r = client.post(f"/api/ebios/projects/{p['project_id']}/atelier-1/generate")
    events = _events(r.text)
    assert events[-1]["type"] == "error"


def test_put_documents_puis_regeneration(client, monkeypatch):
    received = {}

    def fake_generate(project_id, document_ids, progress=None, **kw):
        received["docs"] = list(document_ids)
        progress("extraction", {})
        from tests.ebios.fixtures import make_study as _mk
        return _mk(project_id=project_id)

    monkeypatch.setattr(ebios_api, "_generate", lambda: fake_generate)
    p = _project(client, docs=("doc-a.md",))
    r = client.put(f"/api/ebios/projects/{p['project_id']}/documents",
                   json={"document_ids": ["doc-a.md", "doc-b.md"]})
    assert r.status_code == 200 and r.json()["document_ids"] == ["doc-a.md", "doc-b.md"]
    detail = client.get(f"/api/ebios/projects/{p['project_id']}").json()
    assert detail["project"]["document_ids"] == ["doc-a.md", "doc-b.md"]
    client.post(f"/api/ebios/projects/{p['project_id']}/atelier-1/generate")
    assert received["docs"] == ["doc-a.md", "doc-b.md"]


def test_put_documents_projet_inconnu_404(client):
    r = client.put("/api/ebios/projects/nope/documents", json={"document_ids": []})
    assert r.status_code == 404


def test_cors_expose_content_disposition():
    from starlette.middleware.cors import CORSMiddleware
    from api.main import app as main_app
    m = next(m for m in main_app.user_middleware if m.cls is CORSMiddleware)
    exposed = m.kwargs.get("expose_headers") or []
    assert "Content-Disposition" in exposed
