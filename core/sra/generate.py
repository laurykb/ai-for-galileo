"""Orchestration SRA au-dessus du moteur multi-document partagé du projet."""
from __future__ import annotations

import json
import hashlib
import time
from pathlib import Path
from typing import Callable, Optional

from core.sra.schemas import RiskObjectivesTable, SraGenerationConfig

MAP_BATCH_CHARS = 24_000
MAP_BATCH_OVERLAP = 1_500
SRA_MAP_MAX_OUTPUT_TOKENS = 4_096
SRA_ADJUDICATION_MAX_OUTPUT_TOKENS = 4_096
SRA_FINALIZATION_MAX_OUTPUT_TOKENS = 2_048
ADJUDICATION_BATCH_CHARS = 18_000

DEFAULT_TAXONOMY_PATH = Path(__file__).with_name("default_ro_taxonomy.json")
with DEFAULT_TAXONOMY_PATH.open(encoding="utf-8") as _stream:
    _DEFAULT_TAXONOMY = json.load(_stream)
RO_TYPE_TAXONOMY = {row["ro_type_id"]: (row["canonical_type"], row["definition"])
                    for row in _DEFAULT_TAXONOMY["types"]}
CATEGORY_TO_RO_TYPE = dict(_DEFAULT_TAXONOMY.get("aliases") or {})

def _taxonomy(config: SraGenerationConfig) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """Retourne la taxonomie effective; la configuration projet prime sur le défaut."""
    taxonomy = ({row.ro_type_id: (row.canonical_type, row.definition)
                 for row in config.ro_types} if config.ro_types else dict(RO_TYPE_TAXONOMY))
    aliases = {label: type_id for label, type_id in CATEGORY_TO_RO_TYPE.items()
               if type_id in taxonomy}
    aliases.update(config.ro_type_aliases)
    for type_id, (label, _definition) in taxonomy.items():
        aliases[label] = type_id
    return taxonomy, aliases


def _canonical_ro_type(category: str, aliases: Optional[dict[str, str]] = None) -> tuple[str, list[str]]:
    """Ramène une catégorie adjudiquée vers un type fermé; sépare ses instances."""
    normalized = " ".join(category.split())
    base = normalized.split(" (", 1)[0]
    type_id = (aliases or CATEGORY_TO_RO_TYPE).get(base, "")
    instances: list[str] = []
    if type_id and " (" in normalized and normalized.endswith(")"):
        instances = [item.strip() for item in normalized.split(" (", 1)[1][:-1].split(",") if item.strip()]
    return type_id, instances


SRA_QUESTION = (
    "Analyse exhaustive et rigoureuse des catégories d'attaquants visant le système "
    "et de tous leurs objectifs distincts. Pour chaque relation attaquant → objectif, "
    "fournis une caractérisation factuelle et conserve toutes les sources documentaires."
)

MAP_PROMPT = """[RÔLE] Tu es l'analyste SRA chargé d'extraire les Risk Objectives
d'un lot appartenant au document « {document} ».

[CONSIGNES]
- Appuie-toi UNIQUEMENT sur le texte fourni. Zéro invention.
- Recense TOUTES les catégories d'attaquants et TOUS leurs objectifs distincts.
- Une catégorie doit désigner un acteur humain ou organisationnel capable d une action délibérée.
- Exclue les phénomènes naturels, pannes, vulnérabilités, composants, clients et utilisateurs non malveillants.
- Ne confonds pas une capacité, une technique, une vulnérabilité ou une mesure défensive avec un objectif.
- Formule l objectif du point de vue de l attaquant : effet recherché et raison factuelle.
- Une relation sans preuve textuelle exacte est interdite.
- "extraits" contient 1 à 3 citations exactes copiées dans le texte fourni.
- Ne limite jamais artificiellement le nombre de résultats.

Réponds UNIQUEMENT avec ce JSON :
{{"risk_objectives": [{{
  "categorie_attaquant": "...",
  "objectifs_vises": "description et justification factuelles...",
  "extraits": ["citation exacte..."]
}}]}}

[TEXTE]
{text}
"""

