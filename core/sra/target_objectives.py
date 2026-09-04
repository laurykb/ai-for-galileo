"""Croisement des RO Types avec le corpus d'architecture dédié aux cibles."""
from __future__ import annotations
import json
import time
from typing import Callable, Optional
from core.sra.generate import _content_batches, _parse_json
from core.sra.schemas import SraGenerationConfig, TargetObjectivesTable

TARGET_SOURCE_IDS = [
    "GAL-RPT-TASI-SYST-X-0005_6.0_SystBaselDesignDefDocPart1LogArchit(1)-clean.md",
    "GAL-RPT-TASI-SYST-X-0006_v5_content_E-Signed-clean.md",
    "Galileo-OS-SDD_v1.3-clean.md",
]
TO_MAP_MAX_BATCH_CHARS = 24_000
TO_MAP_MAX_OUTPUT_TOKENS = 4_096
TO_ADJUDICATION_MAX_OUTPUT_TOKENS = 4_096
TO_FINALIZATION_MAX_OUTPUT_TOKENS = 4_096
TO_FINALIZATION_MAX_BATCH_CHARS = 6_000
SECURITY_PROPERTIES = {"confidentiality", "integrity", "availability", "authenticity", "non_repudiation"}
TARGET_DOMAINS = {"ground_segment", "space_segment", "supervision", "communications", "data", "user", "supply_chain", "other"}
MOTIVATIONS = {"financial_gain", "espionage", "sabotage", "disruption", "influence", "strategic_advantage", "ideology", "revenge", "other"}

TO_QUESTION = "Recenser exhaustivement les cibles d'attaque Galileo potentielles pour chaque RO Type retenu."
MAP_PROMPT = """[RÔLE] Tu es l'analyste architecture chargé d'identifier les cibles techniques décrites dans « {document} ».
[DÉFINITION]
Une cible est un élément réel du système : service, segment, station, équipement,
fonction, flux, signal, donnée ou interface.
[CONSIGNES]
- Recense exhaustivement les cibles présentes dans le texte fourni.
- Le nom de cible et son type doivent venir du texte; conserve les acronymes et identifiants exacts.
- La preuve atteste l'existence, le rôle ou la criticité de la cible. Copie 1 à 3 citations exactes.
- N'invente aucun composant, service, flux ou propriété technique.
- Écarte titres, documents de référence, rôles administratifs et notions trop vagues comme « système Galileo » si une cible plus précise est disponible.
Réponds UNIQUEMENT avec ce JSON :
{{"cibles":[{{"cible":"GCS","categorie_cible":"segment","description":"rôle factuel dans l'architecture","extraits":["citation exacte"]}}]}}
[TEXTE]
{text}"""
ADJUDICATION_PROMPT = """[RÔLE] Tu construis des scénarios d'attaque SRA à partir
de profils d'attaquants et de cibles architecturales attestées.
[RO TYPES AUTORISÉS]
{risk_origins}
[RÈGLES]
- La cible est un service, segment, station, équipement, fonction, flux, signal, donnée ou interface.
- Rejette documents, sections, acteurs, vulnérabilités, techniques d'attaque et notions génériques sans objet précis.
- Normalise seulement les variantes manifestes d'une même cible; conserve acronymes et identifiants.
- Pour chaque cible acceptée, examine TOUS les RO Types autorisés.
- Caractérise d'abord chaque scénario selon trois axes. Ces axes sont des signaux de
  compatibilité, jamais des exclusions automatiques :
  * propriétés: confidentiality, integrity, availability, authenticity, non_repudiation;
  * domaines: ground_segment, space_segment, supervision, communications, data,
    user, supply_chain, other;
  * motivations: financial_gain, espionage, sabotage, disruption, influence,
    strategic_advantage, ideology, revenge, other.
- Une relation n'est retenue que si tu peux nommer une attaque concrète, son objectif
  et expliquer pourquoi le profil du RO Type permet raisonnablement ce scénario.
- N'utilise jamais une justification générique du type « cible plausible ».
- La citation fournie prouve la cible. La caractérisation et les preuves du RO Type
  fondent la motivation ou la capacité; n'invente aucune vulnérabilité ni accès.
- Une cible acceptée peut avoir zéro, un ou plusieurs scénarios.
- Chaque candidat apparaît exactement une fois.
Réponds UNIQUEMENT avec :
{{"decisions":[{{"id":1,"accepte":true,"cible_normalisee":"Galileo Control System (GCS)","categorie_cible":"segment","scenarios":[{{"ro_type_id":"RO-01","attaque":"Compromission du système de contrôle","objectif":"Altérer le contrôle de la constellation","proprietes_securite":["integrity"],"domaines_cibles":["ground_segment","supervision"],"motivations":["strategic_advantage"],"justification":"Le profil étatique dispose de capacités avancées et poursuit un objectif de perturbation stratégique."}}],"motif_rejet":""}}]}}
[CANDIDATS]
{candidates}"""
FINALIZATION_PROMPT = """[RÔLE] Tu consolides les scénarios d'attaque d'un même RO Type.
[RO TYPE]
{risk_origin}
[RÈGLES]
- Fusionne uniquement les formulations synonymes qui décrivent la même attaque et le même objectif.
- Ne fusionne pas deux mécanismes ou deux objectifs distincts.
- Chaque scenario_id fourni doit apparaître exactement une fois dans scenario_ids.
- N'ajoute aucune attaque, cible, capacité ou vulnérabilité.
- L'attaque et l'objectif consolidés restent précis et actionnables.
Réponds UNIQUEMENT avec :
{{"groupes":[{{"scenario_ids":["TMP-1"],"attaque":"...","objectif":"...","justification":"..."}}]}}
[SCÉNARIOS]
{scenarios}"""

