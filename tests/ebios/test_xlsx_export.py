import hashlib
import shutil
import pytest

openpyxl = pytest.importorskip("openpyxl")

from core.ebios.exporters.xlsx import (
    export_xlsx, ExportCapacityError, TemplateIntegrityError, TEMPLATE_SHA256, common_responsable,
)
from core.ebios.schemas import EbiosStudy
from tests.ebios.fixtures import make_study

TEMPLATE = "/home/marsattacks/Téléchargements/EBIOS_RM_Ateliers_modele(1).xlsx"


@pytest.fixture()
def out(tmp_path):
    return tmp_path / "export.xlsx"


def _load(path):
    return openpyxl.load_workbook(path, data_only=False)


def test_export_remplit_les_trois_feuilles(out):
    export_xlsx(make_study(), out, template_path=TEMPLATE)
    wb = _load(out)
    mi = wb["Atelier 1 - Missions"]
    assert mi["B1"].value == "Assurer la surveillance spatiale."
    assert mi["B2"].value == "Contrôle mission"
    assert mi["B3"].value == "processus"
    assert mi["G2"].value == "Télémesures"
    assert mi["B5"].value == "Dupont (INTERNE)"          # responsable commun identique
    assert mi["B6"].value == "Segment sol"               # bien support 1
    assert mi["C6"].value == "Liaison RF"
    er = wb["Atelier 1 - ER"]
    assert er["A3"].value == "Contrôle mission"          # dénomination résolue depuis vm-1
    assert er["B3"].value == "Perte du contrôle"
    assert er["D3"].value == 3
    gap = wb["Atelier 1 - GAP"]
    assert gap["A3"].value == "Norme"
    assert gap["C3"].value == "Partiellement appliqué"
    assert gap["D3"].value == "Pas de revue annuelle"
    assert gap["A5"].value == "Réglementation"           # bloc 2, ancre A5


def test_modele_source_intact_et_structure_preservee(out):
    before = hashlib.sha256(open(TEMPLATE, "rb").read()).hexdigest()
    export_xlsx(make_study(), out, template_path=TEMPLATE)
    assert hashlib.sha256(open(TEMPLATE, "rb").read()).hexdigest() == before == TEMPLATE_SHA256
    wb_t, wb_o = _load(TEMPLATE), _load(out)
    assert wb_o.sheetnames == wb_t.sheetnames                      # 15 feuilles, même ordre
    for name in wb_t.sheetnames:
        assert {str(m) for m in wb_o[name].merged_cells.ranges} == \
               {str(m) for m in wb_t[name].merged_cells.ranges}
    assert set(wb_o.defined_names) == set(wb_t.defined_names)      # #REF! non réparés
    # Formules hors Atelier 1 conservées (Atelier 3 - PP, datacartoPP)
    for name in ("Atelier 3 - PP", "datacartoPP"):
        f_t = [(c.coordinate, c.value) for row in wb_t[name].iter_rows() for c in row
               if c.data_type == "f"]
        f_o = [(c.coordinate, c.value) for row in wb_o[name].iter_rows() for c in row
               if c.data_type == "f"]
        assert f_o == f_t


def test_injection_formule_forcee_en_texte(out):
    data = make_study()
    data["atelier_1"]["valeurs_metier"][0]["denomination"] = "=HYPERLINK(\"x\")"
    data["atelier_1"]["evenements_redoutes"][0]["evenement"] = "+DANGER"
    export_xlsx(data, out, template_path=TEMPLATE)
    wb = _load(out)
    b2 = wb["Atelier 1 - Missions"]["B2"]
    assert b2.value == "=HYPERLINK(\"x\")" and b2.data_type == "s"
    b3 = wb["Atelier 1 - ER"]["B3"]
    assert b3.value == "+DANGER" and b3.data_type == "s"


def test_depassement_refuse_sans_tronquer(out):
    data = make_study()
    vm = data["atelier_1"]["valeurs_metier"][0]
    data["atelier_1"]["valeurs_metier"] = [dict(vm, id=f"vm-{i}") for i in range(1, 7)]
    with pytest.raises(ExportCapacityError):
        export_xlsx(data, out, template_path=TEMPLATE)
    assert not out.exists()


def test_sha_template_verifie(tmp_path, out):
    bad = tmp_path / "bad.xlsx"
    shutil.copy(TEMPLATE, bad)
    with open(bad, "ab") as f:
        f.write(b"x")
    with pytest.raises(TemplateIntegrityError):
        export_xlsx(make_study(), out, template_path=bad)


def test_responsable_commun_heterogene(out):
    data = make_study()
    data["atelier_1"]["valeurs_metier"][1]["responsable"]["nom"] = "Durand"
    a1 = EbiosStudy.model_validate(data).atelier_1
    assert common_responsable(a1) == "Dupont (INTERNE) / Durand (INTERNE)"


def test_cellule_sans_valeur_non_remplie(out):
    data = make_study()
    data["atelier_1"]["socle_securite"][1]["ecarts"] = []   # gap-2 sans écart
    export_xlsx(data, out, template_path=TEMPLATE)
    wb = _load(out)
    assert wb["Atelier 1 - GAP"]["D5"].value is None
