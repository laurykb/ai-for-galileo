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
TO_RO_TYPES_PER_PROMPT = 3

TO_QUESTION = "Recenser exhaustivement les cibles d'attaque Galileo potentielles pour chaque RO Type retenu."
MAP_PROMPT = """[RÔLE] Tu es l'analyste SRA chargé de croiser les RO Types retenus avec l'architecture Galileo décrite dans « {document} ».
[RO TYPES RETENUS]
{risk_origins}
[DÉFINITION]
Une cible potentielle est un élément réel du système que le RO Type pourrait chercher à compromettre, espionner, altérer, perturber, contrôler ou détruire : service, segment, station, équipement, fonction, flux, signal, donnée ou interface.
[CONSIGNES]
- Recense exhaustivement les cibles présentes dans le texte fourni.
- Une relation distincte par couple RO Type -> cible.
- Le nom de cible et son type doivent venir du texte; conserve les acronymes et identifiants exacts.
- La preuve atteste l'existence, le rôle ou la criticité de la cible. Copie 1 à 3 citations exactes.
- La justification explique pourquoi ce RO Type pourrait viser cette cible, au regard de son profil fourni.
- N'invente aucun composant, service, flux ou propriété technique.
- Écarte titres, documents de référence, rôles administratifs et notions trop vagues comme « système Galileo » si une cible plus précise est disponible.
Réponds UNIQUEMENT avec ce JSON :
{{"relations":[{{"ro_type_id":"RO-01","cible":"GCS","categorie_cible":"segment","justification":"...","extraits":["citation exacte"]}}]}}
[TEXTE]
{text}"""
ADJUDICATION_PROMPT = """[RÔLE] Tu contrôles des relations RO Type -> cible potentielle pour une analyse SRA.
[RO TYPES AUTORISÉS]
{risk_origins}
[RÈGLES]
- Accepte seulement un RO Type autorisé et une cible système concrète attestée par la citation.
- La cible est un service, segment, station, équipement, fonction, flux, signal, donnée ou interface.
- Rejette documents, sections, acteurs, vulnérabilités, techniques d'attaque et notions génériques sans objet précis.
- Vérifie que la justification du croisement est plausible au regard du profil du RO Type sans inventer de propriété de cible.
- Normalise seulement les variantes manifestes d'une même cible; conserve acronymes et identifiants.
- Chaque candidat apparaît exactement une fois.
Réponds UNIQUEMENT avec :
{{"decisions":[{{"id":1,"accepte":true,"cible_normalisee":"Galileo Control System (GCS)","categorie_cible":"segment","justification":"...","motif_rejet":""}}]}}
[CANDIDATS]
{candidates}"""

def _ro_payload(risk_origin_types: list[dict]) -> list[dict]:
    return [{"ro_type_id": row.get("ro_type_id", ""), "type_canonique": row.get("canonical_type", ""),
             "definition": row.get("definition", "")}
            for row in risk_origin_types if row.get("ro_type_id") and row.get("canonical_type")]

def _default_gather(document: str) -> list[dict]:
    from utils.mongo import get_db
    return list(get_db()["chunks"].find({"source": document, "chunk_type": {"$ne": "summary"}},
        {"content": 1, "section_idx": 1, "chunk_idx": 1}).sort([("section_idx", 1), ("chunk_idx", 1)]))

