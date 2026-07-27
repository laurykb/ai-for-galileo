import hashlib
import shutil
import pytest

openpyxl = pytest.importorskip("openpyxl")

from core.ebios.exporters.xlsx import (
    export_xlsx, TemplateIntegrityError, TEMPLATE_SHA256, common_responsable,
)
from core.ebios.schemas import EbiosStudy
from tests.ebios.fixtures import make_study

TEMPLATE = "/home/marsattacks/Téléchargements/EBIOS_RM_Ateliers_modele(1).xlsx"


@pytest.fixture()
def out(tmp_path):
    return tmp_path / "export.xlsx"


def _load(path):
    return openpyxl.load_workbook(path, data_only=False)


def _merges(ws):
    return {str(m) for m in ws.merged_cells.ranges}


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
    assert gap["A4"].value == "Réglementation"           # gap-2 (0 écart) -> ligne 4


def test_modele_source_intact_et_structure_preservee(out):
    before = hashlib.sha256(open(TEMPLATE, "rb").read()).hexdigest()
    export_xlsx(make_study(), out, template_path=TEMPLATE)
    assert hashlib.sha256(open(TEMPLATE, "rb").read()).hexdigest() == before == TEMPLATE_SHA256
    wb_t, wb_o = _load(TEMPLATE), _load(out)
    assert wb_o.sheetnames == wb_t.sheetnames                      # 15 feuilles, même ordre
    # La feuille GAP est reconstruite dynamiquement (fusions figées retirées) ;
    # toutes les autres feuilles gardent EXACTEMENT les fusions du gabarit.
    for name in wb_t.sheetnames:
        if name == "Atelier 1 - GAP":
            continue
        assert _merges(wb_o[name]) == _merges(wb_t[name])
    assert set(wb_o.defined_names) == set(wb_t.defined_names)      # #REF! non réparés
    # GAP : les fusions d'en-tête (ligne 1) survivent, seule la zone de données bouge.
    assert {"A1:B1", "C1:D1"} <= _merges(wb_o["Atelier 1 - GAP"])
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


def test_injection_formule_forcee_en_texte_colonne_etendue(out):
    """Une cellule au-delà des ancres d'origine (6e VM, colonne O) reste protégée."""
    data = make_study()
    vm = data["atelier_1"]["valeurs_metier"][0]
    fillers = [dict(vm, id=f"vm-{i}") for i in range(3, 6)]        # vm-3, vm-4, vm-5
    extra = dict(vm, id="vm-6", denomination="=EVIL()")            # 6e VM -> colonne O
    data["atelier_1"]["valeurs_metier"] = data["atelier_1"]["valeurs_metier"] + fillers + [extra]
    export_xlsx(data, out, template_path=TEMPLATE)
    wb = _load(out)
    o2 = wb["Atelier 1 - Missions"]["O2"]
    assert o2.value == "=EVIL()" and o2.data_type == "s"


def test_6_vm_ecrit_colonne_o_et_etend_les_fusions_entete(out):
    data = make_study()
    vm = data["atelier_1"]["valeurs_metier"][0]
    extras = [dict(vm, id=f"vm-{i}", denomination=f"VM {i}") for i in range(3, 7)]
    data["atelier_1"]["valeurs_metier"] = data["atelier_1"]["valeurs_metier"] + extras
    assert len(data["atelier_1"]["valeurs_metier"]) == 6
    export_xlsx(data, out, template_path=TEMPLATE)
    wb = _load(out)
    mi = wb["Atelier 1 - Missions"]
    assert mi["O2"].value == "VM 6"
    assert mi["O3"].value == "processus"
    assert mi["O4"].value == "Pilotage des opérations."
    merges = _merges(mi)
    assert "B1:O1" in merges and "B1:N1" not in merges
    assert "B5:O5" in merges and "B5:N5" not in merges
    assert mi["B1"].value == data["atelier_1"]["mission"]
    assert mi["B5"].value == common_responsable(EbiosStudy.model_validate(data).atelier_1)


