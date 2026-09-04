from core.sra.scenario_enrichment import enrich_scenario
from core.sra.treatment import initialize_treatment_plan


def test_enrichissement_est_auditable_et_ne_supprime_aucune_information():
    source = {
        "scenario_id": "AS-1", "attack": "Brouillage de la liaison",
        "objective": "Interrompre le service", "rationale": "Accès distant à la liaison",
        "target_names": ["Signal E1"], "target_domains": ["communications", "space_segment"],
        "security_properties": ["availability"], "motivations": ["disruption"],
        "evidence": [{"source": "a", "quote": "signal E1"}],
    }
    result = enrich_scenario(source, "RO-01", 2)
    assert result["scenario_id"] == source["scenario_id"]
    assert result["primary_target_domain"] == "space_segment"
    assert result["secondary_target_domains"] == ["communications"]
    assert result["effect_category"] == "service_disruption"
    assert result["plausibility_method"] == "heuristic_v1"
    assert result["plausibility_level"] in {"medium", "high"}


def test_plan_de_traitement_ignore_seulement_les_to_rejetes_et_n_invente_pas_de_mesure():
    decision = {
        "source_to_revision_id": "to-v3", "generated_at": "2026-08-06T12:00:00",
        "classified_objectives": [
            {"classification_id": "TOC-1", "ro_type_id": "RO-01",
             "objective_summary": "Maintenir le service", "effect_category": "service_disruption",
             "target_names": ["E1"], "security_properties": ["availability"],
             "review_status": "accepted"},
            {"classification_id": "TOC-2", "ro_type_id": "RO-02",
             "objective_summary": "Écarter", "effect_category": "other",
             "review_status": "rejected"},
        ],
    }
    plan = initialize_treatment_plan("p", decision)
    assert plan["source_to_revision_id"] == "to-v3"
    assert [item["classification_id"] for item in plan["items"]] == ["TOC-1"]
    assert plan["items"][0]["measures"] == []
    assert plan["items"][0]["implementation_status"] == "to_define"
