# tests/test_synthesize_corpus.py
from core.synthesize_corpus import synthesize_corpus


def test_pipeline_emits_glassbox_events_and_reduces(monkeypatch):
    monkeypatch.setenv("CORPUS_MAP_CONCURRENCY", "1")
    # 2 documents pré-filtrés ; extraction factice par doc ; reduce factice.
    prefilter = lambda aspect: ["a.md", "b.md"]
    def extract(document, aspect):
        data = {"a.md": ["Déni de service", "Rejeu"], "b.md": ["Injection SQL", "Rejeu"]}
        return {"document": document, "items": data[document], "raw": ""}
    captured_prompts = []
    def reduce_llm(prompt):
        captured_prompts.append(prompt)
        return ("**Réseau**\n- Déni de service [src: a.md]\n- Rejeu [src: a.md, src: b.md]\n"
                "**Applicatif**\n- Injection SQL [src: b.md]")

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

    # -- Fix B : attribution inline déterministe dans le matériau REDUCE -------
    # Chaque item du matériau porte son étiquette source verbatim (dérivée de
    # ex["document"], jamais du LLM) - la reformulation en (a.md) est interdite.
    assert len(captured_prompts) == 1
    material = captured_prompts[0]
    assert "Déni de service  [src: a.md]" in material
    assert "Rejeu  [src: a.md]" in material
    assert "Injection SQL  [src: b.md]" in material
    assert "Rejeu  [src: b.md]" in material
    # La synthèse finale préserve bien les étiquettes (pas de citation inventée).
    assert "[src: a.md]" in done["result"]["answer"]
    assert "[src: b.md]" in done["result"]["answer"]


def test_pipeline_no_relevant_docs(monkeypatch):
    events = list(synthesize_corpus("q", aspect="x", llm=lambda p: "",
                                    prefilter=lambda a: [], extract=lambda d, a: None))
    done = events[-1]
    assert done["type"] == "done"
    assert done["result"]["documents"] == []
    assert done["result"]["n_items"] == 0
