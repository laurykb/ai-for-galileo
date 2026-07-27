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


def _no_merge_llm(prompt, format=None):
    """Fake merge_llm neutre : ne regroupe jamais rien (garde les tests d'extraction/
    consolidation hermétiques face au nouveau stage de fusion, activé par défaut)."""
    assert format == "json"
    return json.dumps({"groupes": []}, ensure_ascii=False)


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
                              progress=lambda s, d: stages.append(s),
                              merge_llm=_no_merge_llm)
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
    study = generate_atelier1("p-1", ["doc-a.md"], llm=fake_llm, gather=lambda d: _CHUNKS,
                              merge_llm=_no_merge_llm)
    er2 = study["atelier_1"]["evenements_redoutes"][1]
    assert er2["valeur_metier_id"] == ""              # VM inconnue -> non rattaché
    assert er2["gravite"] == 1                        # 9 hors échelle -> défaut + warning
    warns = " ".join(study["warnings"]).lower()
    assert "gravité" in warns or "gravite" in warns
    assert "sans preuve" in warns


def test_dedup_multi_documents():
    study = generate_atelier1("p-1", ["doc-a.md", "doc-b.md"],
                              llm=fake_llm, gather=lambda d: _CHUNKS,
                              merge_llm=_no_merge_llm)
    denoms = [v["denomination"] for v in study["atelier_1"]["valeurs_metier"]]
    assert denoms == ["Contrôle mission"]             # dédupliqué entre les 2 docs
    assert len(study["atelier_1"]["valeurs_metier"][0]["preuves"]) >= 1


def test_json_illisible_isole_pas_de_crash():
    def broken_llm(prompt, format=None):
        if "redout" in prompt.lower():
            return "pas du json"
        return fake_llm(prompt, format=format)
    study = generate_atelier1("p-1", ["doc-a.md"], llm=broken_llm, gather=lambda d: _CHUNKS,
                              merge_llm=_no_merge_llm)
    assert study["atelier_1"]["evenements_redoutes"] == []
    assert any("doc-a.md" in w and "redout" in w.lower() for w in study["warnings"])


def test_json_liste_ou_none_isole():
    assert _parse_json(None) is None

    def llm_vm_liste(prompt, format=None):
        if "valeurs métier" in prompt.lower():
            return "[]"
        return fake_llm(prompt, format=format)

    study = generate_atelier1("p-1", ["doc-a.md"], llm=llm_vm_liste, gather=lambda d: _CHUNKS,
                              merge_llm=_no_merge_llm)
    assert study["atelier_1"]["valeurs_metier"] == []
    assert any("doc-a.md" in w for w in study["warnings"])


def test_item_non_dict_ignore():
    def llm_item_non_dict(prompt, format=None):
        if "valeurs métier" in prompt.lower():
            return json.dumps({"mission": "M", "valeurs_metier": ["chaine"],
                               "biens_supports": []}, ensure_ascii=False)
        return fake_llm(prompt, format=format)

    study = generate_atelier1("p-1", ["doc-a.md"], llm=llm_item_non_dict,
                              gather=lambda d: _CHUNKS, merge_llm=_no_merge_llm)
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
                              gather=lambda d: long_chunks, merge_llm=_no_merge_llm)
    warns = " ".join(study["warnings"])
    assert "tronqué" in warns.lower() and "doc-a.md" in warns


def test_dedup_fusionne_preuves_er_et_gap():
    study = generate_atelier1("p-1", ["doc-a.md", "doc-b.md"],
                              llm=_llm_dedup, gather=_gather_dedup, merge_llm=_no_merge_llm)
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


# ============================================================================
# Task 9 — Fusion sémantique à la consolidation (rôle `synthesize`)
# ============================================================================

_CHUNKS_MERGE_A = [{"content": "MARQUEURA controle mission pilote la trajectoire.",
                    "page_number": 3}]
_CHUNKS_MERGE_B = [{"content": "MARQUEURB controle de la mission pilote la trajectoire.",
                    "page_number": 5}]
_CHUNKS_MERGE_C = [{"content": "GAMMA controle tiers pilote la station.", "page_number": 7}]


def _gather_merge_ab(doc):
    return _CHUNKS_MERGE_A if doc == "doc-a.md" else _CHUNKS_MERGE_B


def _gather_merge_abc(doc):
    return {"doc-a.md": _CHUNKS_MERGE_A, "doc-b.md": _CHUNKS_MERGE_B,
            "doc-c.md": _CHUNKS_MERGE_C}[doc]