def _ro_payload(risk_origin_types: list[dict]) -> list[dict]:
    return [{"ro_type_id": row.get("ro_type_id", ""), "type_canonique": row.get("canonical_type", ""),
             "definition": row.get("definition", ""),
             "caracterisation": row.get("characterization", []),
             "instances_observees": row.get("observed_instances", []),
             "preuves": row.get("evidence", [])}
            for row in risk_origin_types if row.get("ro_type_id") and row.get("canonical_type")]

def _default_gather(document: str) -> list[dict]:
    from utils.mongo import get_db
    from core.evidence_policy import select_primary_evidence_chunks
    chunks = list(get_db()["chunks"].find(
        {"source": document, "chunk_type": {"$ne": "summary"}},
        {"content": 1, "section_idx": 1, "chunk_idx": 1,
         "quality_status": 1, "quality_reasons": 1, "content_provenance": 1},
    ).sort([("section_idx", 1), ("chunk_idx", 1)]))
    selected, policy = select_primary_evidence_chunks(chunks)
    for chunk in selected:
        chunk["evidence_policy"] = policy
    return selected

def extract_targets_from_document(document: str, _aspect: str, risk_origins: list[dict],
        llm: Callable, gather: Optional[Callable] = None, progress: Optional[Callable] = None,
        batch_chars: int = 24_000, batch_overlap: int = 1_500, cancel_event=None) -> dict:
    gather, notify = gather or _default_gather, progress or (lambda stage, detail=None: None)
    chunks = gather(document); batches = _content_batches(chunks, batch_chars, batch_overlap)
    from core.prompt_registry import get_prompt
    map_prompt = get_prompt("sra.to.map", MAP_PROMPT)
    candidates, items, seen = [], [], set()
    evidence_policy = chunks[0].get("evidence_policy", "custom_gather") if chunks else "no_evidence"
    notify("sra_to_document_start", {"document": document, "batch_count": len(batches),
        "characters": sum(len(str(c.get("content", ""))) for c in chunks),
        "evidence_policy": evidence_policy})
    for index, text in enumerate(batches, 1):
        if cancel_event is not None and cancel_event.is_set():
            from core.llm_client import GenerationCancelled
            raise GenerationCancelled("Génération TO arrêtée par l’utilisateur")
        before = len(candidates); notify("sra_to_batch", {"document": document, "batch": index, "batch_count": len(batches), "characters": len(text)})
        def invoke_segment(segment=text):
            try:
                return _parse_json(llm(map_prompt.format(
                    document=document, text=segment),
                    format="json")) or {}
            except __import__("core.llm_client", fromlist=["GenerationTruncated"]).GenerationTruncated:
                merged = {"cibles": []}
                if len(segment) > 4_000:
                    midpoint = len(segment) // 2
                    overlap = min(500, midpoint // 4)
                    notify("sra_to_batch_split", {"document": document,
                        "batch": index, "characters": len(segment), "split": "text"})
                    for subtext in (segment[:midpoint + overlap], segment[midpoint - overlap:]):
                        part = invoke_segment(subtext)
                        merged["cibles"].extend(part.get("cibles", []) or [])
                    return merged
                raise

        parsed = invoke_segment()
        for row in (parsed.get("cibles", []) if parsed else []):
            if not isinstance(row, dict): continue
            target = " ".join(str(row.get("cible", "")).split())
            category = " ".join(str(row.get("categorie_cible", "")).split())
            description = " ".join(str(row.get("description", "")).split())
            for quote in row.get("extraits", []) or []:
                quote = quote.strip() if isinstance(quote, str) else ""
                marker = (target.casefold(), quote)
                if target and quote and quote in text and marker not in seen:
                    seen.add(marker)
                    candidate = {"target_name": target, "target_category": category,
                        "description": description, "source": document, "evidence": quote}
                    candidates.append(candidate)
                    items.append(f"CIBLE: {target} | PREUVE: «{quote}»")
        notify("sra_to_batch_done", {"document": document, "batch": index, "batch_count": len(batches), "new_candidates": len(candidates)-before})
    notify("sra_to_document_done", {"document": document, "batch_count": len(batches), "candidates": len(candidates)})
    return {"document": document, "items": items, "candidates": candidates, "raw": "\n".join(items)}

def _candidate_key(candidate: dict) -> tuple[str, str]:
    name = " ".join(str(candidate.get("target_name", "")).casefold().split())
    category = " ".join(str(candidate.get("target_category", "")).casefold().split())
    return name, category

def _deduplicate_candidates(candidates: list[dict]) -> list[dict]:
    """Regroupe avant LLM les occurrences exactes et agrège toutes leurs preuves."""
    grouped = {}
    for candidate in candidates:
        key = _candidate_key(candidate)
        if not key[0]:
            continue
        entry = grouped.setdefault(key, {**candidate, "evidence_items": []})
        proof = {"source": candidate.get("source", ""), "quote": candidate.get("evidence", "")}
        if proof["quote"] and proof not in entry["evidence_items"]:
            entry["evidence_items"].append(proof)
    return list(grouped.values())

def _batches(candidates: list[dict], limit: int):
    result, current, size = [], [], 0
    for cid, candidate in enumerate(candidates, 1):
        item_size = len(json.dumps(candidate, ensure_ascii=False)) + 40
        if current and size + item_size > limit: result.append(current); current, size = [], 0
        current.append((cid, candidate)); size += item_size
    if current: result.append(current)
    return result

def _finalize_scenarios(entries: list[dict], ro: dict, llm: Callable,
                        notify: Callable, batch_chars: int) -> tuple[list[dict], list[str]]:
    """Fusionne les synonymes par petits lots, avec repli intégral si le JSON est douteux."""
    if len(entries) < 2:
        return entries, []
    from core.prompt_registry import get_prompt
    finalization_prompt = get_prompt("sra.to.finalization", FINALIZATION_PROMPT)
    indexed = [{"scenario_id": f"TMP-{index}", "attaque": row["attack"],
        "objectif": row["objective"], "justification": row["rationale"],
        "cibles": row["target_names"]} for index, row in enumerate(entries, 1)]
    warnings, output = [], []
    for batch_index, batch in enumerate(_batches(indexed, batch_chars), 1):
        payload = [row for _cid, row in batch]
        by_id = {row["scenario_id"]: entries[int(row["scenario_id"].split("-")[1]) - 1]
                 for row in payload}
        try:
            parsed = _parse_json(llm(finalization_prompt.format(
                risk_origin=json.dumps(ro, ensure_ascii=False),
                scenarios=json.dumps(payload, ensure_ascii=False)), format="json")) or {}
        except Exception:  # la finalisation est une optimisation, jamais une perte de données
            warnings.append(f"Finalisation TO indisponible pour {ro['ro_type_id']} lot {batch_index}; scénarios originaux conservés")
            output.extend(by_id.values()); continue
        groups = parsed.get("groupes") or []
        normalized_groups = []
        for group in groups:
            if not isinstance(group, dict): continue
            normalized_groups.append({"scenario_ids": [str(sid) for sid in group.get("scenario_ids") or []],
                "attack": " ".join(str(group.get("attaque", "")).split()),
                "objective": " ".join(str(group.get("objectif", "")).split()),
                "rationale": " ".join(str(group.get("justification", "")).split())})
        referenced = [sid for group in normalized_groups for sid in group["scenario_ids"]]
        if sorted(referenced) != sorted(by_id) or len(referenced) != len(set(referenced)):
            warnings.append(f"Finalisation TO invalide pour {ro['ro_type_id']} lot {batch_index}; scénarios originaux conservés")
            output.extend(by_id.values()); continue
        if any(not group["attack"] or not group["objective"] or not group["rationale"]
               for group in normalized_groups):
            warnings.append(f"Finalisation TO incomplète pour {ro['ro_type_id']} lot {batch_index}; scénarios originaux conservés")
            output.extend(by_id.values()); continue
        for group in normalized_groups:
            merged = {**by_id[group["scenario_ids"][0]], "attack": group["attack"],
                      "objective": group["objective"], "rationale": group["rationale"],
                      "target_names": [], "target_categories": [], "security_properties": [],
                      "target_domains": [], "motivations": [], "evidence": []}
            for sid in group["scenario_ids"]:
                for field in ("target_names", "target_categories", "security_properties",
                              "target_domains", "motivations", "evidence"):
                    for value in by_id[sid].get(field, []):
                        if value not in merged[field]: merged[field].append(value)
            output.append(merged)
    notify("sra_to_finalization", {"ro_type_id": ro["ro_type_id"],
        "before": len(entries), "after": len(output), "warnings": len(warnings)})
    return output, warnings

def _adjudicate(candidates, ro_types, llm, notify, batch_chars, retries, checkpoint_batches=None, save_progress=None, fallback_llm=None):
    accepted, warnings, issues = [], [], []; checkpoint_batches = checkpoint_batches if checkpoint_batches is not None else {}; batches = _batches(candidates, batch_chars)
    allowed_ro_ids = {row["ro_type_id"] for row in ro_types}
    from core.prompt_registry import get_prompt
    adjudication_prompt = get_prompt("sra.to.adjudication", ADJUDICATION_PROMPT)
    notify("sra_to_adjudication_start", {"candidates": len(candidates), "batch_count": len(batches)})
    for batch_index, batch in enumerate(batches, 1):
        batch_key = str(batch_index)
        if batch_key in checkpoint_batches:
            accepted.extend(checkpoint_batches[batch_key])
            notify("sra_to_adjudication_resumed", {"batch": batch_index, "batch_count": len(batches), "relations": len(checkpoint_batches[batch_key])})
            continue
        accepted_before = len(accepted)
        payload = [{"id": cid, **candidate} for cid, candidate in batch]
        def decide(rows, model=llm, current_ro_types=ro_types):
            try:
                return _parse_json(model(adjudication_prompt.format(
                    risk_origins=json.dumps(current_ro_types, ensure_ascii=False),
                    candidates=json.dumps(rows, ensure_ascii=False)),
                    format="json")) or {}
            except __import__("core.llm_client", fromlist=["GenerationTruncated"]).GenerationTruncated:
                if len(rows) > 1:
                    midpoint = len(rows) // 2
                    notify("sra_to_adjudication_split", {"batch": batch_index,
                        "split": "candidates", "candidates": len(rows),
                        "ro_types": len(current_ro_types)})
                    merged = {"decisions": []}
                    for subset in (rows[:midpoint], rows[midpoint:]):
                        part = decide(subset, model, current_ro_types)
                        merged["decisions"].extend(part.get("decisions", []) or [])
                    return merged
                if len(current_ro_types) > 1:
                    midpoint = len(current_ro_types) // 2
                    groups = (current_ro_types[:midpoint], current_ro_types[midpoint:])
                    notify("sra_to_adjudication_split", {"batch": batch_index,
                        "split": "ro_types", "candidates": len(rows),
                        "ro_types": len(current_ro_types),
                        "groups": [[ro["ro_type_id"] for ro in group] for group in groups]})
                    parts = [decide(rows, model, group) for group in groups]
                    merged_decisions = []
                    for row in rows:
                        decisions = [next((item for item in part.get("decisions", [])
                            if isinstance(item, dict) and item.get("id") == row["id"]), None)
                            for part in parts]
                        # Une omission dans un sous-groupe garde le candidat rejouable :
                        # aucune couverture partielle ne doit masquer une décision absente.
                        if any(decision is None for decision in decisions):
                            continue
                        accepted_parts = [decision for decision in decisions
                                          if decision.get("accepte") is True]
                        reference = accepted_parts[0] if accepted_parts else decisions[0]
                        merged_decisions.append({**reference,
                            "accepte": bool(accepted_parts),
                            "scenarios": [scenario for decision in accepted_parts
                                          for scenario in (decision.get("scenarios") or [])],
                            "motif_rejet": " · ".join(str(decision.get("motif_rejet", "")).strip()
                                                     for decision in decisions if decision.get("motif_rejet"))})
                    return {"decisions": merged_decisions}
                notify("sra_to_adjudication_truncated", {"batch": batch_index,
                    "candidate_id": rows[0].get("id") if rows else None,
                    "ro_type_id": current_ro_types[0].get("ro_type_id") if current_ro_types else None})
                return {"decisions": []}
        parsed = decide(payload); by_id = {d.get("id"): d for d in (parsed.get("decisions", []) if parsed else []) if isinstance(d, dict)}
        missing = [row for row in payload if row["id"] not in by_id]
        for attempt in range(1, retries + 1):
            if not missing: break
            notify("sra_to_adjudication_retry", {"batch": batch_index, "attempt": attempt, "candidates": len(missing)})
            # Une réponse valide mais incomplète signale généralement une sortie trop dense.
            # Le réessai réduit la charge au lieu de répéter la requête défaillante.
            for start in range(0, len(missing), 4):
                retry_group = missing[start:start + 4]
                notify("sra_to_adjudication_split", {"batch": batch_index,
                    "split": "missing_candidates", "candidates": len(retry_group),
                    "original_missing": len(missing), "attempt": attempt})
                retried = decide(retry_group, fallback_llm or llm, ro_types)
                for decision in (retried.get("decisions", []) if retried else []):
                    if isinstance(decision, dict): by_id[decision.get("id")] = decision
            # Isole enfin les omissions résiduelles : un candidat ne peut plus être
            # masqué par la longueur de sortie des candidats voisins.
            still_missing = [row for row in missing if row["id"] not in by_id]
            for row in still_missing:
                retried = decide([row], fallback_llm or llm, ro_types)
                for decision in (retried.get("decisions", []) if retried else []):
                    if isinstance(decision, dict): by_id[decision.get("id")] = decision
            missing = [row for row in missing if row["id"] not in by_id]
        rejected = []
        missing_ids = {row["id"] for row in missing}
        for cid, candidate in batch:
            if cid in missing_ids:
                continue
            decision = by_id.get(cid) or {}; target = " ".join(str(decision.get("cible_normalisee", "")).split())
            if decision.get("accepte") is not True or not target:
                rejected.append({"id": cid, "reason": str(decision.get("motif_rejet", "")).strip() or "rejet métier"}); continue
            category = " ".join(str(decision.get("categorie_cible", "")).split()) or candidate["target_category"]
            scenarios = decision.get("scenarios") or []
            invalid_scenarios = 0
            for scenario in scenarios:
                if not isinstance(scenario, dict):
                    invalid_scenarios += 1; continue
                ro_type_id = str(scenario.get("ro_type_id", "")).strip()
                attack = " ".join(str(scenario.get("attaque", "")).split())
                objective = " ".join(str(scenario.get("objectif", "")).split())
                rationale = " ".join(str(scenario.get("justification", "")).split())
                if ro_type_id not in allowed_ro_ids or not attack or not objective or not rationale:
                    invalid_scenarios += 1; continue
                accepted.append({**candidate, "ro_type_id": ro_type_id,
                    "target_name": target, "target_category": category,
                    "attack": attack, "objective": objective, "rationale": rationale,
                    "security_properties": sorted({str(value) for value in scenario.get("proprietes_securite", []) if str(value) in SECURITY_PROPERTIES}),
                    "target_domains": sorted({str(value) for value in scenario.get("domaines_cibles", []) if str(value) in TARGET_DOMAINS}),
                    "motivations": sorted({str(value) for value in scenario.get("motivations", []) if str(value) in MOTIVATIONS})})
            if invalid_scenarios:
                rejected.append({"id": cid, "reason": f"{invalid_scenarios} scénario(s) incomplet(s) ou RO Type invalide"})
        if missing:
            technical = [{"batch": batch_index, "candidate_id": row["id"],
                "target_name": row.get("target_name", ""), "target_category": row.get("target_category", ""),
                "evidence": row.get("evidence_items") or [{"source": row.get("source", ""), "quote": row.get("evidence", "")}],
                "reason": "decision_llm_absente_apres_reessai"} for row in missing]
            issues.extend(technical)
            warnings.append(f"Adjudication TO incomplète lot {batch_index}/{len(batches)} : {len(missing)} candidat(s) écarté(s) techniquement après réessai; détails conservés dans l audit")
            notify("sra_to_adjudication_incomplete", {"batch": batch_index,
                "batch_count": len(batches), "technical_rejections": technical})
        notify("sra_to_adjudication_batch", {"batch": batch_index, "batch_count": len(batches), "candidates": len(batch), "candidates_rejected": len(rejected), "rejections": rejected})
        # Un lot incomplet ne doit jamais être considéré comme repris avec succès :
        # il sera rejoué au prochain lancement avec tous ses candidats et leurs preuves.
        if not missing:
            checkpoint_batches[batch_key] = accepted[accepted_before:]
        else:
            checkpoint_batches.pop(batch_key, None)
        if save_progress:
            save_progress(adjudication_batches=checkpoint_batches, stage="adjudication", complete=False)
    return accepted, warnings, issues

def generate_target_objectives(project_id: str, risk_origin_types: list[dict], document_ids: Optional[list[str]] = None,
        progress: Optional[Callable] = None, extract_llm: Optional[Callable] = None, project_llm: Optional[Callable] = None,
        finalize_llm: Optional[Callable] = None,
        generation_config: Optional[dict] = None, gather: Optional[Callable] = None,
        pipeline: Optional[Callable] = None, cancel_event=None, checkpoint: Optional[dict] = None,
        save_checkpoint: Optional[Callable] = None, update_budget: Optional[Callable] = None) -> dict:
    from core.synthesize_corpus import synthesize_corpus
    config = SraGenerationConfig.model_validate(generation_config or {}); ro_types = _ro_payload(risk_origin_types)
    if not ro_types: raise ValueError("La table RO ne contient aucun RO Type canonique")
    documents = list(document_ids or TARGET_SOURCE_IDS)
    if gather is None:
        from utils.mongo import get_db
        available = {row["_id"] for row in get_db()["chunks"].aggregate([
            {"$match": {"source": {"$in": documents}}}, {"$group": {"_id": "$source"}},
        ])}
        missing = [document for document in documents if document not in available]
        if missing:
            raise ValueError("Corpus TO incomplet; documents non indexés : " + ", ".join(missing))
    if extract_llm is None:
        from core.model_router import build_llm
        client = build_llm("extract", model=config.extraction_model,
                           num_predict=TO_MAP_MAX_OUTPUT_TOKENS)
        extract_llm = lambda prompt, format=None: client.invoke_cancellable(
            prompt, format=format, cancel_event=cancel_event)
    trace, external = [], progress or (lambda stage, detail=None: None)
    checkpoint = checkpoint or {}
    checkpoint_documents = dict(checkpoint.get("documents") or {})
    checkpoint_batches = dict(checkpoint.get("adjudication_batches") or {})
    def notify(stage, detail=None): trace.append({"stage": stage, **(detail or {})}); external(stage, detail)
    def extract(document, aspect):
        if document in checkpoint_documents:
            cached = checkpoint_documents[document]
            notify("sra_to_document_resumed", {"document": document, "candidates": len(cached.get("candidates") or [])})
            return cached
        extracted = extract_targets_from_document(
            document, aspect, ro_types, extract_llm, gather, notify,
            min(config.map_batch_chars, TO_MAP_MAX_BATCH_CHARS),
            min(config.map_batch_overlap, TO_MAP_MAX_BATCH_CHARS // 8), cancel_event)
        checkpoint_documents[document] = extracted
        if save_checkpoint:
            save_checkpoint(stage="map", documents=checkpoint_documents,
                            adjudication_batches=checkpoint_batches, complete=False)
        return extracted
    result = {}
    for event in (pipeline or synthesize_corpus)(TO_QUESTION, aspect=TO_QUESTION, prefilter=lambda _: documents, extract=extract, map_only=True):
        if cancel_event is not None and cancel_event.is_set():
            from core.llm_client import GenerationCancelled
            raise GenerationCancelled("Génération TO arrêtée par l’utilisateur")
        if event.get("type") == "map": notify("map", {k:v for k,v in event.items() if k != "type"})
        elif event.get("type") == "stage": notify(event.get("stage", "analyse"), {k:v for k,v in event.items() if k != "type"})
        elif event.get("type") == "done": result = event.get("result") or {}
    warnings = [f"Aucune cible étayée détectée dans : {doc}" for doc in (result.get("document_coverage") or {}).get("documents_without_evidence", [])]
    raw_candidates = [c for extraction in result.get("extractions", []) for c in extraction.get("candidates", []) if isinstance(c, dict)]
    candidates = _deduplicate_candidates(raw_candidates)
    notify("sra_to_deduplication", {"raw_candidates": len(raw_candidates), "unique_candidates": len(candidates), "duplicates_merged": len(raw_candidates) - len(candidates)})
    if save_checkpoint:
        save_checkpoint(stage="adjudication", documents=checkpoint_documents, candidates=candidates, adjudication_batches=checkpoint_batches, complete=False)
    if update_budget:
        update_budget(sum(len(_content_batches(_default_gather(document) if gather is None else gather(document), min(config.map_batch_chars, TO_MAP_MAX_BATCH_CHARS), min(config.map_batch_overlap, TO_MAP_MAX_BATCH_CHARS // 8))) for document in documents), len(candidates), config.adjudication_batch_chars)
    fallback_llm = None
    if project_llm is None:
        from core.model_router import build_llm
        routine = build_llm("extract", model=config.extraction_model, num_predict=TO_ADJUDICATION_MAX_OUTPUT_TOKENS)
        project_llm = lambda prompt, format=None: routine.invoke_cancellable(prompt, format=format, cancel_event=cancel_event)
        if config.adjudication_model != config.extraction_model:
            fallback = build_llm("extract", model=config.adjudication_model, num_predict=TO_ADJUDICATION_MAX_OUTPUT_TOKENS)
            fallback_llm = lambda prompt, format=None: fallback.invoke_cancellable(prompt, format=format, cancel_event=cancel_event)
    if config.finalization_enabled and finalize_llm is None:
        from core.model_router import build_llm
        finalizer = build_llm("synthesize", model=config.finalization_model,
                              num_predict=TO_FINALIZATION_MAX_OUTPUT_TOKENS)
        finalize_llm = lambda prompt, format=None: finalizer.invoke_cancellable(
            prompt, format=format, cancel_event=cancel_event)
    partial = False
    try:
        accepted, extra, adjudication_issues = _adjudicate(candidates, ro_types, project_llm, notify, config.adjudication_batch_chars, config.adjudication_retries, checkpoint_batches, save_checkpoint, fallback_llm)
    except __import__("utils.task_metrics", fromlist=["TaskBudgetExceeded"]).TaskBudgetExceeded:
        accepted = [row for rows in checkpoint_batches.values() for row in rows]
        extra = ["Budget atteint pendant l adjudication; table partielle sauvegardée et reprise disponible."]
        adjudication_issues = []
        partial = True
    warnings.extend(extra)
    candidate_ro_ids = {row["ro_type_id"] for row in accepted}
    accepted_ro_ids = {row["ro_type_id"] for row in accepted}
    missing_candidate_ro_ids = [row["ro_type_id"] for row in ro_types if row["ro_type_id"] not in candidate_ro_ids]
    missing_retained_ro_ids = [row["ro_type_id"] for row in ro_types if row["ro_type_id"] not in accepted_ro_ids]
    if missing_candidate_ro_ids:
        warnings.append("Aucune cible candidate étayée pour : " + ", ".join(missing_candidate_ro_ids))
    elif missing_retained_ro_ids:
        warnings.append("Aucune relation TO retenue après adjudication pour : " + ", ".join(missing_retained_ro_ids))
    ro_by_id = {row["ro_type_id"]: row for row in ro_types}; consolidated = {}
    for row in accepted:
        if not row.get("attack") or not row.get("objective"):
            continue
        key = (row["ro_type_id"], row["attack"].casefold(), row["objective"].casefold())
        entry = consolidated.setdefault(key, {**row, "target_names": [],
            "target_categories": [], "security_properties": [], "target_domains": [],
            "motivations": [], "evidence": []})
        if row["target_name"] not in entry["target_names"]: entry["target_names"].append(row["target_name"])
        if row["target_category"] and row["target_category"] not in entry["target_categories"]: entry["target_categories"].append(row["target_category"])
        for field in ("security_properties", "target_domains", "motivations"):
            for value in row.get(field, []):
                if value not in entry[field]: entry[field].append(value)
        proofs = row.get("evidence_items") or [{"source": row["source"], "quote": row["evidence"]}]
        for proof in proofs:
            if proof not in entry["evidence"]: entry["evidence"].append(proof)
    entries_by_ro = {row["ro_type_id"]: [] for row in ro_types}
    for entry in consolidated.values(): entries_by_ro[entry["ro_type_id"]].append(entry)
    if config.finalization_enabled and finalize_llm:
        for ro in ro_types:
            finalized, finalization_warnings = _finalize_scenarios(
                entries_by_ro[ro["ro_type_id"]], ro, finalize_llm, notify,
                min(config.adjudication_batch_chars, TO_FINALIZATION_MAX_BATCH_CHARS))
            entries_by_ro[ro["ro_type_id"]] = finalized
            warnings.extend(finalization_warnings)
    scenarios_by_ro = {row["ro_type_id"]: [] for row in ro_types}
    scenario_sequence = 1
    final_entries = [entry for entries in entries_by_ro.values() for entry in entries]
    for entry in sorted(final_entries, key=lambda x: (x["ro_type_id"], x["attack"].casefold(), x["objective"].casefold())):
        from core.sra.scenario_enrichment import enrich_scenario
        scenario = {"scenario_id": f"AS-{scenario_sequence:03d}",
            "attack": entry["attack"], "objective": entry["objective"],
            "target_names": entry["target_names"], "target_categories": entry["target_categories"],
            "security_properties": entry.get("security_properties", []),
            "target_domains": entry.get("target_domains", []),
            "motivations": entry.get("motivations", []),
            "rationale": entry["rationale"], "evidence": entry["evidence"]}
        scenarios_by_ro[entry["ro_type_id"]].append(enrich_scenario(
            scenario, entry["ro_type_id"], len(ro_by_id[entry["ro_type_id"]].get("preuves", []))))
        scenario_sequence += 1
    groups = []
    for index, ro in enumerate(ro_types, 1):
        attacks = scenarios_by_ro[ro["ro_type_id"]]
        if not attacks: continue
        groups.append({"to_id": f"TO-{index:03d}", "ro_type_id": ro["ro_type_id"],
            "ro_canonical_type": ro["type_canonique"], "ro_definition": ro["definition"],
            "attacks": attacks, "ro_evidence": ro.get("preuves", [])})
    targets = {name.casefold() for group in groups for scenario in group["attacks"] for name in scenario["target_names"]}
    notify("sra_to_consolidation", {"ro_types": len(ro_types), "extracted_candidates": len(candidates),
        "groups_retained": len(groups), "scenarios_retained": sum(len(group["attacks"]) for group in groups),
        "duplicates_merged": len(accepted)-len(final_entries), "targets": len(targets), "warnings": len(warnings)})
    return TargetObjectivesTable.model_validate({"project_id": project_id, "generation_mode": "deep", "schema_version": "2.0",
        "source_document_ids": documents, "generation_config": config.model_dump(), "target_objectives": groups,
        "adjudication_issues": adjudication_issues,
        "coverage": {"documents_total": len(documents), "documents_with_evidence": len(documents) - len((result.get("document_coverage") or {}).get("documents_without_evidence", [])), "ro_types_total": len(ro_types), "ro_types_with_candidates": len(candidate_ro_ids), "ro_types_retained": len(accepted_ro_ids), "missing_candidate_ro_type_ids": missing_candidate_ro_ids, "missing_retained_ro_type_ids": missing_retained_ro_ids},
        "warnings": warnings, "trace": trace, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "completion_status": "partial" if partial else ("incomplete" if adjudication_issues else "complete"),
        "resume_available": partial or bool(adjudication_issues)}).model_dump()
