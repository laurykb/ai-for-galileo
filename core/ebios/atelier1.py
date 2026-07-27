"""Génération du brouillon Atelier 1 : extraction par document, consolidation
déterministe, contrôle borné. Jamais de régénération automatique."""
from __future__ import annotations

import json
import time
from typing import Callable, Optional

from core.ebios.evidence import resolve_preuves
from core.ebios.prompts import (EXTRACT_ER_PROMPT, EXTRACT_GAP_PROMPT, EXTRACT_VM_PROMPT,
                                MERGE_GROUPING_PROMPT)
from core.ebios.schemas import EbiosStudy, GRAVITE_LABELS, bounded_control

_MAX_CHARS_DEFAULT = 24000  # matériau max par appel d'extraction, si env_config indisponible


def _max_extract_chars() -> int:
    try:
        from env_config import EBIOS_MAX_EXTRACT_CHARS
        return EBIOS_MAX_EXTRACT_CHARS
    except ImportError:
        return _MAX_CHARS_DEFAULT


def _semantic_merge_enabled() -> bool:
    try:
        from env_config import EBIOS_SEMANTIC_MERGE
        return EBIOS_SEMANTIC_MERGE
    except ImportError:
        return True


_DOMAINS = (
    ("vm", "ebios.extract.vm", EXTRACT_VM_PROMPT),
    ("er", "ebios.extract.er", EXTRACT_ER_PROMPT),
    ("gap", "ebios.extract.gap", EXTRACT_GAP_PROMPT),
)


def _default_llm(prompt: str, format: Optional[str] = None) -> str:
    from core.model_router import build_llm
    return build_llm("extract").invoke(prompt, format=format)


def _default_merge_llm(prompt: str, format: Optional[str] = None) -> str:
    from core.model_router import build_llm
    return build_llm("synthesize").invoke(prompt, format=format)


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
                      progress: Optional[Callable] = None,
                      merge_llm: Optional[Callable] = None) -> dict:
    from core.prompt_registry import get_prompt

    llm = llm or _default_llm
    gather = gather or _default_gather
    notify = progress or (lambda stage, detail=None: None)

    raw_results: dict[str, list[tuple[str, dict, list[dict]]]] = {"vm": [], "er": [], "gap": []}
    warnings: list[str] = []

    limit = _max_extract_chars()
    for doc in document_ids:
        chunks = gather(doc)
        joined = "\n\n".join(c.get("content", "") for c in chunks)
        text = joined[:limit]
        if len(joined) > limit:
            warnings.append(f"Document tronqué pour l'extraction : {doc} "
                            f"({len(joined)} caractères, limite {limit})")
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

    if _semantic_merge_enabled():
        a1, merge_warnings = _semantic_merge(a1, merge_llm or _default_merge_llm, notify)
        warnings.extend(merge_warnings)

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


# ============================================================================
# Fusion sémantique à la consolidation (Task 9) : le rôle `synthesize` PROPOSE
# des regroupements d'indices sur des quasi-doublons inter-documents (dénominations
# différentes désignant la même réalité) ; l'application (fusion réelle, choix du
# canonique, union des preuves) reste 100 % déterministe côté code. Une seule passe,
# jamais de boucle de réparation, jamais de crash sur une sortie LLM invalide.
# ============================================================================

_MERGE_LABELS = {"vm": "valeurs métier", "bs": "biens supports",
                 "er": "événements redoutés", "gap": "référentiels"}


def _vm_display(vm: dict) -> str:
    return f"{vm['denomination']} — {vm['description']}"


def _bs_display(bs: dict) -> str:
    return f"{bs['denomination']} — {bs['description']}"


def _er_display(er: dict, vm_name_by_id: dict[str, str]) -> str:
    return f"{er['evenement']} (VM: {vm_name_by_id.get(er['valeur_metier_id'], '')})"


def _gap_display(gap: dict) -> str:
    return f"{gap['nom_referentiel']} — {gap['type_referentiel']}"


def _valid_groups(raw_groups, n: int, label: str, warnings: list[str]) -> list[list[int]]:
    """Filtre les groupes du LLM : indices valides (1..n), sans doublon interne, et
    disjoints entre eux (un indice ne peut appartenir qu'à un seul groupe appliqué).
    Tout groupe invalide est ignoré et tracé en warning — jamais de crash."""
    used: set[int] = set()
    valid: list[list[int]] = []
    for g in raw_groups:
        if not isinstance(g, list) or len(g) < 2:
            continue  # rien à fusionner (groupe trivial), pas une erreur
        if not all(isinstance(x, int) and 1 <= x <= n for x in g):
            warnings.append(f"Fusion sémantique ({label}) : groupe ignoré (indice hors limites)")
            continue
        if len(set(g)) != len(g):
            warnings.append(f"Fusion sémantique ({label}) : groupe ignoré (indices dupliqués)")
            continue
        if used & set(g):
            warnings.append(f"Fusion sémantique ({label}) : groupe ignoré (chevauchement avec "
                            "un autre groupe)")
            continue
        used |= set(g)
        valid.append(sorted(g))
    return valid


