"""Génération du brouillon Atelier 1 : extraction par document, consolidation
déterministe, contrôle borné. Jamais de régénération automatique."""
from __future__ import annotations

import json
import time
from typing import Callable, Optional

from core.ebios.evidence import resolve_preuves
from core.ebios.prompts import EXTRACT_ER_PROMPT, EXTRACT_GAP_PROMPT, EXTRACT_VM_PROMPT
from core.ebios.schemas import EbiosStudy, GRAVITE_LABELS, bounded_control

_MAX_CHARS = 24000  # matériau max par appel d'extraction

_DOMAINS = (
    ("vm", "ebios.extract.vm", EXTRACT_VM_PROMPT),
    ("er", "ebios.extract.er", EXTRACT_ER_PROMPT),
    ("gap", "ebios.extract.gap", EXTRACT_GAP_PROMPT),
)


def _default_llm(prompt: str, format: Optional[str] = None) -> str:
    from core.model_router import build_llm
    return build_llm("extract").invoke(prompt, format=format)


def _default_gather(document_id: str) -> list[dict]:
    from utils.mongo import get_db
    cur = get_db()["chunks"].find(
        {"source": document_id},
        {"content": 1, "page_number": 1, "section_idx": 1, "chunk_idx": 1},
    ).sort([("section_idx", 1), ("chunk_idx", 1)])
    return list(cur)


def _parse_json(raw) -> Optional[dict]:
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    start, end = raw.find("{"), raw.rfind("}") + 1
    if 0 <= start < end:
        try:
            parsed = json.loads(raw[start:end])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _norm(s: str) -> str:
    return " ".join((s or "").split()).casefold()


def generate_atelier1(project_id: str, document_ids: list[str],
                      llm: Optional[Callable] = None,
                      gather: Optional[Callable] = None,
                      progress: Optional[Callable] = None) -> dict:
    from core.prompt_registry import get_prompt

    llm = llm or _default_llm
    gather = gather or _default_gather
    notify = progress or (lambda stage, detail=None: None)

    raw_results: dict[str, list[tuple[str, dict, list[dict]]]] = {"vm": [], "er": [], "gap": []}
    warnings: list[str] = []

    for doc in document_ids:
        chunks = gather(doc)
        text = "\n\n".join(c.get("content", "") for c in chunks)[:_MAX_CHARS]
        if not text.strip():
            warnings.append(f"Document sans contenu exploitable : {doc}")
            continue
        for key, registry_key, default_prompt in _DOMAINS:
            notify("extraction", {"document": doc, "domaine": key})
            prompt = get_prompt(registry_key, default_prompt).format(text=text)
            parsed = _parse_json(llm(prompt, format="json"))
            if parsed is None:
                label = {"vm": "valeurs métier", "er": "événements redoutés",
                         "gap": "socle de sécurité"}[key]
                warnings.append(f"Extraction illisible ({label}) pour {doc} — ignorée")
                continue
            raw_results[key].append((doc, parsed, chunks))

    notify("consolidation", {})
    a1, cons_warnings = _consolidate(raw_results)
    warnings.extend(cons_warnings)

    study = {
        "schema_version": "1.0",
        "project_id": project_id,
        "source_document_ids": list(document_ids),
        "atelier_1": a1,
        "warnings": [],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "validated_at": None,
    }
    notify("validation", {})
    model = EbiosStudy.model_validate(study)
    study["warnings"] = warnings + bounded_control(model)
    return study


def _mk_resp(item: dict) -> dict:
    rtype = item.get("responsable_type", "INTERNE")
    if rtype not in ("INTERNE", "EXTERNE"):
        rtype = "INTERNE"
    return {"nom": item.get("responsable_nom", "") or "", "type": rtype}


def _union_preuves(preuves: list[dict], new_preuves: list[dict]) -> None:
    """Fusionne `new_preuves` dans `preuves` en place, sans doublon (document_id, extrait)."""
    seen = {(p["document_id"], p["extrait"]) for p in preuves}
    for p in new_preuves:
        marker = (p["document_id"], p["extrait"])
        if marker not in seen:
            preuves.append(p)
            seen.add(marker)


def _merge(bucket: dict, key: str, entry: dict) -> dict:
    """Dédup déterministe par clé normalisée ; fusion des preuves sans doublon."""
    if key in bucket:
        _union_preuves(bucket[key]["preuves"], entry["preuves"])
        return bucket[key]
    bucket[key] = entry
    return entry


