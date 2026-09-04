"""Enrichissement déterministe et auditable des scénarios TO.

Ces signaux aident la revue et la consolidation mais ne filtrent jamais un
scénario. Ils restent explicitement marqués ``heuristic_v1`` tant qu'aucun jeu
de référence métier n'est disponible.
"""
from __future__ import annotations

import re

DOMAINS = (
    "supply_chain", "user", "space_segment", "communications", "data",
    "supervision", "ground_segment", "other",
)
FAMILY_LABELS = {
    "ground_segment": "Segment sol et fonctions de mission",
    "space_segment": "Segment spatial et charge utile",
    "supervision": "Supervision, contrôle et conduite",
    "communications": "Liaisons, signaux et réseaux",
    "data": "Données, calculs et archives",
    "user": "Services et équipements utilisateurs",
    "supply_chain": "Chaîne d'approvisionnement et fabrication",
    "other": "Capacité transverse ou non classée",
}
EFFECT_KEYWORDS = {
    "information_compromise": ("exfiltr", "espion", "intercept", "vol ", "confidential", "accès non autorisé"),
    "integrity_or_control": ("altér", "modif", "inject", "falsif", "spoof", "leurr", "replay", "contrôle", "comprom", "corrupt", "manipul"),
    "service_disruption": ("déni", "indispon", "interrom", "perturb", "brouill", "jamm", "dégrad", "sabot", "désactiv", "disrupt"),
    "supply_chain_compromise": ("supply chain", "chaîne d'approvisionnement", "fournisseur", "fabricant", "manufacturer"),
}


def _unique(values):
    return list(dict.fromkeys(value for value in values if value))


def _primary_domain(scenario: dict) -> tuple[str, list[str]]:
    domains = _unique([str(value) for value in scenario.get("target_domains", []) if value in DOMAINS]) or ["other"]
    primary = next((domain for domain in DOMAINS if domain in domains), "other")
    return primary, [domain for domain in domains if domain != primary]


def _effect(scenario: dict) -> str:
    text = " ".join(str(scenario.get(key, "")) for key in ("attack", "objective", "rationale")).casefold()
    scores = {effect: sum(token in text for token in tokens) for effect, tokens in EFFECT_KEYWORDS.items()}
    properties = set(scenario.get("security_properties", []))
    scores["information_compromise"] += 2 * int("confidentiality" in properties)
    scores["integrity_or_control"] += 2 * int(bool(properties & {"integrity", "authenticity", "non_repudiation"}))
    scores["service_disruption"] += 2 * int("availability" in properties)
    scores["supply_chain_compromise"] += 3 * int("supply_chain" in scenario.get("target_domains", []))
    effect = max(scores, key=scores.get)
    return effect if scores[effect] else "other"


def _target_key(names: list[str], family: str) -> str:
    tokens = []
    for name in names:
        compact = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
        if compact:
            tokens.append(compact)
    return family + ":" + (tokens[0] if tokens else "unclassified")


def enrich_scenario(scenario: dict, ro_type_id: str = "", ro_evidence_count: int = 0) -> dict:
    primary, secondary = _primary_domain(scenario)
    rationale = str(scenario.get("rationale", ""))
    text = " ".join((str(scenario.get("attack", "")), str(scenario.get("objective", "")), rationale)).casefold()
    access = 1.0 if any(token in text for token in ("accès", "interne", "distant", "physique", "supply", "interface", "liaison")) else 0.45
    capability = 0.9 if ro_type_id in {"RO-01", "RO-08"} else (0.65 if len(rationale) >= 80 else 0.4)
    motivations = [value for value in scenario.get("motivations", []) if value != "other"]
    motivation = 0.85 if motivations else 0.4
    target_proofs = len(scenario.get("evidence", []))
    evidence = min(1.0, 0.25 + 0.25 * target_proofs + 0.1 * ro_evidence_count)
    score = round(0.25 * access + 0.25 * capability + 0.2 * motivation + 0.3 * evidence, 2)
    level = "high" if score >= 0.75 else ("medium" if score >= 0.55 else "low")
    return {
        **scenario,
        "canonical_target_family": FAMILY_LABELS[primary],
        "canonical_target_key": _target_key(list(scenario.get("target_names", [])), primary),
        "primary_target_domain": primary,
        "secondary_target_domains": secondary,
        "effect_category": _effect(scenario),
        "plausibility_score": score,
        "plausibility_level": level,
        "plausibility_method": "heuristic_v1",
        "plausibility_factors": {
            "access_position": access, "capability": capability,
            "motivation": motivation, "evidence": evidence,
        },
    }
