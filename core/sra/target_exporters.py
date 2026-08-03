"""Exports de la matrice RO Type vers cibles potentielles."""
import csv
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from core.sra.schemas import TargetObjectivesTable
TO_HEADERS = ("TO ID", "RO Type ID", "RO Type canonique", "Cible potentielle", "Catégorie de cible", "Justification", "Sources et preuves")
def _values(row):
    return (row.to_id, row.ro_type_id, row.ro_canonical_type, row.target_name, row.target_category,
            row.rationale, "\n".join(f"{item.source}: {item.quote}" for item in row.evidence))
def export_target_csv(table: dict, destination: Path) -> None:
    model = TargetObjectivesTable.model_validate(table)
    with destination.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream); writer.writerow(TO_HEADERS)
        for row in model.target_objectives: writer.writerow(_values(row))
def export_target_xlsx(table: dict, destination: Path) -> None:
    model = TargetObjectivesTable.model_validate(table); workbook = Workbook(); sheet = workbook.active
    sheet.title = "TO par RO Type"; sheet.append(TO_HEADERS)
    for row in model.target_objectives: sheet.append(_values(row))
    sheet.freeze_panes = "A2"; sheet.auto_filter.ref = sheet.dimensions
    for column, width in zip("ABCDEFG", (12, 12, 28, 45, 22, 80, 100)): sheet.column_dimensions[column].width = width
    for cell in sheet[1]: cell.font = Font(bold=True)
    for row in sheet.iter_rows(min_row=2):
        for cell in row: cell.alignment = Alignment(wrap_text=True, vertical="top")
    workbook.save(destination)
