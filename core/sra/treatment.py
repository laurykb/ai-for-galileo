"""Initialisation sans invention du plan de traitement depuis les TO revus."""
from __future__ import annotations

import time

from core.sra.schemas import TreatmentPlan

OUTCOMES = {
    "information_compromise": "Préserver la confidentialité et limiter l'exposition des informations.",
    "integrity_or_control": "Préserver l'intégrité, l'authenticité et la maîtrise des fonctions ciblées.",
    "service_disruption": "Maintenir la disponibilité et la continuité des capacités ciblées.",
    "supply_chain_compromise": "Préserver la confiance dans les fournisseurs, composants et livraisons.",
    "other": "Définir le résultat de sécurité attendu lors de la revue métier.",
}


def initialize_treatment_plan(project_id: str, decision_table: dict) -> dict:
    if not decision_table or not decision_table.get("classified_objectives"):
        raise ValueError("Une table TO décisionnelle est requise")
    items = []
    for index, row in enumerate(decision_table["classified_objectives"], 1):
        if row.get("review_status") == "rejected":
            continue
        items.append({
            "treatment_id": f"TR-{index:03d}",
            "classification_id": row["classification_id"],
            "ro_type_id": row["ro_type_id"],
            "target_objective": row["objective_summary"],
            "expected_security_outcome": OUTCOMES.get(row.get("effect_category"), OUTCOMES["other"]),
            "target_names": row.get("target_names", []),
            "security_properties": row.get("security_properties", []),
            "measures": [], "owner": "", "due_date": "",
            "implementation_status": "to_define",
        })
    return TreatmentPlan.model_validate({
        "project_id": project_id,
        "source_to_revision_id": decision_table.get("source_to_revision_id", ""),
        "source_decision_generated_at": decision_table.get("generated_at", ""),
        "items": items, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }).model_dump()