ADJUDICATION_PROMPT = """[RÔLE] Tu es l'expert senior qui contrôle les candidats
Risk Objectives avant leur inscription dans la table de Risk Identification SRA.

[DÉFINITION MÉTIER]
Un Risk Objective est une finalité hostile délibérée recherchée par un acteur humain
ou organisationnel contre un système, une mission, ses données ou ses services.

[RÈGLES DE DÉCISION]
- Accepte seulement une relation acteur intentionnel -> effet hostile recherché.
- Rejette les phénomènes naturels, pannes, erreurs involontaires, vulnérabilités,
  composants, technologies, moyens d'attaque et mesures défensives pris pour acteurs.
- Rejette une activité légitime, scientifique, commerciale, diplomatique ou défensive
  lorsqu'aucune finalité hostile n'est établie par la preuve.
- Une capacité, une technique, un test ou un déploiement n'est pas un objectif :
  conserve uniquement la finalité hostile explicitement étayée derrière ce moyen.
- N'infère jamais une intention hostile à partir de la seule possession d'une capacité.
- Produis un objectif atomique par effet final. Sépare les objectifs multiples.
- Normalise la catégorie vers l'une des familles configurées suivantes quand la preuve le permet :
{taxonomy}
- Si aucune famille ne convient, conserve un libellé générique factuel sans inventer
  de nouvelle motivation. Le pipeline décidera si cette catégorie peut être proposée.
- Un État nommé peut être conservé entre parenthèses, par exemple
  "Acteur étatique (Chine)". Ne transforme pas un pays mentionné en attaquant si le
  passage décrit seulement sa politique, sa coopération ou sa défense.
- L'objectif normalisé décrit l'effet recherché, sa cible et sa justification factuelle,
  mais pas une liste de techniques.
- Utilise uniquement les preuves fournies. Zéro invention.

Chaque candidat doit apparaître exactement une fois dans "decisions".
Pour un rejet, "objectifs_atomiques" est vide et "motif_rejet" explique brièvement.
Pour une acceptation, "categorie_normalisee" et "objectifs_atomiques" sont obligatoires.

Réponds UNIQUEMENT avec ce JSON :
{{"decisions": [{{
  "id": 1,
  "accepte": true,
  "categorie_normalisee": "Acteur étatique (Chine)",
  "objectifs_atomiques": ["Priver la mission de ses communications satellitaires."],
  "motif_rejet": "",
  "confiance": "haute"
}}]}}

[CANDIDATS]
{candidates}
"""

FINALIZATION_PROMPT = """Tu es le rédacteur SRA. Reformule en français clair et factuel les
caractérisations de chaque RO Type. Ne change aucun identifiant, type, instance ou preuve.
N'ajoute aucun fait. Réponds uniquement avec le JSON
{{"types":[{{"ro_type_id":"RO-01","characterization":["..."]}}]}}
[RO TYPES]
{types}"""

def _parse_json(raw: object) -> Optional[dict]:
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    start, end = raw.find("{"), raw.rfind("}") + 1
    if 0 <= start < end:
        try:
            value = json.loads(raw[start:end])
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            pass
    return None


def _content_batches(
    chunks: list[dict],
    limit: int = MAP_BATCH_CHARS,
    overlap: int = MAP_BATCH_OVERLAP,
) -> list[str]:
    """Couvre l'intégralité d'un document avec chevauchement entre fenêtres."""
    text = "\n\n".join(
        str(chunk.get("content", "")).strip()
        for chunk in chunks
        if str(chunk.get("content", "")).strip()
    )
    if not text:
        return []
    step = max(1, limit - min(overlap, limit - 1))
    return [text[start:start + limit] for start in range(0, len(text), step)]


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


def _adjudication_batches(candidates: list[dict], limit: int = ADJUDICATION_BATCH_CHARS) -> list[list[tuple[int, dict]]]:
    """Regroupe les candidats sans couper une preuve ni dépasser le contexte."""
    batches: list[list[tuple[int, dict]]] = []
    current: list[tuple[int, dict]] = []
    current_chars = 0
    for candidate_id, candidate in enumerate(candidates, 1):
        size = len(json.dumps(candidate, ensure_ascii=False)) + 40
        if current and current_chars + size > limit:
            batches.append(current)
            current = []
            current_chars = 0
        current.append((candidate_id, candidate))
        current_chars += size
    if current:
        batches.append(current)
    return batches


