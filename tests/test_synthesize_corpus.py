# tests/test_synthesize_corpus.py
from core.synthesize_corpus import synthesize_corpus


def test_pipeline_emits_glassbox_events_and_reduces(monkeypatch):
    monkeypatch.setenv("CORPUS_MAP_CONCURRENCY", "1")
    # 2 documents pré-filtrés ; extraction factice par doc ; reduce factice.
    prefilter = lambda aspect: ["a.md", "b.md"]
    def extract(document, aspect):
        data = {"a.md": ["Déni de service", "Rejeu"], "b.md": ["Injection SQL", "Rejeu"]}
        return {"document": document, "items": data[document], "raw": ""}
    reduce_llm = lambda prompt: "**Réseau**\n- Déni de service (a.md)\n- Rejeu (a.md, b.md)\n**Applicatif**\n- Injection SQL (b.md)"

    events = list(synthesize_corpus("catégorise les attaques", aspect="attaques",
                                    llm=reduce_llm, prefilter=prefilter, extract=extract))
    types = [e["type"] for e in events]
    assert types[0] == "stage" and events[0]["stage"] == "prefilter"
    assert events[0]["documents"] == ["a.md", "b.md"]
    map_events = [e for e in events if e["type"] == "map"]
    assert len(map_events) == 2 and map_events[0]["total"] == 2
    assert any(e["type"] == "stage" and e["stage"] == "reduce" for e in events)
    done = events[-1]
    assert done["type"] == "done"
    assert set(done["result"]["documents"]) == {"a.md", "b.md"}
    assert "Injection SQL" in done["result"]["answer"]


def test_pipeline_no_relevant_docs(monkeypatch):
    events = list(synthesize_corpus("q", aspect="x", llm=lambda p: "",
                                    prefilter=lambda a: [], extract=lambda d, a: None))
    done = events[-1]
    assert done["type"] == "done"
    assert done["result"]["documents"] == []
    assert done["result"]["n_items"] == 0
