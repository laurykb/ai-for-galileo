"""Métriques RAGAS locales — juge/embeddings injectés, 100% hors-ligne."""
from core.evaluation import decompose_claims


def test_decompose_parses_atomic_claims():
    fake = lambda prompt: "- Le chiffrement est AES-256\n- La clé fait 256 bits\n\n* Le mode est GCM\n"
    out = decompose_claims("…", llm=fake)
    assert out == ["Le chiffrement est AES-256", "La clé fait 256 bits", "Le mode est GCM"]


def test_decompose_empty_text():
    assert decompose_claims("", llm=lambda p: "IGNORED") == []