def _consolidate(raw: dict) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    mission = ""
    vms: dict[str, dict] = {}
    bss: dict[str, dict] = {}
    ers: list[dict] = []
    gaps: dict[str, dict] = {}

    for doc, parsed, chunks in raw["vm"]:
        if not mission and parsed.get("mission"):
            mission = str(parsed["mission"]).strip()
        for item in parsed.get("valeurs_metier", []) or []:
            if not isinstance(item, dict):
                continue
            denom = str(item.get("denomination", "")).strip()
            if not denom:
                continue
            preuves, w = resolve_preuves(item.get("extraits", []) or [], doc, chunks)
            warnings.extend(w)
            nature = item.get("nature", "processus")
            if nature not in ("processus", "information"):
                warnings.append(f"Nature invalide « {nature} » pour « {denom} » — "
                                "remplacée par processus")
                nature = "processus"
            _merge(vms, _norm(denom), {
                "id": "", "denomination": denom, "nature": nature,
                "description": str(item.get("description", "")),
                "responsable": _mk_resp(item), "preuves": preuves,
            })
        for item in parsed.get("biens_supports", []) or []:
            if not isinstance(item, dict):
                continue
            denom = str(item.get("denomination", "")).strip()
            if not denom:
                continue
            preuves, w = resolve_preuves(item.get("extraits", []) or [], doc, chunks)
            warnings.extend(w)
            entry = _merge(bss, _norm(denom), {
                "id": "", "denomination": denom,
                "description": str(item.get("description", "")),
                "responsable": _mk_resp(item),
                "valeurs_metier_associees": [], "preuves": preuves,
                "_vm_refs": [],
            })
            entry["_vm_refs"].extend(str(v) for v in item.get("valeurs_metier", []) or [])

    for i, vm in enumerate(vms.values(), 1):
        vm["id"] = f"vm-{i}"
    vm_by_name = {_norm(v["denomination"]): v["id"] for v in vms.values()}

    for i, bs in enumerate(bss.values(), 1):
        bs["id"] = f"bs-{i}"
        refs = []
        for name in bs.pop("_vm_refs"):
            vm_id = vm_by_name.get(_norm(name))
            if vm_id and vm_id not in refs:
                refs.append(vm_id)
            elif not vm_id:
                warnings.append(f"Bien support « {bs['denomination']} » : valeur métier "
                                f"« {name} » non reconnue — association ignorée")
        bs["valeurs_metier_associees"] = refs

    ers_by_key: dict[tuple, dict] = {}
    for doc, parsed, chunks in raw["er"]:
        for item in parsed.get("evenements_redoutes", []) or []:
            if not isinstance(item, dict):
                continue
            evenement = str(item.get("evenement", "")).strip()
            if not evenement:
                continue
            key = (_norm(item.get("valeur_metier", "")), _norm(evenement))
            preuves, w = resolve_preuves(item.get("extraits", []) or [], doc, chunks)
            warnings.extend(w)
            vm_id = vm_by_name.get(_norm(str(item.get("valeur_metier", ""))), "")
            if not vm_id:
                warnings.append(f"ER « {evenement} » : valeur métier "
                                f"« {item.get('valeur_metier', '')} » non reconnue")
            gravite = item.get("gravite")
            if not isinstance(gravite, int) or not 1 <= gravite <= 4:
                warnings.append(f"ER « {evenement} » : gravité invalide « {gravite} » — "
                                f"ramenée à 1 ({GRAVITE_LABELS[1]}), à corriger")
                gravite = 1
            if key in ers_by_key:
                _union_preuves(ers_by_key[key]["preuves"], preuves)
            else:
                ers_by_key[key] = {
                    "id": "", "valeur_metier_id": vm_id,
                    "evenement": evenement, "impact": str(item.get("impact", "")),
                    "gravite": gravite,
                    "justification_gravite": str(item.get("justification_gravite", "")),
                    "preuves": preuves,
                }
    ers = list(ers_by_key.values())
    for i, er in enumerate(ers, 1):
        er["id"] = f"er-{i}"

    for doc, parsed, chunks in raw["gap"]:
        for item in parsed.get("socle_securite", []) or []:
            if not isinstance(item, dict):
                continue
            nom = str(item.get("nom_referentiel", "")).strip()
            if not nom:
                continue
            etat = item.get("etat_application", "Non appliqué")
            if etat not in ("Appliqué", "Partiellement appliqué", "Non appliqué"):
                warnings.append(f"Référentiel « {nom} » : état invalide « {etat} » — "
                                "remplacé par Non appliqué")
                etat = "Non appliqué"
            gap_entry = gaps.setdefault(_norm(nom), {
                "id": "", "type_referentiel": str(item.get("type_referentiel", "")),
                "nom_referentiel": nom, "etat_application": etat,
                "_ecarts_by_key": {},
            })
            for ec in item.get("ecarts", []) or []:
                if not isinstance(ec, dict):
                    continue
                desc = str(ec.get("description", "")).strip()
                if not desc:
                    continue
                preuves, w = resolve_preuves(ec.get("extraits", []) or [], doc, chunks)
                warnings.extend(w)
                ec_key = _norm(desc)
                if ec_key in gap_entry["_ecarts_by_key"]:
                    _union_preuves(gap_entry["_ecarts_by_key"][ec_key]["preuves"], preuves)
                else:
                    gap_entry["_ecarts_by_key"][ec_key] = {
                        "description": desc,
                        "justification": str(ec.get("justification", "")),
                        "preuves": preuves,
                    }
    for i, gap in enumerate(gaps.values(), 1):
        gap["id"] = f"gap-{i}"
        gap["ecarts"] = list(gap.pop("_ecarts_by_key").values())

    a1 = {"mission": mission, "valeurs_metier": list(vms.values()),
          "biens_supports": list(bss.values()), "evenements_redoutes": ers,
          "socle_securite": list(gaps.values())}
    return a1, warnings
