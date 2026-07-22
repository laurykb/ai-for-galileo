"""Tests du plancher de couverture par document — offline, déterministe."""
from retrieval.coverage import apply_coverage_floor


def _mk(src, ce, i):
    return {"doc": f"{src}-{i}", "meta": {"source": src}, "ce_score": ce}


def _big(n=15):
    return [_mk("BIG", 0.90 - i * 0.01, i) for i in range(n)]


def test_small_doc_floored_in():
    items = _big(15) + [_mk("SMALL", 0.55, 99)]  # SMALL dernier, hors top-15
    out = apply_coverage_floor(items, base_k=15, per_doc_floor=1, max_chunks=24, floor_min_ce=0.5)
    assert "SMALL" in {it["meta"]["source"] for it in out}
    assert len(out) <= 24


def test_irrelevant_doc_not_floored():
    items = _big(15) + [_mk("SMALL", 0.30, 99)]  # sous le seuil floor_min_ce
    out = apply_coverage_floor(items, base_k=15, per_doc_floor=1, max_chunks=24, floor_min_ce=0.5)
    assert "SMALL" not in {it["meta"]["source"] for it in out}


def test_no_change_when_all_present():
    items = [_mk("A", 0.9, 0), _mk("B", 0.8, 1), _mk("A", 0.7, 2)]
    out = apply_coverage_floor(items, base_k=3, per_doc_floor=1, max_chunks=24, floor_min_ce=None)
    assert len(out) == 3


def test_cap_respected_and_floored_survive():
    # base_k=3 -> top3 tous BIG ; SMALL doit entrer malgré le plafond serré.
    items = _big(5) + [_mk("SMALL", 0.40, 99)]
    out = apply_coverage_floor(items, base_k=3, per_doc_floor=1, max_chunks=3, floor_min_ce=0.3)
    assert len(out) <= 3
    assert "SMALL" in {it["meta"]["source"] for it in out}


def test_empty_input():
    assert apply_coverage_floor([], base_k=15) == []


def test_elastic_pool_scales_with_corpus():
    from retrieval.coverage import elastic_candidate_pool
    # Petit corpus -> proche de la borne basse.
    assert elastic_candidate_pool(2, pool_min=40, pool_max=120, per_doc=3) == 46
    # Gros corpus -> plafonné à la borne haute.
    assert elastic_candidate_pool(50, pool_min=40, pool_max=120, per_doc=3) == 120
    # Corpus vide/inconnu -> borne basse.
    assert elastic_candidate_pool(0, pool_min=40, pool_max=120, per_doc=3) == 40
