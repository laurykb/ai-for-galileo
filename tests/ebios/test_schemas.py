import pytest
from pydantic import ValidationError
from core.ebios.schemas import (
    EbiosStudy, Atelier1, check_references, check_capacity, bounded_control,
    GRAVITE_LABELS, GAP_ECART_CAPACITES,
)
from tests.ebios.fixtures import make_study


def test_roundtrip_valide():
    study = EbiosStudy.model_validate(make_study())
    assert study.schema_version == "1.0"
    assert study.atelier_1.valeurs_metier[0].nature == "processus"
    assert study.model_dump()["atelier_1"]["evenements_redoutes"][0]["gravite"] == 3


def test_gravite_bornee_1_4():
    data = make_study()
    data["atelier_1"]["evenements_redoutes"][0]["gravite"] = 5
    with pytest.raises(ValidationError):
        EbiosStudy.model_validate(data)
    assert GRAVITE_LABELS == {1: "Mineure", 2: "Significative", 3: "Grave", 4: "Critique"}


def test_etat_application_enum():
    data = make_study()
    data["atelier_1"]["socle_securite"][0]["etat_application"] = "Inconnu"
    with pytest.raises(ValidationError):
        EbiosStudy.model_validate(data)


def test_references_internes():
    data = make_study()
    data["atelier_1"]["evenements_redoutes"][0]["valeur_metier_id"] = "vm-999"
    errs = check_references(EbiosStudy.model_validate(data))
    assert any("vm-999" in e for e in errs)
    assert check_references(EbiosStudy.model_validate(make_study())) == []


def test_capacites():
    data = make_study()
    a1 = data["atelier_1"]
    vm = a1["valeurs_metier"][0]
    a1["valeurs_metier"] = [dict(vm, id=f"vm-{i}") for i in range(1, 7)]  # 6 > 5
    errs = check_capacity(EbiosStudy.model_validate(data).atelier_1)
    assert any("valeurs" in e.lower() for e in errs)
    assert GAP_ECART_CAPACITES == [2, 2, 1]


def test_capacite_ecarts_gap():
    data = make_study()
    gap = data["atelier_1"]["socle_securite"][0]
    gap["ecarts"] = [dict(gap["ecarts"][0]) for _ in range(3)]  # 3 > bloc 1 (2)
    errs = check_capacity(EbiosStudy.model_validate(data).atelier_1)
    assert any("écart" in e.lower() or "ecart" in e.lower() for e in errs)


def test_bounded_control_signale_preuves_manquantes():
    data = make_study()
    data["atelier_1"]["valeurs_metier"][0]["preuves"] = []
    warns = bounded_control(EbiosStudy.model_validate(data))
    assert any("preuve" in w.lower() for w in warns)
