import json

import core.ebios.atelier1 as atelier1_module
from core.ebios.atelier1 import _parse_json, generate_atelier1
from core.ebios.evidence import resolve_preuves
from core.ebios.schemas import EbiosStudy

_CHUNKS = [
    {"content": "Le contrôle mission assure le pilotage.", "page_number": 4},
    {"content": "La perte du contrôle interrompt la mission.", "page_number": 9},
]

_VM_JSON = {
    "mission": "Surveillance spatiale",
    "valeurs_metier": [{"denomination": "Contrôle mission", "nature": "processus",
                        "description": "Pilotage.", "responsable_nom": "Dupont",
                        "responsable_type": "INTERNE",
                        "extraits": ["Le contrôle mission assure le pilotage."]}],
    "biens_supports": [{"denomination": "Segment sol", "description": "Stations.",
                        "responsable_nom": "Martin", "responsable_type": "EXTERNE",
                        "valeurs_metier": ["Contrôle mission"],
                        "extraits": ["Le contrôle mission assure le pilotage."]}],
}
_ER_JSON = {"evenements_redoutes": [
    {"valeur_metier": "Contrôle mission", "evenement": "Perte du contrôle",
     "impact": "Interruption", "gravite": 3, "justification_gravite": "Arrêt",
     "extraits": ["La perte du contrôle interrompt la mission."]},
    {"valeur_metier": "Inconnue", "evenement": "Événement orphelin",
     "impact": "x", "gravite": 9, "justification_gravite": "", "extraits": []},
]}
_GAP_JSON = {"socle_securite": [
    {"type_referentiel": "Norme", "nom_referentiel": "ISO 27001",
     "etat_application": "Appliqué",
     "ecarts": [{"description": "Pas de revue", "justification": "", "extraits": []}]},
]}


def fake_llm(prompt, format=None):
    assert format == "json"
    if "valeurs métier" in prompt.lower():
        return json.dumps(_VM_JSON, ensure_ascii=False)
    if "redout" in prompt.lower():
        return json.dumps(_ER_JSON, ensure_ascii=False)
    return json.dumps(_GAP_JSON, ensure_ascii=False)


def test_resolve_preuves_localise_la_page():
    preuves, warns = resolve_preuves(
        ["Le contrôle mission assure le pilotage.", "Texte jamais vu"], "doc-a.md", _CHUNKS)
    assert preuves[0]["page"] == 4 and preuves[0]["document_id"] == "doc-a.md"
    assert preuves[1]["page"] is None
    assert any("non localisée" in w for w in warns)


def test_generation_complete_et_sourcee():
    stages = []
    study = generate_atelier1("p-1", ["doc-a.md"], llm=fake_llm,
                              gather=lambda doc: _CHUNKS,
                              progress=lambda s, d: stages.append(s))
    model = EbiosStudy.model_validate(study)          # conforme au schéma
    assert model.project_id == "p-1"
    a1 = model.atelier_1
    assert a1.mission == "Surveillance spatiale"
    assert a1.valeurs_metier[0].id == "vm-1"
    assert a1.valeurs_metier[0].preuves[0].page == 4
    assert a1.biens_supports[0].valeurs_metier_associees == ["vm-1"]
    assert a1.evenements_redoutes[0].valeur_metier_id == "vm-1"
    assert {"extraction", "consolidation", "validation"} <= set(stages)


def test_entrees_douteuses_signalees_jamais_inventees():
    study = generate_atelier1("p-1", ["doc-a.md"], llm=fake_llm, gather=lambda d: _CHUNKS)
    er2 = study["atelier_1"]["evenements_redoutes"][1]
    assert er2["valeur_metier_id"] == ""              # VM inconnue -> non rattaché
    assert er2["gravite"] == 1                        # 9 hors échelle -> défaut + warning
    warns = " ".join(study["warnings"]).lower()
    assert "gravité" in warns or "gravite" in warns
    assert "sans preuve" in warns


def test_dedup_multi_documents():
    study = generate_atelier1("p-1", ["doc-a.md", "doc-b.md"],
                              llm=fake_llm, gather=lambda d: _CHUNKS)
    denoms = [v["denomination"] for v in study["atelier_1"]["valeurs_metier"]]
    assert denoms == ["Contrôle mission"]             # dédupliqué entre les 2 docs
    assert len(study["atelier_1"]["valeurs_metier"][0]["preuves"]) >= 1


