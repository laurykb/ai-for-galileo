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


def test_map_error_isolated(monkeypatch):
    """Un échec extract() sur UN document ne fait pas échouer tout le run : ce
    document apparaît dans un événement `map` avec n_items=0, les autres docs
    atteignent le reduce normalement, et `done` est bien émis."""
    monkeypatch.setenv("CORPUS_MAP_CONCURRENCY", "1")
    prefilter = lambda aspect: ["broken.md", "b.md"]

    def extract(document, aspect):
        if document == "broken.md":
            raise TimeoutError("Ollama timeout")
        return {"document": document, "items": ["Injection SQL"], "raw": ""}

    def reduce_llm(prompt):
        return "**Applicatif**\n- Injection SQL [src: b.md]"

    events = list(synthesize_corpus("catégorise", aspect="attaques",
                                    llm=reduce_llm, prefilter=prefilter, extract=extract))

    map_events = {e["document"]: e for e in events if e["type"] == "map"}
    assert map_events["broken.md"]["n_items"] == 0
    assert map_events["b.md"]["n_items"] == 1

    done = events[-1]
    assert done["type"] == "done"
    assert set(done["result"]["documents"]) == {"broken.md", "b.md"}
    assert done["result"]["n_items"] == 1
    assert "Injection SQL" in done["result"]["answer"]


def test_reduce_batched_when_material_large(monkeypatch):
    """Un matériau MAP qui dépasse _MAX_MATERIAL_CHARS déclenche un REDUCE par lots
    borné (jamais un unique appel LLM qui tronquerait silencieusement)."""
    monkeypatch.setenv("CORPUS_MAP_CONCURRENCY", "1")

    # 10 docs, chacun avec un item très long (~3000 chars) => matériau total > 24000.
    n_docs = 10
    docs = [f"doc{i}.md" for i in range(n_docs)]
    long_item = "X" * 3000

    prefilter = lambda aspect: docs

    def extract(document, aspect):
        return {"document": document, "items": [long_item], "raw": ""}

    calls = []

    def counting_llm(prompt):
        calls.append(prompt)
        return f"synthèse partielle {len(calls)} [src: doc0.md]"

    events = list(synthesize_corpus("catégorise", aspect="attaques",
                                    llm=counting_llm, prefilter=prefilter, extract=extract))

    reduce_stage_events = [e for e in events if e["type"] == "stage" and e["stage"] == "reduce"]
    assert len(reduce_stage_events) == 1
    reduce_stage = reduce_stage_events[0]
    assert reduce_stage.get("batched") is True
    n_batches = reduce_stage["batches"]
    assert n_batches > 1

    # n_batches appels de réduction partielle + 1 appel de fusion finale.
    assert len(calls) == n_batches + 1

    done = events[-1]
    assert done["type"] == "done"
    assert set(done["result"]["documents"]) == set(docs)
    assert done["result"]["n_items"] == n_docs
    assert done["result"]["answer"].strip() != ""