def test_9_er_ecrit_lignes_10_et_11(out):
    data = make_study()
    er = data["atelier_1"]["evenements_redoutes"][0]
    extras = [dict(er, id=f"er-{i}", evenement=f"Événement {i}") for i in range(3, 10)]
    data["atelier_1"]["evenements_redoutes"] = data["atelier_1"]["evenements_redoutes"] + extras
    assert len(data["atelier_1"]["evenements_redoutes"]) == 9
    export_xlsx(data, out, template_path=TEMPLATE)
    wb = _load(out)
    er_ws = wb["Atelier 1 - ER"]
    assert er_ws["B10"].value == "Événement 8"
    assert er_ws["B11"].value == "Événement 9"


def test_gap_4_referentiels_fusions_verticales_correctes(out):
    data = make_study()
    a1 = data["atelier_1"]
    ecart = a1["socle_securite"][0]["ecarts"][0]

    def _gap(id_, nom, n_ecarts):
        return {
            "id": id_, "type_referentiel": "Norme", "nom_referentiel": nom,
            "etat_application": "Appliqué",
            "ecarts": [dict(ecart, description=f"{nom} écart {i}") for i in range(n_ecarts)],
        }

    a1["socle_securite"] = [
        _gap("gap-1", "Ref1", 3),
        _gap("gap-2", "Ref2", 0),
        _gap("gap-3", "Ref3", 2),
        _gap("gap-4", "Ref4", 1),
    ]
    export_xlsx(data, out, template_path=TEMPLATE)
    wb = _load(out)
    gap = wb["Atelier 1 - GAP"]
    merges = _merges(gap)

    # Les fusions figées du gabarit ont disparu.
    for rng in ("A3:A4", "A5:A7", "B3:B4", "B5:B7", "C3:C4", "C5:C7", "D5:D6", "E5:E6"):
        assert rng not in merges

    # ref1 (3 écarts) -> lignes 3-5, A/B/C fusionnées verticalement
    assert "A3:A5" in merges and "B3:B5" in merges and "C3:C5" in merges
    assert gap["A3"].value == "Norme" and gap["B3"].value == "Ref1"
    assert [gap[f"D{r}"].value for r in (3, 4, 5)] == \
           ["Ref1 écart 0", "Ref1 écart 1", "Ref1 écart 2"]

    # ref2 (0 écart) -> ligne 6 seule, non fusionnée
    assert gap["B6"].value == "Ref2"
    assert not any(m.startswith("A6:") for m in merges)
    assert gap["D6"].value is None

    # ref3 (2 écarts) -> lignes 7-8, fusionnées
    assert "A7:A8" in merges and "B7:B8" in merges and "C7:C8" in merges
    assert gap["B7"].value == "Ref3"
    assert [gap[f"D{r}"].value for r in (7, 8)] == ["Ref3 écart 0", "Ref3 écart 1"]

    # ref4 (1 écart) -> ligne 9 seule, non fusionnée
    assert gap["B9"].value == "Ref4"
    assert not any(m.startswith("A9:") for m in merges)
    assert gap["D9"].value == "Ref4 écart 0"


def test_etude_sur_capacite_exporte_sans_exception(out):
    """Volume qui dépassait toutes les anciennes capacités : plus de refus, l'export
    aboutit et produit un fichier."""
    data = make_study()
    vm = data["atelier_1"]["valeurs_metier"][0]
    bs = data["atelier_1"]["biens_supports"][0]
    er = data["atelier_1"]["evenements_redoutes"][0]
    gap = data["atelier_1"]["socle_securite"][0]
    data["atelier_1"]["valeurs_metier"] = [dict(vm, id=f"vm-{i}") for i in range(1, 21)]
    data["atelier_1"]["biens_supports"] = [dict(bs, id=f"bs-{i}",
                                                valeurs_metier_associees=["vm-1"])
                                           for i in range(1, 31)]
    data["atelier_1"]["evenements_redoutes"] = [dict(er, id=f"er-{i}", valeur_metier_id="vm-1")
                                                for i in range(1, 21)]
    data["atelier_1"]["socle_securite"] = [dict(gap, id=f"gap-{i}") for i in range(1, 11)]
    export_xlsx(data, out, template_path=TEMPLATE)
    assert out.exists()


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
    assert wb["Atelier 1 - GAP"]["D4"].value is None
