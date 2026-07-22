from tools.corpus_tools import list_sources, extract_from_document_tool


def test_list_sources_tool(monkeypatch):
    import core.corpus as corpus
    monkeypatch.setattr(corpus, "list_indexed_sources", lambda db=None: ["a.md", "b.md"])
    out = list_sources()
    assert out["ok"] is True and out["sources"] == ["a.md", "b.md"]


def test_extract_tool(monkeypatch):
    import core.corpus_extract as ce
    monkeypatch.setattr(ce, "extract_from_document",
                        lambda document, aspect: {"document": document, "items": ["X"], "raw": ""})
    out = extract_from_document_tool("a.md", "attaques")
    assert out["ok"] is True and out["items"] == ["X"] and out["document"] == "a.md"
