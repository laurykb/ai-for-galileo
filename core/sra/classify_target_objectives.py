"""Vue décisionnelle TO, dérivée sans perte de la table exhaustive."""
from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Callable

from core.sra.generate import _parse_json
from core.sra.schemas import ClassifiedTargetObjectivesTable
from core.sra.scenario_enrichment import enrich_scenario

DOMAIN_LABELS = {
    "ground_segment": "Segment sol",
    "space_segment": "Segment spatial",
    "supervision": "Supervision",
    "communications": "Communications",
    "data": "Données",
    "user": "Utilisateurs",
    "supply_chain": "Chaîne d'approvisionnement",
    "other": "Autre",
}

EFFECT_LABELS = {
    "information_compromise": "Compromission d'informations",
    "integrity_or_control": "Altération ou prise de contrôle",
    "service_disruption": "Dégradation ou interruption de service",
    "supply_chain_compromise": "Compromission de la chaîne d'approvisionnement",
    "other": "Autre effet hostile",
}
RO_GROUP_BUDGETS = {
    "RO-01": 3, "RO-02": 1, "RO-03": 1, "RO-04": 1, "RO-05": 1,
    "RO-06": 1, "RO-07": 1, "RO-08": 4, "RO-09": 4, "RO-10": 3,
}
DEFAULT_TOTAL_BUDGET = 20
DOMAIN_PRIORITY = (
    "supply_chain", "user", "space_segment", "communications", "data",
    "supervision", "ground_segment", "other",
)
EFFECT_KEYWORDS = {
    "information_compromise": ("exfiltr", "espion", "intercept", "vol ", "steal", "disclos", "confidential", "accès non autorisé", "unauthorized access"),
    "integrity_or_control": ("altér", "modif", "inject", "falsif", "spoof", "leurr", "replay", "prise de contrôle", "comprom", "corrupt", "manipul", "hijack"),
    "service_disruption": ("déni", "denial", "indispon", "interrom", "perturb", "brouill", "jamm", "dégrad", "sabot", "désactiv", "empêcher", "disrupt"),
    "supply_chain_compromise": ("supply chain", "chaîne d'approvisionnement", "fournisseur", "fabricant", "manufacturer", "production line"),
}

CLASSIFICATION_PROMPT = """[RÔLE]
Tu synthétises un ensemble de scénarios d'attaque déjà validés. Ils appartiennent
tous au même RO Type et au même effet de sécurité recherché.

[RÈGLES]
- Donne un nom générique d'attaque qui couvre fidèlement tous les scénarios.
- Résume les mécanismes d'attaque et leurs objectifs sans inventer de vulnérabilité,
  de capacité, de cible ou de conséquence.
- Ne supprime aucune famille d'attaque substantiellement différente : cite-les dans
  la synthèse même si le nom générique est plus large.
- Réponds uniquement avec ce JSON :
{{"nom_attaque_generique":"...","resume_attaques":"...","resume_objectifs":"..."}}

[RO TYPE]
{ro_type}

[DOMAINE CIBLE]
{domain}

[EFFET DE SÉCURITÉ]
{effect}

[SCÉNARIOS SOURCES]
{scenarios}"""


def _unique(values):
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _fallback(domain: str, scenarios: list[dict], effect: str = "other") -> dict:
    attacks = _unique([str(row.get("attack", "")).strip() for row in scenarios if row.get("attack")])
    objectives = _unique([str(row.get("objective", "")).strip() for row in scenarios if row.get("objective")])
    label = DOMAIN_LABELS.get(domain, domain)
    return {
        "generic_attack_name": f"{EFFECT_LABELS.get(effect, EFFECT_LABELS['other'])} — {label.lower()}",
        "attack_summary": " ; ".join(attacks),
        "objective_summary": " ; ".join(objectives),
    }

SUMMARY_BATCH_CHARS = 18_000

