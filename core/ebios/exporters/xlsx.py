"""Export XLSX déterministe de l'Atelier 1 vers le classeur contractuel EBIOS RM.

Copie le modèle (jamais écrasé, intégrité SHA-256 vérifiée), n'écrit que les
cellules d'ancrage des zones fusionnées, force chaque chaîne en texte
(anti-injection de formule) et refuse tout dépassement de capacité.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import openpyxl

from core.ebios.schemas import Atelier1, EbiosStudy, check_capacity

TEMPLATE_SHA256 = "752dadf48369d0e135def3a79affccdb455d0a812925e193b4068f75a45501dc"

_SHEET_MISSIONS = "Atelier 1 - Missions"
_SHEET_ER = "Atelier 1 - ER"
_SHEET_GAP = "Atelier 1 - GAP"

# Ancres mesurées sur le classeur réel (fusions vérifiées le 2026-07-27).
_VM_COLS = ("B", "G", "H", "J", "M")                     # lignes 2/3/4, resp. commun B5
_BS_COLS = ("B", "C", "D", "G", "H", "I", "J", "K", "L", "M", "N")  # lignes 6/7/8
_ER_FIRST_ROW, _ER_LAST_ROW = 3, 9
_GAP_BLOCKS = (
    {"anchor_row": 3, "ecart_rows": (3, 4)},
    {"anchor_row": 5, "ecart_rows": (5, 7)},   # D5:D6/E5:E6 fusionnées -> 2 ancres
    {"anchor_row": 8, "ecart_rows": (8,)},
)


class ExportCapacityError(ValueError):
    """Le contenu dépasse les capacités du classeur : refus, pas de troncature."""


class TemplateIntegrityError(RuntimeError):
    """Le modèle contractuel ne correspond pas au SHA-256 attendu."""


def _default_template_path() -> str:
    from env_config import EBIOS_TEMPLATE_PATH
    return EBIOS_TEMPLATE_PATH


def _set(ws, coord: str, value) -> None:
    """Écrit une cellule ; les chaînes sont forcées en texte (jamais interprétées)."""
    if value is None or value == "":
        return
    cell = ws[coord]
    cell.value = value
    if isinstance(value, str):
        cell.data_type = "s"


def _fmt_resp(resp) -> str:
    return f"{resp.nom} ({resp.type.value})"


def common_responsable(a1: Atelier1) -> str:
    """Valeur de B5:N5 : commune si identique, sinon noms distincts joints."""
    seen: list[str] = []
    for vm in a1.valeurs_metier:
        label = _fmt_resp(vm.responsable)
        if label not in seen:
            seen.append(label)
    return " / ".join(seen)


def export_xlsx(study: dict, out_path, template_path=None) -> Path:
    model = EbiosStudy.model_validate(study)
    a1 = model.atelier_1
    errs = check_capacity(a1)
    if errs:
        raise ExportCapacityError(" ; ".join(errs))

    template = Path(template_path or _default_template_path())
    digest = hashlib.sha256(template.read_bytes()).hexdigest()
    if digest != TEMPLATE_SHA256:
        raise TemplateIntegrityError(
            f"Modèle {template} : SHA-256 {digest} != attendu {TEMPLATE_SHA256}"
        )

    out = Path(out_path)
    shutil.copy(template, out)
    wb = openpyxl.load_workbook(out, data_only=False)

    ws = wb[_SHEET_MISSIONS]
    _set(ws, "B1", a1.mission)
    for col, vm in zip(_VM_COLS, a1.valeurs_metier):
        _set(ws, f"{col}2", vm.denomination)
        _set(ws, f"{col}3", vm.nature.value)
        _set(ws, f"{col}4", vm.description)
    _set(ws, "B5", common_responsable(a1))
    for col, bs in zip(_BS_COLS, a1.biens_supports):
        _set(ws, f"{col}6", bs.denomination)
        _set(ws, f"{col}7", bs.description)
        _set(ws, f"{col}8", _fmt_resp(bs.responsable))

    ws = wb[_SHEET_ER]
    vm_names = {vm.id: vm.denomination for vm in a1.valeurs_metier}
    for row, er in zip(range(_ER_FIRST_ROW, _ER_LAST_ROW + 1), a1.evenements_redoutes):
        _set(ws, f"A{row}", vm_names.get(er.valeur_metier_id, ""))
        _set(ws, f"B{row}", er.evenement)
        _set(ws, f"C{row}", er.impact)
        _set(ws, f"D{row}", er.gravite)

    ws = wb[_SHEET_GAP]
    for block, gap in zip(_GAP_BLOCKS, a1.socle_securite):
        row = block["anchor_row"]
        _set(ws, f"A{row}", gap.type_referentiel)
        _set(ws, f"B{row}", gap.nom_referentiel)
        _set(ws, f"C{row}", gap.etat_application.value)
        for erow, ecart in zip(block["ecart_rows"], gap.ecarts):
            _set(ws, f"D{erow}", ecart.description)
            _set(ws, f"E{erow}", ecart.justification)

    wb.save(out)
    return out
