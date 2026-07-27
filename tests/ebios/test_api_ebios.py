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