def _summarize(ro: dict, domain: str, scenarios: list[dict], llm: Callable, notify: Callable,
               effect: str = "other") -> dict:
    payload = json.dumps(scenarios, ensure_ascii=False)
    if len(payload) > SUMMARY_BATCH_CHARS and len(scenarios) > 1:
        batches, current, size = [], [], 0
        for scenario in scenarios:
            item_size = len(json.dumps(scenario, ensure_ascii=False))
            if current and size + item_size > SUMMARY_BATCH_CHARS:
                batches.append(current); current, size = [], 0
            current.append(scenario); size += item_size
        if current:
            batches.append(current)
        # La syntaxe JSON ajoute crochets et séparateurs au poids des éléments.
        # Si ce surcoût produit un unique lot toujours trop grand, forcer un vrai split.
        if len(batches) == 1:
            midpoint = len(scenarios) // 2
            batches = [scenarios[:midpoint], scenarios[midpoint:]]
        notify("sra_to_classification_split", {"domain": domain, "scenarios": len(scenarios), "batches": len(batches)})
        partials = [_summarize(ro, domain, batch, llm, notify, effect) for batch in batches]
        scenarios = [{"scenario_id": "MAP-" + str(index), "attaque": item["generic_attack_name"],
                      "objectif": item["objective_summary"], "justification": item["attack_summary"]}
                     for index, item in enumerate(partials, 1)]
        payload = json.dumps(scenarios, ensure_ascii=False)
    parsed = _parse_json(llm(CLASSIFICATION_PROMPT.format(
        ro_type=json.dumps(ro, ensure_ascii=False), domain=domain,
        effect=EFFECT_LABELS.get(effect, effect), scenarios=payload,
    ), format="json")) or {}
    generated = {
        "generic_attack_name": " ".join(str(parsed.get("nom_attaque_generique", "")).split()),
        "attack_summary": " ".join(str(parsed.get("resume_attaques", "")).split()),
        "objective_summary": " ".join(str(parsed.get("resume_objectifs", "")).split()),
    }
    if not all(generated.values()):
        raise ValueError("synthèse incomplète")
    return generated


def _primary_domain(scenario: dict) -> tuple[str, list[str]]:
    domains = _unique([str(value) for value in (scenario.get("target_domains") or ["other"])
                       if str(value) in DOMAIN_LABELS]) or ["other"]
    primary = next((domain for domain in DOMAIN_PRIORITY if domain in domains), "other")
    return primary, [domain for domain in domains if domain != primary]


def _raw_effect(scenario: dict) -> str:
    text = " ".join((str(scenario.get("attack", "")), str(scenario.get("objective", "")),
                     str(scenario.get("rationale", "")))).casefold()
    scores = {effect: sum(keyword in text for keyword in keywords)
              for effect, keywords in EFFECT_KEYWORDS.items()}
    properties = set(scenario.get("security_properties") or [])
    scores["information_compromise"] += 2 * int("confidentiality" in properties)
    scores["integrity_or_control"] += 2 * int(bool(properties & {"integrity", "authenticity", "non_repudiation"}))
    scores["service_disruption"] += 2 * int("availability" in properties)
    scores["supply_chain_compromise"] += 3 * int("supply_chain" in set(scenario.get("target_domains") or []))
    best = max(scores, key=scores.get)
    return best if scores[best] else "other"


def _budget_effect(effect: str, budget: int) -> str:
    if effect == "other":
        effect = "integrity_or_control"
    if budget <= 1:
        return "other"
    if budget == 2:
        return "service_disruption" if effect == "service_disruption" else "integrity_or_control"
    if budget == 3 and effect == "supply_chain_compromise":
        return "integrity_or_control"
    return effect


