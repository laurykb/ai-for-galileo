"""Le routeur envoie une question d'agrégation vers le pipeline de synthèse corpus.
On patche synthesize_corpus pour rester offline."""
import json
import api.rag as rag
from fastapi.testclient import TestClient
from fastapi import FastAPI


def _events(resp_text):
    out = []
    for line in resp_text.splitlines():
        if line.startswith("data:"):
            out.append(json.loads(line[len("data:"):].strip()))
    return out


def test_aggregate_question_routes_to_synth(monkeypatch):
    def fake_pipeline(question, aspect=None, **kw):
        yield {"type": "stage", "stage": "prefilter", "documents": ["a.md", "b.md"]}
        yield {"type": "map", "index": 1, "total": 2, "document": "a.md", "n_items": 2}
        yield {"type": "map", "index": 2, "total": 2, "document": "b.md", "n_items": 1}
        yield {"type": "stage", "stage": "reduce"}
        yield {"type": "token", "text": "Synthèse..."}
        yield {"type": "done", "result": {"answer": "Synthèse...",
                                          "documents": ["a.md", "b.md"], "n_items": 3}}
    monkeypatch.setattr(rag, "_synthesize_corpus", fake_pipeline, raising=False)
    # neutralise la persistance Mongo
    monkeypatch.setattr(rag, "_persist_exchange", lambda *a, **k: None)

    app = FastAPI(); app.include_router(rag.router)
    client = TestClient(app)
    r = client.post("/api/ask", json={"question": "Catégorise toutes les attaques du corpus",
                                      "mode": "auto"})
    evs = _events(r.text)
    assert any(e.get("type") == "route" and e.get("mode") == "synth" for e in evs)
    assert any(e.get("type") == "map" for e in evs)
    assert any(e.get("type") == "done" and e.get("found") for e in evs)



def test_synth_respects_an_explicit_source_scope(monkeypatch):
    captured = {}

    def fake_pipeline(question, aspect=None, **kw):
        captured.update(kw)
        documents = kw["prefilter"](aspect)
        yield {"type": "token", "text": "Synthèse"}
        yield {"type": "done", "result": {"answer": "Synthèse", "documents": documents,
                                              "n_items": 1}}

    monkeypatch.setattr(rag, "_synthesize_corpus", fake_pipeline, raising=False)
    monkeypatch.setattr(rag, "_persist_exchange", lambda *a, **k: {"saved": True, "message": None})

    app = FastAPI(); app.include_router(rag.router)
    response = TestClient(app).post("/api/ask", json={
        "question": "Catégorise toutes les attaques",
        "mode": "synth",
        "source": "architecture.md",
    })

    events = _events(response.text)
    assert captured["prefilter"]("ignored") == ["architecture.md"]
    assert any(event.get("type") == "done" and event.get("found") for event in events)


def test_persistence_result_is_streamed_to_the_client(monkeypatch):
    def fake_pipeline(question, aspect=None, **kw):
        yield {"type": "done", "result": {"answer": "Synthèse", "documents": ["a.md"],
                                              "n_items": 1}}

    monkeypatch.setattr(rag, "_synthesize_corpus", fake_pipeline, raising=False)
    monkeypatch.setattr(rag, "_persist_exchange", lambda *a, **k: {
        "saved": False, "message": "Conversation non sauvegardée : MongoDB indisponible."
    })

    app = FastAPI(); app.include_router(rag.router)
    response = TestClient(app).post("/api/ask", json={
        "question": "Catégorise toutes les attaques", "mode": "synth"
    })

    events = _events(response.text)
    assert any(event.get("type") == "persistence" and event.get("saved") is False
               for event in events)
