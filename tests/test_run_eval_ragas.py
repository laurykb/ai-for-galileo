"""Test du mode --mode ragas de evals/run_eval.py (offline, métriques RAGAS stubées)."""


def test_ragas_mode_item(monkeypatch):
    import evals.run_eval as R
    from core import evaluation as E
    # stub retrieval+génération et métriques pour rester offline
    monkeypatch.setattr("core.ask.process_query",
                        lambda q, source_filter=None: ("réponse", [{"doc": "ctx"}], []))
    monkeypatch.setattr(E, "faithfulness_ragas", lambda g, c, llm=None: 0.9)
    monkeypatch.setattr(E, "context_recall_ragas", lambda r, c, llm=None: 0.8)
    monkeypatch.setattr(E, "context_precision_ragas", lambda q, r, c, topk=5, llm=None: 0.7)
    monkeypatch.setattr(E, "answer_relevancy_ragas", lambda g, q, llm=None, embed=None, n=3: 0.85)
    item = {"question": "Q ?", "answer": "ref", "expected_keywords": []}
    m = R._evaluate_item(item, mode="ragas", source_filter=None, use_judge=True, judge=None)
    assert m["status"] == "ok"
    assert m["faithfulness"] == 0.9 and m["context_recall"] == 0.8
    assert m["context_precision"] == 0.7 and m["answer_relevancy"] == 0.85