def _adjudicate_candidates(
    candidates: list[dict], llm: Callable, notify: Callable,
    batch_chars: int = ADJUDICATION_BATCH_CHARS, retries: int = 1,
    taxonomy_labels: Optional[list[str]] = None,
) -> tuple[list[dict], list[str]]:
    """Filtre et normalise les candidats; toute omission est un rejet sûr."""
    accepted: list[dict] = []
    warnings: list[str] = []
    batches = _adjudication_batches(candidates, batch_chars)
    from core.prompt_registry import get_prompt
    adjudication_prompt = get_prompt("sra.ro.adjudication", ADJUDICATION_PROMPT)
    notify("sra_adjudication_start", {
        "candidates": len(candidates), "batch_count": len(batches),
    })
    for batch_index, batch in enumerate(batches, 1):
        payload = [{
            "id": candidate_id,
            "categorie_extraite": candidate.get("attacker_category", ""),
            "objectif_extrait": candidate.get("targeted_objectives", ""),
            "preuve_exacte": candidate.get("evidence", ""),
            "source": candidate.get("source", ""),
        } for candidate_id, candidate in batch]
        def decide(rows):
            try:
                return _parse_json(llm(adjudication_prompt.format(
                    taxonomy="; ".join(taxonomy_labels or
                                       [label for label, _definition in RO_TYPE_TAXONOMY.values()]),
                    candidates=json.dumps(rows, ensure_ascii=False),
                ), format="json")) or {}
            except __import__("core.llm_client", fromlist=["GenerationTruncated"]).GenerationTruncated:
                if len(rows) <= 1:
                    raise
                midpoint = len(rows) // 2
                notify("sra_adjudication_split", {"batch": batch_index,
                    "candidates": len(rows)})
                merged = {"decisions": []}
                for subset in (rows[:midpoint], rows[midpoint:]):
                    part = decide(subset)
                    merged["decisions"].extend(part.get("decisions", []) or [])
                return merged

        parsed = decide(payload)
        decisions = parsed.get("decisions", []) if parsed else []
        by_id = {
            decision.get("id"): decision for decision in decisions
            if isinstance(decision, dict) and isinstance(decision.get("id"), int)
        }
        missing = [item for item in payload if item["id"] not in by_id]
        for retry_index in range(1, retries + 1):
            if not missing:
                break
            notify("sra_adjudication_retry", {
                "batch": batch_index, "attempt": retry_index, "candidates": len(missing),
            })
            retried = decide(missing)
            for decision in (retried.get("decisions", []) if retried else []):
                decision_id = decision.get("id") if isinstance(decision, dict) else None
                if isinstance(decision_id, int):
                    by_id[decision_id] = decision
            missing = [item for item in missing if item["id"] not in by_id]
        accepted_in_batch = 0
        rejected_details: list[dict] = []
        for candidate_id, candidate in batch:
            decision = by_id.get(candidate_id)
            if not decision:
                rejected_details.append({
                    "id": candidate_id, "reason": "décision absente ou invalide",
                })
                continue
            if decision.get("accepte") is not True:
                rejected_details.append({
                    "id": candidate_id,
                    "reason": str(decision.get("motif_rejet", "")).strip()
                    or "rejet métier",
                })
                continue
            category = " ".join(
                str(decision.get("categorie_normalisee", "")).split()
            )
            objectives = decision.get("objectifs_atomiques", [])
            if not category or not isinstance(objectives, list):
                rejected_details.append({
                    "id": candidate_id, "reason": "acceptation incomplète",
                })
                continue
            valid_objective = False
            for objective in objectives:
                objective = " ".join(str(objective).split())
                if not objective:
                    continue
                valid_objective = True
                accepted.append({
                    "attacker_category": category,
                    "targeted_objectives": objective,
                    "evidence": candidate.get("evidence", ""),
                    "source": candidate.get("source", ""),
                    "candidate_id": candidate_id,
                    "confidence": decision.get("confiance", ""),
                })
                accepted_in_batch += 1
            if not valid_objective:
                rejected_details.append({
                    "id": candidate_id, "reason": "aucun objectif atomique valide",
                })
        if len(by_id) < len(batch):
            warnings.append(
                f"Adjudication incomplète lot {batch_index}/{len(batches)} : "
                f"{len(batch) - len(by_id)} candidat(s) rejeté(s) par sécurité"
            )
        notify("sra_adjudication_batch", {
            "batch": batch_index, "batch_count": len(batches),
            "candidates": len(batch),
            "objectives_accepted": accepted_in_batch,
            "candidates_rejected": len(rejected_details),
            "rejections": rejected_details,
        })
    notify("sra_adjudication_done", {
        "candidates": len(candidates),
        "atomic_objectives_accepted": len(accepted),
        "candidates_rejected": len(candidates)
        - len({row["candidate_id"] for row in accepted}),
    })
    return accepted, warnings