def extract_targets_from_document(document: str, _aspect: str, risk_origins: list[dict],
        llm: Callable, gather: Optional[Callable] = None, progress: Optional[Callable] = None,
        batch_chars: int = 24_000, batch_overlap: int = 1_500, cancel_event=None) -> dict:
    gather, notify = gather or _default_gather, progress or (lambda stage, detail=None: None)
    chunks = gather(document); batches = _content_batches(chunks, batch_chars, batch_overlap)
    candidates, items, seen = [], [], set(); allowed = {row["ro_type_id"] for row in risk_origins}
    notify("sra_to_document_start", {"document": document, "batch_count": len(batches),
        "characters": sum(len(str(c.get("content", ""))) for c in chunks)})
    for index, text in enumerate(batches, 1):
        if cancel_event is not None and cancel_event.is_set():
            from core.llm_client import GenerationCancelled
            raise GenerationCancelled("Génération TO arrêtée par l’utilisateur")
        before = len(candidates); notify("sra_to_batch", {"document": document, "batch": index, "batch_count": len(batches), "characters": len(text)})
        def invoke_group(group, segment=text):
            group_json = json.dumps(group, ensure_ascii=False)
            try:
                return _parse_json(llm(MAP_PROMPT.format(
                    document=document, risk_origins=group_json, text=segment),
                    format="json")) or {}
            except __import__("core.llm_client", fromlist=["GenerationTruncated"]).GenerationTruncated:
                merged = {"relations": []}
                if len(group) > 1:
                    midpoint = max(1, len(group) // 2)
                    notify("sra_to_batch_split", {"document": document,
                        "batch": index, "ro_types": len(group), "split": "ro_types"})
                    parts = [group[:midpoint], group[midpoint:]]
                    for subgroup in parts:
                        part = invoke_group(subgroup, segment)
                        merged["relations"].extend(part.get("relations", []) or [])
                    return merged
                if len(segment) > 4_000:
                    midpoint = len(segment) // 2
                    overlap = min(500, midpoint // 4)
                    notify("sra_to_batch_split", {"document": document,
                        "batch": index, "characters": len(segment), "split": "text"})
                    for subtext in (segment[:midpoint + overlap], segment[midpoint - overlap:]):
                        part = invoke_group(group, subtext)
                        merged["relations"].extend(part.get("relations", []) or [])
                    return merged
                raise

        parsed = invoke_group(risk_origins)
        for row in (parsed.get("relations", []) if parsed else []):
            if not isinstance(row, dict): continue
            rid = str(row.get("ro_type_id", "")).strip(); target = " ".join(str(row.get("cible", "")).split())
            category = " ".join(str(row.get("categorie_cible", "")).split()); rationale = " ".join(str(row.get("justification", "")).split())
            for quote in row.get("extraits", []) or []:
                quote = quote.strip() if isinstance(quote, str) else ""; marker = (rid, target.casefold(), quote)
                if rid in allowed and target and rationale and quote and quote in text and marker not in seen:
                    seen.add(marker); candidate = {"ro_type_id": rid, "target_name": target, "target_category": category,
                        "rationale": rationale, "source": document, "evidence": quote}
                    candidates.append(candidate); items.append(f"{rid} -> {target} | PREUVE: «{quote}»")
        notify("sra_to_batch_done", {"document": document, "batch": index, "batch_count": len(batches), "new_candidates": len(candidates)-before})
    notify("sra_to_document_done", {"document": document, "batch_count": len(batches), "candidates": len(candidates)})
    return {"document": document, "items": items, "candidates": candidates, "raw": "\n".join(items)}

def _batches(candidates: list[dict], limit: int):
    result, current, size = [], [], 0
    for cid, candidate in enumerate(candidates, 1):
        item_size = len(json.dumps(candidate, ensure_ascii=False)) + 40
        if current and size + item_size > limit: result.append(current); current, size = [], 0
        current.append((cid, candidate)); size += item_size
    if current: result.append(current)
    return result

def _adjudicate(candidates, ro_types, llm, notify, batch_chars, retries):
    accepted, warnings = [], []; ro_json = json.dumps(ro_types, ensure_ascii=False); batches = _batches(candidates, batch_chars)
    notify("sra_to_adjudication_start", {"candidates": len(candidates), "batch_count": len(batches)})
    for batch_index, batch in enumerate(batches, 1):
        payload = [{"id": cid, **candidate} for cid, candidate in batch]
        def decide(rows):
            try:
                return _parse_json(llm(ADJUDICATION_PROMPT.format(
                    risk_origins=ro_json,
                    candidates=json.dumps(rows, ensure_ascii=False)),
                    format="json")) or {}
            except __import__("core.llm_client", fromlist=["GenerationTruncated"]).GenerationTruncated:
                if len(rows) <= 1:
                    raise
                midpoint = len(rows) // 2
                notify("sra_to_adjudication_split", {"batch": batch_index,
                    "candidates": len(rows)})
                merged = {"decisions": []}
                for subset in (rows[:midpoint], rows[midpoint:]):
                    part = decide(subset)
                    merged["decisions"].extend(part.get("decisions", []) or [])
                return merged
        parsed = decide(payload); by_id = {d.get("id"): d for d in (parsed.get("decisions", []) if parsed else []) if isinstance(d, dict)}
        missing = [row for row in payload if row["id"] not in by_id]
        for attempt in range(1, retries + 1):
            if not missing: break
            notify("sra_to_adjudication_retry", {"batch": batch_index, "attempt": attempt, "candidates": len(missing)})
            retried = decide(missing)
            for decision in (retried.get("decisions", []) if retried else []):
                if isinstance(decision, dict): by_id[decision.get("id")] = decision
            missing = [row for row in missing if row["id"] not in by_id]
        rejected = []
        for cid, candidate in batch:
            decision = by_id.get(cid) or {}; target = " ".join(str(decision.get("cible_normalisee", "")).split())
            if decision.get("accepte") is not True or not target:
                rejected.append({"id": cid, "reason": str(decision.get("motif_rejet", "")).strip() or "rejet métier"}); continue
            accepted.append({**candidate, "target_name": target,
                "target_category": " ".join(str(decision.get("categorie_cible", "")).split()) or candidate["target_category"],
                "rationale": " ".join(str(decision.get("justification", "")).split()) or candidate["rationale"]})
        if missing: warnings.append(f"Adjudication TO incomplète lot {batch_index}/{len(batches)} : {len(missing)} candidat(s) rejeté(s) par sécurité")
        notify("sra_to_adjudication_batch", {"batch": batch_index, "batch_count": len(batches), "candidates": len(batch), "candidates_rejected": len(rejected), "rejections": rejected})
    return accepted, warnings

def generate_target_objectives(project_id: str, risk_origin_types: list[dict], document_ids: Optional[list[str]] = None,
        progress: Optional[Callable] = None, extract_llm: Optional[Callable] = None, project_llm: Optional[Callable] = None,
        generation_config: Optional[dict] = None, gather: Optional[Callable] = None,
        pipeline: Optional[Callable] = None, cancel_event=None) -> dict:
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
    def notify(stage, detail=None): trace.append({"stage": stage, **(detail or {})}); external(stage, detail)
    extract = lambda document, aspect: extract_targets_from_document(
        document, aspect, ro_types, extract_llm, gather, notify,
        min(config.map_batch_chars, TO_MAP_MAX_BATCH_CHARS),
        min(config.map_batch_overlap, TO_MAP_MAX_BATCH_CHARS // 8), cancel_event)
    result = {}
    for event in (pipeline or synthesize_corpus)(TO_QUESTION, aspect=TO_QUESTION, prefilter=lambda _: documents, extract=extract, map_only=True):
        if cancel_event is not None and cancel_event.is_set():
            from core.llm_client import GenerationCancelled
            raise GenerationCancelled("Génération TO arrêtée par l’utilisateur")
        if event.get("type") == "map": notify("map", {k:v for k,v in event.items() if k != "type"})
        elif event.get("type") == "stage": notify(event.get("stage", "analyse"), {k:v for k,v in event.items() if k != "type"})
        elif event.get("type") == "done": result = event.get("result") or {}
    warnings = [f"Aucune cible étayée détectée dans : {doc}" for doc in (result.get("document_coverage") or {}).get("documents_without_evidence", [])]
    candidates = [c for extraction in result.get("extractions", []) for c in extraction.get("candidates", []) if isinstance(c, dict)]
    if project_llm is None:
        from core.model_router import build_llm
        client = build_llm("extract", model=config.adjudication_model,
                           num_predict=TO_ADJUDICATION_MAX_OUTPUT_TOKENS)
        project_llm = lambda prompt, format=None: client.invoke_cancellable(
            prompt, format=format, cancel_event=cancel_event)
    accepted, extra = _adjudicate(candidates, ro_types, project_llm, notify, config.adjudication_batch_chars, config.adjudication_retries); warnings.extend(extra)
    ro_by_id = {row["ro_type_id"]: row for row in ro_types}; consolidated = {}
    for row in accepted:
        key = (row["ro_type_id"], row["target_name"].casefold()); entry = consolidated.setdefault(key, {**row, "evidence": []})
        proof = {"source": row["source"], "quote": row["evidence"]}
        if proof not in entry["evidence"]: entry["evidence"].append(proof)
    relations = []
    for index, entry in enumerate(sorted(consolidated.values(), key=lambda x: (x["ro_type_id"], x["target_name"].casefold())), 1):
        relations.append({"to_id": f"TO-{index:03d}", "ro_type_id": entry["ro_type_id"],
            "ro_canonical_type": ro_by_id[entry["ro_type_id"]]["type_canonique"], "target_name": entry["target_name"],
            "target_category": entry["target_category"], "rationale": entry["rationale"], "evidence": entry["evidence"]})
    notify("sra_to_consolidation", {"ro_types": len(ro_types), "extracted_candidates": len(candidates), "relations_retained": len(relations),
        "duplicates_merged": len(accepted)-len(relations), "targets": len({r["target_name"].casefold() for r in relations}), "warnings": len(warnings)})
    return TargetObjectivesTable.model_validate({"project_id": project_id, "generation_mode": "deep", "schema_version": "1.0",
        "source_document_ids": documents, "generation_config": config.model_dump(), "target_objectives": relations,
        "warnings": warnings, "trace": trace, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}).model_dump()