def _merge_groups_for(merge_llm: Callable, entries_display: list[str], cat: str,
                      warnings: list[str]) -> list[list[int]]:
    """Un seul appel au rôle `synthesize` (s'il y a au moins 2 éléments) -> groupes
    d'indices validés. JSON illisible/non-dict/garbage ou appel en échec -> catégorie
    inchangée + warning (isolation totale, jamais de crash, pas de boucle de retry)."""
    label = _MERGE_LABELS[cat]
    if len(entries_display) < 2:
        return []
    from core.prompt_registry import get_prompt
    numbered = "\n".join(f"{i}. {d}" for i, d in enumerate(entries_display, 1))
    prompt = get_prompt("ebios.merge", MERGE_GROUPING_PROMPT).format(items=numbered)
    try:
        raw = merge_llm(prompt, format="json")
    except Exception:
        warnings.append(f"Fusion sémantique ({label}) : appel LLM en échec — catégorie inchangée")
        return []
    parsed = _parse_json(raw)
    if parsed is None or not isinstance(parsed.get("groupes"), list):
        warnings.append(f"Fusion sémantique ({label}) : réponse illisible — catégorie inchangée")
        return []
    return _valid_groups(parsed["groupes"], len(entries_display), label, warnings)


def _pick_canonical(items: list[tuple[int, dict]], preuve_count: Callable[[dict], int]):
    """Canonique = entrée avec le plus de preuves ; égalité -> la première (index le
    plus bas dans la liste d'origine)."""
    return max(items, key=lambda t: (preuve_count(t[1]), -t[0]))[1]


def _finish_vm_merge(vms: list[dict], groups: list[list[int]],
                     warnings: list[str]) -> tuple[list[dict], dict[str, str]]:
    absorb_map: dict[str, str] = {}  # id absorbé (avant renumérotation) -> id canonique
    to_remove: set[int] = set()
    for idxs in groups:
        items = [(i, vms[i - 1]) for i in idxs]
        canonical = _pick_canonical(items, lambda v: len(v["preuves"]))
        for i, entry in items:
            if entry is canonical:
                continue
            _union_preuves(canonical["preuves"], entry["preuves"])
            warnings.append(f"Fusion sémantique : « {entry['denomination']} » fusionné dans "
                            f"« {canonical['denomination']} »")
            absorb_map[entry["id"]] = canonical["id"]
            to_remove.add(i)
    survivors = [v for i, v in enumerate(vms, 1) if i not in to_remove]
    id_map: dict[str, str] = {}  # id (avant renumérotation) -> id définitif (vm-N)
    for new_i, v in enumerate(survivors, 1):
        id_map[v["id"]] = f"vm-{new_i}"
        v["id"] = f"vm-{new_i}"
    # Résolution transitive (id absorbé -> id canonique -> id définitif). Les groupes
    # étant disjoints il n'y a qu'un seul niveau d'indirection, mais on reste robuste
    # face à des chaînes (garde anti-cycle via `seen`).
    resolved: dict[str, str] = {}
    for old_id in set(absorb_map.keys()) | set(id_map.keys()):
        target = old_id
        seen: set[str] = set()
        while target in absorb_map and target not in seen:
            seen.add(target)
            target = absorb_map[target]
        resolved[old_id] = id_map[target]
    return survivors, resolved


def _remap_vm_refs(bss: list[dict], ers: list[dict], vm_id_map: dict[str, str]) -> None:
    for bs in bss:
        remapped: list[str] = []
        for ref in bs["valeurs_metier_associees"]:
            mapped = vm_id_map.get(ref, ref)
            if mapped not in remapped:
                remapped.append(mapped)
        bs["valeurs_metier_associees"] = remapped
    for er in ers:
        if er["valeur_metier_id"]:
            er["valeur_metier_id"] = vm_id_map.get(er["valeur_metier_id"], er["valeur_metier_id"])


