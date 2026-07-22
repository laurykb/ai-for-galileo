# tests/test_corpus_extract.py
from core.corpus_extract import extract_from_document


def test_extract_parses_items_from_llm(monkeypatch):
    fake_material = ("résumé", 3, "résumés de section RAPTOR")
    fake_llm = lambda prompt: "- Déni de service\n- Injection SQL\n\n- Rejeu\n"
    out = extract_from_document("doc.md", "attaques",
                                llm=fake_llm, gather=lambda src: fake_material)
    assert out["document"] == "doc.md"
    assert out["items"] == ["Déni de service", "Injection SQL", "Rejeu"]


def test_extract_empty_material_returns_no_items():
    out = extract_from_document("doc.md", "attaques",
                                llm=lambda p: "IGNORED",
                                gather=lambda src: ("", 0, "échantillon de chunks"))
    assert out["items"] == []
    assert out["document"] == "doc.md"
