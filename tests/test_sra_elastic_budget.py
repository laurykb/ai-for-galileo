from api.sra import _dynamic_to_budget, _elastic_to_budget, _ro_workload


def test_to_budget_grows_with_documents_without_repeating_windows_per_ro_group():
    small_budget, small = _elastic_to_budget(
        {"a.md": 24000}, ro_types=3, batch_chars=24000, batch_overlap=1500
    )
    large_budget, large = _elastic_to_budget(
        {"a.md": 1000000, "b.md": 1000000}, ro_types=10,
        batch_chars=24000, batch_overlap=1500,
    )

    assert large["windows"] > small["windows"]
    assert large["ro_type_groups"] == small["ro_type_groups"] == 1
    assert large["estimated_map_calls"] == large["windows"]
    assert large_budget["max_total_tokens"] > small_budget["max_total_tokens"]
    assert large_budget["max_llm_calls"] > small_budget["max_llm_calls"]
    assert large_budget["max_wall_s"] > small_budget["max_wall_s"]


def test_to_budget_keeps_minimum_guardrails_for_tiny_workload():
    budget, estimate = _elastic_to_budget(
        {"a.md": 10}, ro_types=1, batch_chars=24000, batch_overlap=1500
    )

    assert estimate["estimated_map_calls"] == 1
    assert budget == {
        "max_llm_calls": 50,
        "max_wall_s": 1800,
        "max_total_tokens": 250000,
    }


def test_dynamic_budget_accounts_for_post_map_candidates_and_caps_wall_time():
    base = {"max_llm_calls": 50, "max_wall_s": 1800, "max_total_tokens": 250000}
    budget, estimate = _dynamic_to_budget(base, map_calls=122, candidates=1200, batch_chars=18000)
    assert estimate["estimated_adjudication_calls"] > 20
    assert budget["max_llm_calls"] > 203
    assert budget["max_wall_s"] <= 7200

def test_ro_workload_reuses_single_pass_budget(monkeypatch):
    import api.sra as sra
    monkeypatch.setattr(
        sra,
        "_to_workload",
        lambda repo, documents, ro_types, config: (
            {"max_llm_calls": 60, "max_wall_s": 2000, "max_total_tokens": 300000},
            {"documents": 2, "windows": 12, "ro_types": ro_types,
             "ro_type_groups": 1, "estimated_map_calls": 12},
        ),
    )

    budget, estimate = _ro_workload(object(), ["a", "b"], {})

    assert budget["max_total_tokens"] == 300000
    assert estimate["analysis"] == "risk_origins"
    assert estimate["estimated_map_calls"] == 12
    assert "ro_types" not in estimate
