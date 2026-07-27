"""Export XLSX déterministe de l'Atelier 1 vers le classeur contractuel EBIOS RM.

Copie le modèle (jamais écrasé, intégrité SHA-256 vérifiée), n'écrit que du texte
forcé (anti-injection de formule) et ÉTEND le classeur au-delà des ancres
d'origine quand le volume dépasse les emplacements pré-fusionnés du gabarit :
le contractuel, c'est la structure des colonnes, pas le nombre de lignes.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import openpyxl
from openpyxl.utils import column_index_from_string, get_column_letter

from core.ebios.schemas import Atelier1, EbiosStudy

TEMPLATE_SHA256 = "752dadf48369d0e135def3a79affccdb455d0a812925e193b4068f75a45501dc"

_SHEET_MISSIONS = "Atelier 1 - Missions"
_SHEET_ER = "Atelier 1 - ER"
_SHEET_GAP = "Atelier 1 - GAP"

# Ancres mesurées sur le classeur réel (fusions vérifiées le 2026-07-27). Elles
# définissent où commence l'extension dynamique, pas une limite : au-delà, on
# continue d'écrire (colonnes/lignes simples, aucune fusion figée à respecter).
_VM_COLS = ("B", "G", "H", "J", "M")                     # lignes 2/3/4, resp. commun B5
_BS_COLS = ("B", "C", "D", "G", "H", "I", "J", "K", "L", "M", "N")  # lignes 6/7/8
_MISSIONS_LAST_COL_IDX = column_index_from_string("N")   # dernière colonne du gabarit (B..N)
_MISSIONS_EXT_START_IDX = _MISSIONS_LAST_COL_IDX + 1     # colonne O : début de l'extension
_ER_FIRST_ROW = 3
_GAP_FIRST_ROW = 3
# Fusions figées du gabarit dans la zone de données GAP (lignes 3-8, colonnes A-E) :
# elles présupposent la répartition d'écarts [2, 2, 1] et doivent disparaître avant
# la mise en page dynamique (référentiels/écarts en nombre quelconque).
_GAP_TEMPLATE_MERGES = ("A3:A4", "A5:A7", "B3:B4", "B5:B7", "C3:C4", "C5:C7", "D5:D6", "E5:E6")


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


def _unmerge_if_present(ws, rng: str) -> None:
    if rng in {str(m) for m in ws.merged_cells.ranges}:
        ws.unmerge_cells(rng)


def _widen_header_merges(ws, last_col_idx: int) -> None:
    """Étend B1:N1 (mission) et B5:N5 (responsable commun) jusqu'à la dernière
    colonne utilisée par l'extension. La valeur reste à l'ancre B1/B5."""
    last_letter = get_column_letter(last_col_idx)
    for row in (1, 5):
        _unmerge_if_present(ws, f"B{row}:N{row}")
        ws.merge_cells(f"B{row}:{last_letter}{row}")


def _write_missions(ws, a1: Atelier1) -> None:
    _set(ws, "B1", a1.mission)
    _set(ws, "B5", common_responsable(a1))

    last_used_idx = _MISSIONS_LAST_COL_IDX
    for i, vm in enumerate(a1.valeurs_metier):
        if i < len(_VM_COLS):
            col = _VM_COLS[i]
        else:
            col_idx = _MISSIONS_EXT_START_IDX + (i - len(_VM_COLS))
            col = get_column_letter(col_idx)
            last_used_idx = max(last_used_idx, col_idx)
        _set(ws, f"{col}2", vm.denomination)
        _set(ws, f"{col}3", vm.nature.value)
        _set(ws, f"{col}4", vm.description)

    for i, bs in enumerate(a1.biens_supports):
        if i < len(_BS_COLS):
            col = _BS_COLS[i]
        else:
            col_idx = _MISSIONS_EXT_START_IDX + (i - len(_BS_COLS))
            col = get_column_letter(col_idx)
            last_used_idx = max(last_used_idx, col_idx)
        _set(ws, f"{col}6", bs.denomination)
        _set(ws, f"{col}7", bs.description)
        _set(ws, f"{col}8", _fmt_resp(bs.responsable))

    if last_used_idx > _MISSIONS_LAST_COL_IDX:
        _widen_header_merges(ws, last_used_idx)


def _write_er(ws, a1: Atelier1) -> None:
    vm_names = {vm.id: vm.denomination for vm in a1.valeurs_metier}
    for offset, er in enumerate(a1.evenements_redoutes):
        row = _ER_FIRST_ROW + offset
        _set(ws, f"A{row}", vm_names.get(er.valeur_metier_id, ""))
        _set(ws, f"B{row}", er.evenement)
        _set(ws, f"C{row}", er.impact)
        _set(ws, f"D{row}", er.gravite)


def _write_gap(ws, a1: Atelier1) -> None:
    for rng in _GAP_TEMPLATE_MERGES:
        _unmerge_if_present(ws, rng)

    row = _GAP_FIRST_ROW
    for gap in a1.socle_securite:
        span = max(len(gap.ecarts), 1)
        _set(ws, f"A{row}", gap.type_referentiel)
        _set(ws, f"B{row}", gap.nom_referentiel)
        _set(ws, f"C{row}", gap.etat_application.value)
        if span > 1:
            last_row = row + span - 1
            ws.merge_cells(f"A{row}:A{last_row}")
            ws.merge_cells(f"B{row}:B{last_row}")
            ws.merge_cells(f"C{row}:C{last_row}")
        for offset, ecart in enumerate(gap.ecarts):
            erow = row + offset
            _set(ws, f"D{erow}", ecart.description)
            _set(ws, f"E{erow}", ecart.justification)
        row += span


def export_xlsx(study: dict, out_path, template_path=None) -> Path:
    model = EbiosStudy.model_validate(study)
    a1 = model.atelier_1

    template = Path(template_path or _default_template_path())
    digest = hashlib.sha256(template.read_bytes()).hexdigest()
    if digest != TEMPLATE_SHA256:
        raise TemplateIntegrityError(
            f"Modèle {template} : SHA-256 {digest} != attendu {TEMPLATE_SHA256}"
        )

    out = Path(out_path)
    shutil.copy(template, out)
    wb = openpyxl.load_workbook(out, data_only=False)

    _write_missions(wb[_SHEET_MISSIONS], a1)
    _write_er(wb[_SHEET_ER], a1)
    _write_gap(wb[_SHEET_GAP], a1)

    wb.save(out)
    return out
