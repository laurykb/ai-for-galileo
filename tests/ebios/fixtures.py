import copy

_PREUVE = {"document_id": "doc-a.md", "source": "doc-a.md", "page": 3, "extrait": "extrait cité"}

_STUDY = {
    "schema_version": "1.0",
    "project_id": "p-1",
    "source_document_ids": ["doc-a.md"],
    "atelier_1": {
        "mission": "Assurer la surveillance spatiale.",
        "valeurs_metier": [
            {"id": "vm-1", "denomination": "Contrôle mission", "nature": "processus",
             "description": "Pilotage des opérations.", "responsable": {"nom": "Dupont", "type": "INTERNE"},
             "preuves": [dict(_PREUVE)]},
            {"id": "vm-2", "denomination": "Télémesures", "nature": "information",
             "description": "Données de vol.", "responsable": {"nom": "Dupont", "type": "INTERNE"},
             "preuves": [dict(_PREUVE)]},
        ],
        "biens_supports": [
            {"id": "bs-1", "denomination": "Segment sol", "description": "Stations.",
             "responsable": {"nom": "Martin", "type": "EXTERNE"},
             "valeurs_metier_associees": ["vm-1"], "preuves": [dict(_PREUVE)]},
            {"id": "bs-2", "denomination": "Liaison RF", "description": "Bande X.",
             "responsable": {"nom": "Martin", "type": "EXTERNE"},
             "valeurs_metier_associees": ["vm-1", "vm-2"], "preuves": [dict(_PREUVE)]},
        ],
        "evenements_redoutes": [
            {"id": "er-1", "valeur_metier_id": "vm-1", "evenement": "Perte du contrôle",
             "impact": "Interruption mission", "gravite": 3, "justification_gravite": "Arrêt prolongé",
             "preuves": [dict(_PREUVE)]},
            {"id": "er-2", "valeur_metier_id": "vm-2", "evenement": "Divulgation télémesures",
             "impact": "Atteinte confidentialité", "gravite": 2, "justification_gravite": "",
             "preuves": [dict(_PREUVE)]},
        ],
        "socle_securite": [
            {"id": "gap-1", "type_referentiel": "Norme", "nom_referentiel": "ISO 27001",
             "etat_application": "Partiellement appliqué",
             "ecarts": [{"description": "Pas de revue annuelle", "justification": "Effectif réduit",
                         "preuves": [dict(_PREUVE)]}]},
            {"id": "gap-2", "type_referentiel": "Réglementation", "nom_referentiel": "LPM",
             "etat_application": "Appliqué", "ecarts": []},
        ],
    },
    "warnings": [],
    "generated_at": "2026-07-27T12:00:00",
    "validated_at": None,
}


def make_study(**overrides):
    data = copy.deepcopy(_STUDY)
    data.update(overrides)
    return data