def classify_target_objectives(project_id: str, source_table: dict, llm: Callable | None = None,
                               source_to_revision_id: str = "",
                               source_ro_revision_id: str = "",
                               progress: Callable | None = None) -> dict:
    """Construit un artefact dérivé sans modifier la table TO exhaustive."""
    if not source_table or not source_table.get("target_objectives"):
        raise ValueError("Une table TO exhaustive est requise")
    if llm is None:
        from core.model_router import build_llm
        client = build_llm("synthesize", num_predict=2048)
        llm = lambda prompt, format=None: client.invoke(prompt, format=format)

    external_notify = progress or (lambda _stage, _detail=None: None)
    trace = []
    def notify(stage, detail=None):
        event = {"stage": stage, **(detail or {})}
        trace.append(event)
        external_notify(stage, detail)
    grouped = defaultdict(list)
    ro_meta = {}
    for relation in source_table.get("target_objectives", []):
        ro_id = str(relation.get("ro_type_id", "")).strip()
        if not ro_id:
            continue
        ro_meta[ro_id] = {
            "ro_canonical_type": relation.get("ro_canonical_type", ""),
            "ro_definition": relation.get("ro_definition", ""),
        }
        budget = RO_GROUP_BUDGETS.get(ro_id, 2)
        for scenario in relation.get("attacks", []) or []:
            enriched = enrich_scenario(
                scenario, ro_id, len(relation.get("ro_evidence", [])))
            grouped[(ro_id, _budget_effect(enriched["effect_category"], budget))].append(enriched)

    rows, warnings = [], []
    ordered = sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1]))
    notify("sra_to_classification_start", {"groups": len(ordered)})
    for index, ((ro_id, effect), scenarios) in enumerate(ordered, 1):
        domain_counts = defaultdict(int)
        for scenario in scenarios:
            domain_counts[scenario["primary_target_domain"]] += 1
        domain = min(domain_counts, key=lambda value: (-domain_counts[value], DOMAIN_PRIORITY.index(value)))
        all_domains = _unique([value for scenario in scenarios for value in
                               [scenario["primary_target_domain"], *scenario["secondary_target_domains"]]])
        secondary_domains = [value for value in all_domains if value != domain]
        compact = [{
            "scenario_id": row.get("scenario_id"), "attaque": row.get("attack"),
            "objectif": row.get("objective"), "cibles": row.get("target_names", []),
            "justification": row.get("rationale", ""),
        } for row in scenarios]
        fallback = _fallback(domain, scenarios, effect)
        try:
            generated = _summarize({"ro_type_id": ro_id, **ro_meta[ro_id]},
                                   domain, compact, llm, notify, effect)
        except Exception as exc:
            generated = fallback
            warnings.append(f"Synthèse déterministe utilisée pour {ro_id}/{effect} : {type(exc).__name__}")
        evidence = _unique([
            (item.get("source", ""), item.get("quote", ""))
            for scenario in scenarios for item in (scenario.get("evidence") or [])
            if item.get("source") and item.get("quote")
        ])
        row = {
            "classification_id": f"TOC-{index:03d}", "ro_type_id": ro_id,
            **ro_meta[ro_id], "target_domain": domain,
            "primary_target_domain": domain, "secondary_target_domains": secondary_domains,
            "effect_category": effect, **generated,
            "scenario_ids": [row.get("scenario_id", "") for row in scenarios],
            "source_scenario_count": len(scenarios),
            "target_names": _unique([name for row in scenarios for name in row.get("target_names", [])]),
            "security_properties": _unique([value for row in scenarios for value in row.get("security_properties", [])]),
            "motivations": _unique([value for row in scenarios for value in row.get("motivations", [])]),
            "evidence": [{"source": source, "quote": quote} for source, quote in evidence],
        }
        rows.append(row)
        notify("sra_to_classification_group", {"index": index, "groups": len(ordered),
            "ro_type_id": ro_id, "effect_category": effect,
            "primary_target_domain": domain, "scenarios": len(scenarios)})
    source_ids = [scenario.get("scenario_id", "") for relation in source_table.get("target_objectives", [])
                  for scenario in (relation.get("attacks") or [])]
    classified_ids = [scenario_id for row in rows for scenario_id in row["scenario_ids"]]
    if len(classified_ids) != len(source_ids) or set(classified_ids) != set(source_ids):
        raise ValueError("Consolidation TO non conservative : couverture des scénarios incomplète")
    if len(rows) > DEFAULT_TOTAL_BUDGET:
        warnings.append(f"Budget décisionnel dépassé : {len(rows)}/{DEFAULT_TOTAL_BUDGET} TO; aucune fusion incompatible forcée")
    result = {
        "project_id": project_id, "schema_version": "2.0",
        "consolidation_strategy": "ro_type_x_security_effect",
        "target_objective_budget": DEFAULT_TOTAL_BUDGET,
        "source_to_revision_id": source_to_revision_id,
        "source_ro_revision_id": source_ro_revision_id,
        "source_to_generated_at": source_table.get("generated_at", ""),
        "classified_objectives": rows, "warnings": warnings, "trace": trace,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    return ClassifiedTargetObjectivesTable.model_validate(result).model_dump()