def extract_sra_from_document(
    document: str,
    _aspect: str,
    llm: Optional[Callable] = None,
    gather: Optional[Callable] = None,
    progress: Optional[Callable] = None,
    batch_chars: int = MAP_BATCH_CHARS, batch_overlap: int = MAP_BATCH_OVERLAP,
    cancel_event=None,
) -> dict:
    """MAP métier SRA : balayage intégral d'un document, sortie atomique sourcée."""
    if llm is None:
        from core.model_router import build_llm
        llm = build_llm("extract").invoke
    gather = gather or _default_gather
    notify = progress or (lambda stage, detail=None: None)
    from core.prompt_registry import get_prompt
    map_prompt = get_prompt("sra.ro.map", MAP_PROMPT)
    items: list[str] = []
    candidates: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    chunks = gather(document)
    evidence_policy = (
        chunks[0].get("evidence_policy", "custom_gather") if chunks else "no_evidence"
    )
    batches = _content_batches(chunks, batch_chars, batch_overlap)
    total_chars = sum(len(str(chunk.get("content", ""))) for chunk in chunks)
    notify("sra_document_start", {"document": document,
           "batch_count": len(batches), "characters": total_chars,
           "evidence_policy": evidence_policy})
    for batch_index, text in enumerate(batches, 1):
        if cancel_event is not None and cancel_event.is_set():
            from core.llm_client import GenerationCancelled
            raise GenerationCancelled("Génération RO arrêtée par l’utilisateur")
        notify("sra_batch", {"document": document, "batch": batch_index,
               "batch_count": len(batches), "characters": len(text)})
        before = len(items)
        def invoke_segment(segment):
            try:
                return _parse_json(llm(map_prompt.format(
                    document=document, text=segment), format="json")) or {}
            except __import__("core.llm_client", fromlist=["GenerationTruncated"]).GenerationTruncated:
                if len(segment) <= 4_000:
                    raise
                midpoint = len(segment) // 2
                overlap = min(500, midpoint // 4)
                notify("sra_batch_split", {"document": document,
                    "batch": batch_index, "characters": len(segment)})
                merged = {"risk_objectives": []}
                for subtext in (segment[:midpoint + overlap], segment[midpoint - overlap:]):
                    part = invoke_segment(subtext)
                    merged["risk_objectives"].extend(
                        part.get("risk_objectives", []) or [])
                return merged

        parsed = invoke_segment(text)
        if parsed is None:
            continue
        for row in parsed.get("risk_objectives", []) or []:
            if not isinstance(row, dict):
                continue
            category = " ".join(str(row.get("categorie_attaquant", "")).split())
            objective = " ".join(str(row.get("objectifs_vises", "")).split())
            extracts = [
                extract.strip() for extract in (row.get("extraits", []) or [])
                if isinstance(extract, str) and extract.strip() in text
            ]
            for extract in extracts:
                marker = (category.casefold(), objective.casefold(), extract)
                if category and objective and marker not in seen:
                    seen.add(marker)
                    candidates.append({
                        "attacker_category": category,
                        "targeted_objectives": objective,
                        "evidence": extract,
                        "source": document,
                    })
                    items.append(
                        f"CATÉGORIE_ATTAQUANT: {category} | "
                        f"OBJECTIFS_VISÉS: {objective} | PREUVE: «{extract}»"
                    )
        notify("sra_batch_done", {"document": document, "batch": batch_index,
               "batch_count": len(batches), "new_candidates": len(items) - before})
    notify("sra_document_done", {"document": document,
           "batch_count": len(batches), "candidates": len(items)})
    return {
        "document": document, "items": items, "candidates": candidates,
        "raw": "\n".join(items), "evidence_policy": evidence_policy,
    }


def generate_risk_objectives(
    project_id: str,
    document_ids: list[str],
    progress: Optional[Callable] = None,
    extract_llm: Optional[Callable] = None,
    synthesize_llm: Optional[Callable] = None,
    project_llm: Optional[Callable] = None,
    generation_config: Optional[dict] = None,
    gather: Optional[Callable] = None,
    pipeline: Optional[Callable] = None,
    cancel_event=None,
) -> dict:
    """Balaye tout le corpus puis consolide les RO atomiques sans résumé destructif."""
    from core.synthesize_corpus import synthesize_corpus

    config = SraGenerationConfig.model_validate(generation_config or {})
    taxonomy, taxonomy_aliases = _taxonomy(config)
    if extract_llm is None:
        from core.model_router import build_llm
        client = build_llm("extract", model=config.extraction_model,
                           num_predict=SRA_MAP_MAX_OUTPUT_TOKENS)
        extract_llm = lambda prompt, format=None: client.invoke_cancellable(
            prompt, format=format, cancel_event=cancel_event)

    external_notify = progress or (lambda stage, detail=None: None)
    trace: list[dict] = []

    def notify(stage: str, detail=None):
        event = {"stage": stage, **(detail or {})}
        trace.append(event)
        external_notify(stage, detail)

    pipeline = pipeline or synthesize_corpus
    extract = lambda document, aspect: extract_sra_from_document(
        document, aspect, llm=extract_llm, gather=gather, progress=notify,
        batch_chars=config.map_batch_chars, batch_overlap=config.map_batch_overlap,
        cancel_event=cancel_event,
    )
    events = pipeline(
        SRA_QUESTION, aspect=SRA_QUESTION,
        prefilter=lambda _aspect: list(document_ids), extract=extract,
        llm=synthesize_llm, map_only=True,
    )
    result = {}
    for event in events:
        if cancel_event is not None and cancel_event.is_set():
            from core.llm_client import GenerationCancelled
            raise GenerationCancelled("Génération RO arrêtée par l’utilisateur")
        event_type = event.get("type")
        if event_type == "map":
            notify("map", {key: value for key, value in event.items()
                           if key != "type"})
        elif event_type == "stage":
            notify(event.get("stage", "analyse"),
                   {key: value for key, value in event.items() if key != "type"})
        elif event_type == "done":
            result = event.get("result") or {}

    warnings: list[str] = []
    coverage = result.get("document_coverage") or {}
    for document in coverage.get("documents_without_evidence", []) or []:
        warnings.append(f"Aucun RO étayé détecté dans : {document}")

    candidates = [
        candidate
        for extraction in (result.get("extractions") or [])
        for candidate in (extraction.get("candidates") or [])
        if isinstance(candidate, dict)
    ]
    if project_llm is None:
        from core.model_router import build_llm
        client = build_llm("extract", model=config.adjudication_model,
                           num_predict=SRA_ADJUDICATION_MAX_OUTPUT_TOKENS)
        project_llm = lambda prompt, format=None: client.invoke_cancellable(
            prompt, format=format, cancel_event=cancel_event)
    adjudicated, adjudication_warnings = _adjudicate_candidates(
        candidates, project_llm, notify,
        batch_chars=config.adjudication_batch_chars,
        retries=config.adjudication_retries,
        taxonomy_labels=[label for label, _definition in taxonomy.values()],
    )
    warnings.extend(adjudication_warnings)

    consolidated: dict[str, dict] = {}
    ambiguous: list[dict] = []
    for candidate in adjudicated:
        category = " ".join(str(candidate.get("attacker_category", "")).split())
        type_id, instances = _canonical_ro_type(category, taxonomy_aliases)
        source = str(candidate.get("source", "")).strip()
        proof = str(candidate.get("evidence", "")).strip()
        characterization = " ".join(str(candidate.get("targeted_objectives", "")).split())
        if not type_id and config.taxonomy_mode == "extensible":
            base = category.split(" (", 1)[0].strip()
            if " (" in category and category.endswith(")"):
                instances = [item.strip() for item in category.split(" (", 1)[1][:-1].split(",")
                             if item.strip()]
            digest = hashlib.sha256(base.casefold().encode("utf-8")).hexdigest()[:8].upper()
            type_id = f"RO-EXT-{digest}"
            taxonomy[type_id] = (base, "Type proposé à partir des preuves du corpus; validation humaine requise.")
        if not type_id:
            ambiguous.append({
                "extracted_category": category,
                "characterization": characterization,
                "source": source, "evidence": proof,
                "reason": "Catégorie absente de la taxonomie fermée",
            })
            continue
        canonical_type, definition = taxonomy[type_id]
        entry = consolidated.setdefault(type_id, {
            "ro_type_id": type_id, "canonical_type": canonical_type,
            "definition": definition, "observed_instances": [],
            "characterization": [], "evidence": [],
            "review_comment": ("Type découvert hors taxonomie "
                               f"{config.taxonomy_id}@{config.taxonomy_version}; à valider."
                               if type_id.startswith("RO-EXT-") else ""),
        })
        for instance in instances:
            if instance not in entry["observed_instances"]:
                entry["observed_instances"].append(instance)
        if characterization and characterization not in entry["characterization"]:
            entry["characterization"].append(characterization)
        if proof and source and not any(
            item["quote"] == proof and item["source"] == source
            for item in entry["evidence"]
        ):
            entry["evidence"].append({"source": source, "quote": proof})

    rows = [consolidated[type_id] for type_id in taxonomy
            if type_id in consolidated]
    if config.finalization_enabled and rows:
        try:
            from core.model_router import build_llm
            finalizer = build_llm(
                "synthesize", model=config.finalization_model,
                temperature=0.1, think=False,
                num_predict=SRA_FINALIZATION_MAX_OUTPUT_TOKENS,
            )
            payload = [{
                "ro_type_id": row["ro_type_id"],
                "canonical_type": row["canonical_type"],
                "characterization": row["characterization"],
            } for row in rows]
            from core.prompt_registry import get_prompt
            prompt = get_prompt("sra.ro.finalization", FINALIZATION_PROMPT).format(
                types=json.dumps(payload, ensure_ascii=False))
            finalized = _parse_json(finalizer.invoke_cancellable(
                prompt, format="json", cancel_event=cancel_event)) or {}
            by_type = {item.get("ro_type_id"): item for item in finalized.get("types", []) if isinstance(item, dict)}
            for row in rows:
                values = by_type.get(row["ro_type_id"], {}).get("characterization", [])
                if isinstance(values, list) and any(str(value).strip() for value in values):
                    row["characterization"] = [" ".join(str(value).split()) for value in values if str(value).strip()]
            notify("sra_finalization_done", {"model": config.finalization_model, "types": len(rows)})
        except Exception as exc:  # la forme ne doit jamais casser le fond
            warnings.append(f"Finalisation rédactionnelle ignorée : {exc}")
            notify("sra_finalization_failed", {"model": config.finalization_model})
    notify("sra_consolidation", {
        "extracted_candidates": len(candidates),
        "adjudicated_risk_origins": len(adjudicated),
        "ro_types_retained": len(rows),
        "occurrences_merged_into_types": len(adjudicated) - len(rows),
        "ambiguous_candidates": len(ambiguous),
        "warnings": len(warnings),
        "evidence_by_type": rows,
    })
    table = {
        "project_id": project_id,
        "generation_mode": "deep",
        "schema_version": "2.0",
        "source_document_ids": list(document_ids),
        "generation_config": config.model_dump(),
        "risk_origin_types": rows,
        "ambiguous_candidates": ambiguous,
        "risk_objectives": [],
        "warnings": warnings,
        "trace": trace,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    return RiskObjectivesTable.model_validate(table).model_dump()
