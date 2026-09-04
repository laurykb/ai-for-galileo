import copy
import json

from core.sra.classify_target_objectives import classify_target_objectives


def test_consolide_par_effet_sans_dupliquer_les_domaines_ni_modifier_la_source():
    source = {
        "generated_at": "2026-08-05T13:04:23",
        "target_objectives": [{
            "to_id": "TO-001", "ro_type_id": "RO-01",
            "ro_canonical_type": "Acteur étatique", "ro_definition": "Capacités avancées",
            "attacks": [
                {"scenario_id": "AS-001", "attack": "Brouillage", "objective": "Dégrader le SIS",
                 "target_names": ["Signal E1"], "target_domains": ["space_segment", "communications"],
                 "security_properties": ["availability"], "motivations": ["disruption"],
                 "rationale": "r1", "evidence": [{"source": "a", "quote": "q1"}]},
                {"scenario_id": "AS-002", "attack": "Leurrage", "objective": "Altérer la navigation",
                 "target_names": ["Signal E5"], "target_domains": ["space_segment"],
                 "security_properties": ["integrity"], "motivations": ["strategic_advantage"],
                 "rationale": "r2", "evidence": [{"source": "b", "quote": "q2"}]},
            ],
        }],
    }
    original = copy.deepcopy(source)

    def llm(prompt, format=None):
        domain = prompt.split("[DOMAINE CIBLE]\n", 1)[1].split("\n", 1)[0]
        return json.dumps({
            "nom_attaque_generique": f"Attaques {domain}",
            "resume_attaques": "Synthèse fidèle",
            "resume_objectifs": "Objectifs consolidés",
        })

    table = classify_target_objectives(
        "p", source, llm=llm, source_to_revision_id="to-v1",
        source_ro_revision_id="ro-v2",
    )

    assert source == original
    assert table["source_to_revision_id"] == "to-v1"
    assert table["source_ro_revision_id"] == "ro-v2"
    assert table["schema_version"] == "2.0"
    assert [(row["ro_type_id"], row["effect_category"]) for row in table["classified_objectives"]] == [
        ("RO-01", "integrity_or_control"), ("RO-01", "service_disruption"),
    ]
    assert [scenario_id for row in table["classified_objectives"] for scenario_id in row["scenario_ids"]] == ["AS-002", "AS-001"]
    disrupted = table["classified_objectives"][1]
    assert disrupted["primary_target_domain"] == "space_segment"
    assert disrupted["secondary_target_domains"] == ["communications"]
    assert [event["stage"] for event in table["trace"]] == [
        "sra_to_classification_start",
        "sra_to_classification_group",
        "sra_to_classification_group",
    ]


def test_classe_sans_domaine_dans_other_et_conserve_un_repli():
    source = {"target_objectives": [{
        "to_id": "TO-001", "ro_type_id": "RO-03", "ro_canonical_type": "Cybercriminel",
        "attacks": [{"scenario_id": "AS-010", "attack": "Rançongiciel",
                     "objective": "Extorquer", "target_names": ["GCS"],
                     "target_domains": [], "rationale": "r", "evidence": []}],
    }]}
    table = classify_target_objectives(
        "p", source, llm=lambda *_args, **_kwargs: "{invalid",
    )
    row = table["classified_objectives"][0]
    assert row["target_domain"] == "other"
    assert row["scenario_ids"] == ["AS-010"]
    assert row["effect_category"] == "other"
    assert row["generic_attack_name"] == "Autre effet hostile — autre"
    assert table["warnings"]

def test_exporte_un_nouvel_excel_classe(tmp_path):
    from openpyxl import load_workbook
    from core.sra.classify_target_objectives import classify_target_objectives
    from core.sra.target_exporters import export_classified_target_xlsx

    source = {"target_objectives": [{
        "to_id": "TO-001", "ro_type_id": "RO-01", "ro_canonical_type": "Acteur étatique",
        "attacks": [{"scenario_id": "AS-001", "attack": "Brouillage",
                     "objective": "Dégrader", "target_names": ["Signal E1"],
                     "target_domains": ["space_segment"], "rationale": "r",
                     "evidence": []}],
    }]}
    table = classify_target_objectives(
        "p", source,
        llm=lambda *_args, **_kwargs: json.dumps({
            "nom_attaque_generique": "Attaques contre les signaux spatiaux",
            "resume_attaques": "Brouillage des signaux",
            "resume_objectifs": "Dégrader le service",
        }),
    )
    output = tmp_path / "classified.xlsx"
    export_classified_target_xlsx(table, output)

    sheet = load_workbook(output).active
    assert sheet.title == "TO décisionnels"
    assert sheet["D2"].value == "service_disruption"
    assert sheet["E2"].value == "space_segment"
    assert sheet["G2"].value == "Attaques contre les signaux spatiaux"
    assert sheet["J2"].value == "AS-001"

def test_map_reduce_force_un_vrai_split_quand_le_surcout_json_depasse_la_limite(monkeypatch):
    import core.sra.classify_target_objectives as module

    scenarios = [
        {"scenario_id": "AS-1", "attaque": "A" * 20, "objectif": "O", "justification": "R"},
        {"scenario_id": "AS-2", "attaque": "B" * 20, "objectif": "P", "justification": "S"},
    ]
    item_total = sum(len(json.dumps(item, ensure_ascii=False)) for item in scenarios)
    assert len(json.dumps(scenarios, ensure_ascii=False)) > item_total
    monkeypatch.setattr(module, "SUMMARY_BATCH_CHARS", item_total)
    calls = []

    def llm(prompt, format=None):
        calls.append(prompt)
        return json.dumps({
            "nom_attaque_generique": "Attaque générique",
            "resume_attaques": "Synthèse",
            "resume_objectifs": "Objectifs",
        })

    result = module._summarize(
        {"ro_type_id": "RO-09"}, "data", scenarios, llm,
        lambda *_args, **_kwargs: None,
    )
    assert result["generic_attack_name"] == "Attaque générique"
    assert len(calls) == 3
