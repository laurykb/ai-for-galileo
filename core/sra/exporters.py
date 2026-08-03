"""Exports CSV et XLSX de la table Risk Objectives."""
import csv
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from core.sra.schemas import RiskObjectivesTable

HEADERS = ("RO Type ID", "RO Type canonique", "Définition", "Instances observées", "Caractérisation", "Sources et preuves")


def export_csv(table: dict, destination: Path) -> None:
    model = RiskObjectivesTable.model_validate(table)
    with destination.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(HEADERS)
        for row in model.risk_origin_types:
            writer.writerow((row.ro_type_id, row.canonical_type, row.definition, "; ".join(row.observed_instances), "\n".join(row.characterization), "\n".join(f"{item.source}: {item.quote}" for item in row.evidence)))
        if not model.risk_origin_types:
            for row in model.risk_objectives:
                writer.writerow(("", row.attacker_category, "", "", row.targeted_objectives, ""))


def export_xlsx(table: dict, destination: Path) -> None:
    model = RiskObjectivesTable.model_validate(table)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "RO Types"
    sheet.append(HEADERS)
    for row in model.risk_origin_types:
        sheet.append((row.ro_type_id, row.canonical_type, row.definition, "; ".join(row.observed_instances), "\n".join(row.characterization), "\n".join(f"{item.source}: {item.quote}" for item in row.evidence)))
    if not model.risk_origin_types:
        for row in model.risk_objectives:
            sheet.append(("", row.attacker_category, "", "", row.targeted_objectives, ""))
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column, width in zip("ABCDEF", (14, 30, 55, 40, 80, 100)):
        sheet.column_dimensions[column].width = width
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    workbook.save(destination)