def test_json_illisible_isole_pas_de_crash():
    def broken_llm(prompt, format=None):
        if "redout" in prompt.lower():
            return "pas du json"
        return fake_llm(prompt, format=format)
    study = generate_atelier1("p-1", ["doc-a.md"], llm=broken_llm, gather=lambda d: _CHUNKS)
    assert study["atelier_1"]["evenements_redoutes"] == []
    assert any("doc-a.md" in w and "redout" in w.lower() for w in study["warnings"])


def test_json_liste_ou_none_isole():
    assert _parse_json(None) is None

    def llm_vm_liste(prompt, format=None):
        if "valeurs métier" in prompt.lower():
            return "[]"
        return fake_llm(prompt, format=format)

    study = generate_atelier1("p-1", ["doc-a.md"], llm=llm_vm_liste, gather=lambda d: _CHUNKS)
    assert study["atelier_1"]["valeurs_metier"] == []
    assert any("doc-a.md" in w for w in study["warnings"])


def test_item_non_dict_ignore():
    def llm_item_non_dict(prompt, format=None):
        if "valeurs métier" in prompt.lower():
            return json.dumps({"mission": "M", "valeurs_metier": ["chaine"],
                               "biens_supports": []}, ensure_ascii=False)
        return fake_llm(prompt, format=format)

    study = generate_atelier1("p-1", ["doc-a.md"], llm=llm_item_non_dict,
                              gather=lambda d: _CHUNKS)
    assert study["atelier_1"]["valeurs_metier"] == []


_CHUNKS_ALPHA = [
    {"content": "Alpha signal perdu detecte sur le segment.", "page_number": 9},
    {"content": "Alpha sauvegarde absente pour le systeme.", "page_number": 9},
]
_CHUNKS_BETA = [
    {"content": "Beta signal perdu detecte sur le segment.", "page_number": 12},
    {"content": "Beta sauvegarde absente pour le systeme.", "page_number": 12},
]


def _llm_dedup(prompt, format=None):
    p = prompt.lower()
    if "valeurs métier" in p:
        return json.dumps({"mission": "M", "valeurs_metier": [], "biens_supports": []},
                          ensure_ascii=False)
    marker = "Alpha" if "alpha" in p else "Beta"
    if "redout" in p:
        return json.dumps({"evenements_redoutes": [
            {"valeur_metier": "Contrôle mission", "evenement": "Perte du signal",
             "impact": "x", "gravite": 2, "justification_gravite": "j",
             "extraits": [f"{marker} signal perdu detecte sur le segment."]}]},
            ensure_ascii=False)
    return json.dumps({"socle_securite": [
        {"type_referentiel": "Norme", "nom_referentiel": "ISO 27001",
         "etat_application": "Appliqué",
         "ecarts": [{"description": "Pas de sauvegarde", "justification": "",
                     "extraits": [f"{marker} sauvegarde absente pour le systeme."]}]}]},
        ensure_ascii=False)


def _gather_dedup(doc):
    return _CHUNKS_ALPHA if doc == "doc-a.md" else _CHUNKS_BETA


def test_troncature_signalee(monkeypatch):
    monkeypatch.setattr(atelier1_module, "_max_extract_chars", lambda: 30)
    long_chunks = [{"content": "x" * 50, "page_number": 1}]
    study = generate_atelier1("p-1", ["doc-a.md"], llm=fake_llm,
                              gather=lambda d: long_chunks)
    warns = " ".join(study["warnings"])
    assert "tronqué" in warns.lower() and "doc-a.md" in warns


def test_dedup_fusionne_preuves_er_et_gap():
    study = generate_atelier1("p-1", ["doc-a.md", "doc-b.md"],
                              llm=_llm_dedup, gather=_gather_dedup)
    ers = study["atelier_1"]["evenements_redoutes"]
    assert len(ers) == 1
    assert len(ers[0]["preuves"]) == 2
    assert {p["page"] for p in ers[0]["preuves"]} == {9, 12}

    gaps = study["atelier_1"]["socle_securite"]
    assert len(gaps) == 1
    ecarts = gaps[0]["ecarts"]
    assert len(ecarts) == 1
    assert len(ecarts[0]["preuves"]) == 2
    assert {p["page"] for p in ecarts[0]["preuves"]} == {9, 12}
