"""Métriques RAGAS locales — juge/embeddings injectés, 100% hors-ligne."""
from core.evaluation import decompose_claims


def test_decompose_parses_atomic_claims():
    fake = lambda prompt: "- Le chiffrement est AES-256\n- La clé fait 256 bits\n\n* Le mode est GCM\n"
    out = decompose_claims("…", llm=fake)
    assert out == ["Le chiffrement est AES-256", "La clé fait 256 bits", "Le mode est GCM"]


def test_decompose_empty_text():
    assert decompose_claims("", llm=lambda p: "IGNORED") == []


def test_faithfulness_supported_ratio(monkeypatch):
    # 2 affirmations ; le juge en soutient 1 (score 1.0) et pas l'autre (0.0).
    from core import evaluation as E
    monkeypatch.setattr(E, "decompose_claims", lambda text, llm=None: ["A", "B"])
    # juge : "A" soutenue, "B" non
    monkeypatch.setattr(
        E, "_ask_judge",
        lambda _llm, prompt: 1.0 if "A" in prompt.split("AFFIRMATION")[-1] else 0.0,
    )
    chunks = [{"doc": "contexte"}]
    assert E.faithfulness_ragas("réponse", chunks, llm=object()) == 0.5


def test_context_recall_attributable_ratio(monkeypatch):
    from core import evaluation as E
    monkeypatch.setattr(E, "decompose_claims", lambda text, llm=None: ["x", "y", "z"])
    monkeypatch.setattr(E, "_ask_judge", lambda _llm, prompt: 1.0 if "x" in prompt or "y" in prompt else 0.0)
    val = E.context_recall_ragas("ref", [{"doc": "c"}], llm=object())
    assert abs(val - 2/3) < 1e-6


def test_faithfulness_default_llm_uses_judge_helper(monkeypatch):
    """Regression test: faithfulness_ragas with llm=None must resolve to real judge."""
    from core import evaluation as E
    called = {"judge": False}
    monkeypatch.setattr(E, "decompose_claims", lambda text, llm=None: ["A"])
    monkeypatch.setattr(E, "_get_judge_llm", lambda: object())     # real judge resolved
    def fake_ask(llm, prompt):
        called["judge"] = llm is not None      # must NOT be None
        return 1.0
    monkeypatch.setattr(E, "_ask_judge", fake_ask)
    val = E.faithfulness_ragas("gen", [{"doc": "ctx"}])   # llm defaults to None → must resolve
    assert val == 1.0
    assert called["judge"] is True


def test_context_recall_default_llm_uses_judge_helper(monkeypatch):
    """Regression test: context_recall_ragas with llm=None must resolve to real judge."""
    from core import evaluation as E
    called = {"judge": False}
    monkeypatch.setattr(E, "decompose_claims", lambda text, llm=None: ["A"])
    monkeypatch.setattr(E, "_get_judge_llm", lambda: object())     # real judge resolved
    def fake_ask(llm, prompt):
        called["judge"] = llm is not None      # must NOT be None
        return 1.0
    monkeypatch.setattr(E, "_ask_judge", fake_ask)
    val = E.context_recall_ragas("ref", [{"doc": "ctx"}])   # llm defaults to None → must resolve
    assert val == 1.0
    assert called["judge"] is True


def test_context_precision_rank_aware(monkeypatch):
    from core import evaluation as E
    # 3 chunks ; pertinents = positions 1 et 3 (0-indexé 0 et 2).
    monkeypatch.setattr(E, "_judge_chunk_relevant",
                        lambda q, ref, doc, llm: doc in ("c0", "c2"))
    chunks = [{"doc": "c0"}, {"doc": "c1"}, {"doc": "c2"}]
    val = E.context_precision_ragas("q", "ref", chunks, topk=3, llm=object())
    # AP@k = mean(precision@rank aux positions pertinentes) = mean(1/1, 2/3) = 0.8333
    assert abs(val - (1.0 + 2/3) / 2) < 1e-6


def test_context_precision_no_relevant(monkeypatch):
    from core import evaluation as E
    monkeypatch.setattr(E, "_judge_chunk_relevant", lambda q, ref, doc, llm: False)
    assert E.context_precision_ragas("q", "ref", [{"doc": "c"}], llm=object()) == 0.0


def test_context_precision_default_llm_uses_judge_helper(monkeypatch):
    """Regression test: context_precision_ragas with llm=None must resolve to real judge."""
    from core import evaluation as E
    called = {"judge": False}
    monkeypatch.setattr(E, "_get_judge_llm", lambda: object())     # real judge resolved
    def fake_judge_relevant(q, ref, doc, llm):
        called["judge"] = llm is not None      # must NOT be None
        return True
    monkeypatch.setattr(E, "_judge_chunk_relevant", fake_judge_relevant)
    val = E.context_precision_ragas("q", "ref", [{"doc": "c"}])   # llm defaults to None → must resolve
    assert val == 1.0
    assert called["judge"] is True