def _llm_extract_vm_dup(prompt, format=None):
    assert format == "json"
    if "valeurs métier" in prompt.lower():
        if "MARQUEURA" in prompt:
            return json.dumps({"mission": "Surveillance spatiale", "valeurs_metier": [
                {"denomination": "Contrôle mission", "nature": "processus",
                 "description": "Pilotage.", "responsable_nom": "", "responsable_type": "INTERNE",
                 "extraits": ["MARQUEURA controle mission pilote la trajectoire."]}],
                "biens_supports": []}, ensure_ascii=False)
        return json.dumps({"mission": "", "valeurs_metier": [
            {"denomination": "Contrôle de la mission", "nature": "processus",
             "description": "Pilotage.", "responsable_nom": "", "responsable_type": "INTERNE",
             "extraits": ["MARQUEURB controle de la mission pilote la trajectoire."]}],
            "biens_supports": []}, ensure_ascii=False)
    if "redout" in prompt.lower():
        return json.dumps({"evenements_redoutes": []}, ensure_ascii=False)
    return json.dumps({"socle_securite": []}, ensure_ascii=False)


def _llm_extract_vm_triple(prompt, format=None):
    assert format == "json"
    if "valeurs métier" in prompt.lower():
        if "MARQUEURA" in prompt:
            denom, extrait = "Alpha", "MARQUEURA controle mission pilote la trajectoire."
        elif "MARQUEURB" in prompt:
            denom, extrait = "Beta", "MARQUEURB controle de la mission pilote la trajectoire."
        else:
            denom, extrait = "Gamma", "GAMMA controle tiers pilote la station."
        return json.dumps({"mission": "M", "valeurs_metier": [
            {"denomination": denom, "nature": "processus", "description": "",
             "responsable_nom": "", "responsable_type": "INTERNE", "extraits": [extrait]}],
            "biens_supports": []}, ensure_ascii=False)
    if "redout" in prompt.lower():
        return json.dumps({"evenements_redoutes": []}, ensure_ascii=False)
    return json.dumps({"socle_securite": []}, ensure_ascii=False)


def _merge_llm_group_1_2(prompt, format=None):
    return json.dumps({"groupes": [[1, 2]]}, ensure_ascii=False)


def test_fusion_semantique_vm_fusionne_doublons_multidoc():
    stages = []
    study = generate_atelier1("p-1", ["doc-a.md", "doc-b.md"],
                              llm=_llm_extract_vm_dup, gather=_gather_merge_ab,
                              merge_llm=_merge_llm_group_1_2,
                              progress=lambda s, d: stages.append((s, d)))
    EbiosStudy.model_validate(study)
    vms = study["atelier_1"]["valeurs_metier"]
    assert len(vms) == 1
    assert vms[0]["id"] == "vm-1"
    assert vms[0]["denomination"] == "Contrôle mission"  # égalité de preuves -> la première
    assert {p["document_id"] for p in vms[0]["preuves"]} == {"doc-a.md", "doc-b.md"}
    warns = " ".join(study["warnings"])
    assert "Fusion sémantique" in warns
    assert "Contrôle de la mission" in warns and "Contrôle mission" in warns
    assert ("fusion", {"categorie": "vm"}) in stages


def test_fusion_groupe_indices_hors_limites_ignore():
    def merge_out_of_range(prompt, format=None):
        return json.dumps({"groupes": [[1, 99]]}, ensure_ascii=False)

    study = generate_atelier1("p-1", ["doc-a.md", "doc-b.md", "doc-c.md"],
                              llm=_llm_extract_vm_triple, gather=_gather_merge_abc,
                              merge_llm=merge_out_of_range)
    EbiosStudy.model_validate(study)
    vms = study["atelier_1"]["valeurs_metier"]
    assert len(vms) == 3  # groupe invalide (indice hors limites) -> aucune fusion
    assert any("fusion sémantique" in w.lower() for w in study["warnings"])


def test_fusion_groupe_chevauchant_ignore():
    def merge_overlap(prompt, format=None):
        return json.dumps({"groupes": [[1, 2], [2, 3]]}, ensure_ascii=False)

    study = generate_atelier1("p-1", ["doc-a.md", "doc-b.md", "doc-c.md"],
                              llm=_llm_extract_vm_triple, gather=_gather_merge_abc,
                              merge_llm=merge_overlap)
    EbiosStudy.model_validate(study)
    vms = study["atelier_1"]["valeurs_metier"]
    assert len(vms) == 2  # 1er groupe fusionné, 2e ignoré (indice 2 déjà utilisé)
    assert {v["denomination"] for v in vms} == {"Alpha", "Gamma"}
    assert [v["id"] for v in vms] == ["vm-1", "vm-2"]  # ids réassignés séquentiellement
    assert any("fusion sémantique" in w.lower() for w in study["warnings"])


_VM2_JSON = {"mission": "M", "valeurs_metier": [
    {"denomination": "Alpha", "nature": "processus", "description": "",
     "responsable_nom": "", "responsable_type": "INTERNE",
     "extraits": ["Alpha pilote le systeme."]},
    {"denomination": "Beta", "nature": "processus", "description": "",
     "responsable_nom": "", "responsable_type": "INTERNE",
     "extraits": ["Beta pilote le systeme."]}],
    "biens_supports": []}

