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


def test_write_last_eval_persists_aggregate(tmp_path):
    """Régression : run_eval doit écrire evals/last_eval.json avec l'agrégat
    complet (dont answer_relevancy) — le mode ragas ne l'écrivait pas."""
    import json
    import evals.run_eval as R
    path = tmp_path / "last_eval.json"
    agg = {"context_recall": 0.53, "answer_relevancy": 0.6, "faithfulness": 0.5}
    R._write_last_eval(path, dataset_name="golden", mode="ragas",
                       run_name="ragas-x", aggregate=agg, num_questions=30)
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mode"] == "ragas"
    assert data["run_name"] == "ragas-x"
    assert data["num_questions"] == 30
    assert data["aggregate"]["answer_relevancy"] == 0.6