def _finish_bs_merge(bss: list[dict], groups: list[list[int]], warnings: list[str]) -> list[dict]:
    to_remove: set[int] = set()
    for idxs in groups:
        items = [(i, bss[i - 1]) for i in idxs]
        canonical = _pick_canonical(items, lambda b: len(b["preuves"]))
        for i, entry in items:
            if entry is canonical:
                continue
            _union_preuves(canonical["preuves"], entry["preuves"])
            for ref in entry["valeurs_metier_associees"]:
                if ref not in canonical["valeurs_metier_associees"]:
                    canonical["valeurs_metier_associees"].append(ref)
            warnings.append(f"Fusion sémantique : « {entry['denomination']} » fusionné dans "
                            f"« {canonical['denomination']} »")
            to_remove.add(i)
    survivors = [b for i, b in enumerate(bss, 1) if i not in to_remove]
    for new_i, b in enumerate(survivors, 1):
        b["id"] = f"bs-{new_i}"
    return survivors


def _finish_er_merge(ers: list[dict], groups: list[list[int]], warnings: list[str]) -> list[dict]:
    to_remove: set[int] = set()
    for idxs in groups:
        items = [(i, ers[i - 1]) for i in idxs]
        vm_ids = {e["valeur_metier_id"] for _, e in items}
        if len(vm_ids) > 1:
            warnings.append("Fusion sémantique (événements redoutés) : groupe refusé — les "
                            "éléments ne partagent pas la même valeur métier")
            continue
        canonical = _pick_canonical(items, lambda e: len(e["preuves"]))
        for i, entry in items:
            if entry is canonical:
                continue
            _union_preuves(canonical["preuves"], entry["preuves"])
            warnings.append(f"Fusion sémantique : « {entry['evenement']} » fusionné dans "
                            f"« {canonical['evenement']} »")
            to_remove.add(i)
    survivors = [e for i, e in enumerate(ers, 1) if i not in to_remove]
    for new_i, e in enumerate(survivors, 1):
        e["id"] = f"er-{new_i}"
    return survivors


def _gap_preuve_total(gap: dict) -> int:
    return sum(len(ec["preuves"]) for ec in gap["ecarts"])


def _finish_gap_merge(gaps: list[dict], groups: list[list[int]], warnings: list[str]) -> list[dict]:
    to_remove: set[int] = set()
    for idxs in groups:
        items = [(i, gaps[i - 1]) for i in idxs]
        canonical = _pick_canonical(items, _gap_preuve_total)
        canon_ecarts_by_key = {_norm(ec["description"]): ec for ec in canonical["ecarts"]}
        for i, entry in items:
            if entry is canonical:
                continue
            for ec in entry["ecarts"]:
                key = _norm(ec["description"])
                if key in canon_ecarts_by_key:
                    _union_preuves(canon_ecarts_by_key[key]["preuves"], ec["preuves"])
                else:
                    canonical["ecarts"].append(ec)
                    canon_ecarts_by_key[key] = ec
            warnings.append(f"Fusion sémantique : « {entry['nom_referentiel']} » fusionné dans "
                            f"« {canonical['nom_referentiel']} »")
            to_remove.add(i)
    survivors = [g for i, g in enumerate(gaps, 1) if i not in to_remove]
    for new_i, g in enumerate(survivors, 1):
        g["id"] = f"gap-{new_i}"
    return survivors


def _semantic_merge(a1: dict, merge_llm: Callable, notify: Callable) -> tuple[dict, list[str]]:
    """Une passe par catégorie (VM, BS, ER, référentiels GAP), jamais de boucle.
    Application déterministe des groupes proposés par le LLM ; ids réassignés
    séquentiellement après fusion."""
    warnings: list[str] = []

    vms = a1["valeurs_metier"]
    notify("fusion", {"categorie": "vm"})
    vm_groups = _merge_groups_for(merge_llm, [_vm_display(v) for v in vms], "vm", warnings)
    vms, vm_id_map = _finish_vm_merge(vms, vm_groups, warnings)

    bss = a1["biens_supports"]
    ers = a1["evenements_redoutes"]
    _remap_vm_refs(bss, ers, vm_id_map)

    notify("fusion", {"categorie": "bs"})
    bs_groups = _merge_groups_for(merge_llm, [_bs_display(b) for b in bss], "bs", warnings)
    bss = _finish_bs_merge(bss, bs_groups, warnings)

    vm_name_by_id = {v["id"]: v["denomination"] for v in vms}
    notify("fusion", {"categorie": "er"})
    er_groups = _merge_groups_for(merge_llm, [_er_display(e, vm_name_by_id) for e in ers],
                                  "er", warnings)
    ers = _finish_er_merge(ers, er_groups, warnings)

    gaps = a1["socle_securite"]
    notify("fusion", {"categorie": "gap"})
    gap_groups = _merge_groups_for(merge_llm, [_gap_display(g) for g in gaps], "gap", warnings)
    gaps = _finish_gap_merge(gaps, gap_groups, warnings)

    a1["valeurs_metier"] = vms
    a1["biens_supports"] = bss
    a1["evenements_redoutes"] = ers
    a1["socle_securite"] = gaps
    return a1, warnings