_CHUNKS_2VM = [{"content": "Alpha pilote le systeme. Beta pilote le systeme.",
               "page_number": 1}]


def _llm_2vm_same_vm_er(prompt, format=None):
    p = prompt.lower()
    if "valeurs métier" in p:
        return json.dumps(_VM2_JSON, ensure_ascii=False)
    if "redout" in p:
        return json.dumps({"evenements_redoutes": [
            {"valeur_metier": "Alpha", "evenement": "Evt un", "impact": "", "gravite": 2,
             "justification_gravite": "", "extraits": []},
            {"valeur_metier": "Alpha", "evenement": "Evt deux", "impact": "", "gravite": 2,
             "justification_gravite": "", "extraits": []}]}, ensure_ascii=False)
    return json.dumps({"socle_securite": []}, ensure_ascii=False)


def _llm_2vm_cross_vm_er(prompt, format=None):
    p = prompt.lower()
    if "valeurs métier" in p:
        return json.dumps(_VM2_JSON, ensure_ascii=False)
    if "redout" in p:
        return json.dumps({"evenements_redoutes": [
            {"valeur_metier": "Alpha", "evenement": "Evt un", "impact": "", "gravite": 2,
             "justification_gravite": "", "extraits": []},
            {"valeur_metier": "Beta", "evenement": "Evt trois", "impact": "", "gravite": 2,
             "justification_gravite": "", "extraits": []}]}, ensure_ascii=False)
    return json.dumps({"socle_securite": []}, ensure_ascii=False)


def _merge_llm_vm_none_er_group12(prompt, format=None):
    """N'agit que sur la liste ER (repérée au marqueur « evt ») : groupe [1, 2].
    Sur VM/BS/GAP : ne regroupe rien (0 ou 2 items distincts, aucun doublon)."""
    if "evt" in prompt.lower():
        return json.dumps({"groupes": [[1, 2]]}, ensure_ascii=False)
    return json.dumps({"groupes": []}, ensure_ascii=False)


def test_er_meme_valeur_metier_fusionne():
    study = generate_atelier1("p-1", ["doc-a.md"], llm=_llm_2vm_same_vm_er,
                              gather=lambda d: _CHUNKS_2VM,
                              merge_llm=_merge_llm_vm_none_er_group12)
    EbiosStudy.model_validate(study)
    ers = study["atelier_1"]["evenements_redoutes"]
    assert len(ers) == 1
    assert ers[0]["id"] == "er-1"
    assert any("fusion sémantique" in w.lower() for w in study["warnings"])


def test_er_inter_vm_refuse():
    study = generate_atelier1("p-1", ["doc-a.md"], llm=_llm_2vm_cross_vm_er,
                              gather=lambda d: _CHUNKS_2VM,
                              merge_llm=_merge_llm_vm_none_er_group12)
    EbiosStudy.model_validate(study)
    ers = study["atelier_1"]["evenements_redoutes"]
    assert len(ers) == 2  # refusé : valeurs métier différentes -> inchangé
    warns = " ".join(study["warnings"]).lower()
    assert "fusion sémantique" in warns


def test_fusion_json_illisible_isole_categorie_inchangee():
    def llm_2vm(prompt, format=None):
        p = prompt.lower()
        if "valeurs métier" in p:
            return json.dumps(_VM2_JSON, ensure_ascii=False)
        if "redout" in p:
            return json.dumps({"evenements_redoutes": []}, ensure_ascii=False)
        return json.dumps({"socle_securite": []}, ensure_ascii=False)

    def garbage_merge_llm(prompt, format=None):
        return "pas du json"

    study = generate_atelier1("p-1", ["doc-a.md"], llm=llm_2vm, gather=lambda d: _CHUNKS_2VM,
                              merge_llm=garbage_merge_llm)
    EbiosStudy.model_validate(study)
    vms = study["atelier_1"]["valeurs_metier"]
    assert len(vms) == 2  # catégorie inchangée, isolation
    warns = " ".join(study["warnings"]).lower()
    assert "fusion sémantique" in warns
    assert "valeurs métier" in warns or "valeurs metier" in warns  # catégorie nommée


def test_fusion_flag_off_aucun_appel_merge_llm(monkeypatch):
    monkeypatch.setattr(atelier1_module, "_semantic_merge_enabled", lambda: False)

    def raising_merge_llm(prompt, format=None):
        raise AssertionError("merge_llm ne doit jamais être appelé quand le flag est désactivé")

    study = generate_atelier1("p-1", ["doc-a.md"], llm=fake_llm, gather=lambda d: _CHUNKS,
                              merge_llm=raising_merge_llm)
    EbiosStudy.model_validate(study)  # aucune exception -> flag off a bien coupé l'appel
