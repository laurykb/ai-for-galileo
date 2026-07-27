import pytest

from core.ebios.repository import EbiosRepository, VersionConflictError, ValidationBlockedError
from tests.ebios.fixtures import make_study


@pytest.fixture()
def repo(fake_db):
    return EbiosRepository(db=fake_db)


def test_create_et_get_projet(repo):
    p = repo.create_project("Étude spatiale", ["doc-a.md"])
    assert p["project_id"] and p["status"] == "created"
    assert repo.get_project(p["project_id"])["name"] == "Étude spatiale"
    assert [x["name"] for x in repo.list_projects()] == ["Étude spatiale"]


def test_versionnement_incremental_et_statuts(repo):
    p = repo.create_project("E", ["doc-a.md"])
    v1 = repo.save_version(p["project_id"], make_study(), origin="generation",
                           author="pipeline", expected_version=None)
    assert v1["version"] == 1
    assert repo.get_project(p["project_id"])["status"] == "draft_generated"
    v2 = repo.save_version(p["project_id"], make_study(), origin="manual_edit",
                           author="ingénieur", expected_version=1)
    assert v2["version"] == 2 and v2["parent_version"] == 1
    assert repo.get_project(p["project_id"])["status"] == "draft_reviewed"
    assert repo.latest_version(p["project_id"])["version"] == 2


def test_conflit_optimiste(repo):
    p = repo.create_project("E", [])
    repo.save_version(p["project_id"], make_study(), "generation", "pipeline", None)
    with pytest.raises(VersionConflictError):
        repo.save_version(p["project_id"], make_study(), "manual_edit", "ingénieur",
                          expected_version=0)  # quelqu'un a déjà écrit v1


def test_validation_fige_et_trace(repo):
    p = repo.create_project("E", [])
    repo.save_version(p["project_id"], make_study(), "generation", "pipeline", None)
    v = repo.validate(p["project_id"], expected_version=1, validated_by="Laury")
    assert v["study"]["validated_at"] is not None
    assert v["origin"] == "validation" and v["version"] == 2
    assert repo.get_project(p["project_id"])["status"] == "validated"


def test_validation_bloquee_si_references_cassees(repo):
    p = repo.create_project("E", [])
    bad = make_study()
    bad["atelier_1"]["evenements_redoutes"][0]["valeur_metier_id"] = "vm-999"
    repo.save_version(p["project_id"], bad, "generation", "pipeline", None)
    with pytest.raises(ValidationBlockedError):
        repo.validate(p["project_id"], expected_version=1, validated_by="Laury")


def test_edition_apres_validation_revoque_la_validation(repo):
    p = repo.create_project("E", [])
    repo.save_version(p["project_id"], make_study(), "generation", "pipeline", None)
    validated = repo.validate(p["project_id"], expected_version=1, validated_by="Laury")
    assert validated["study"]["validated_at"] is not None
    # L'ingénieur modifie le brouillon déjà validé : le front renvoie le study
    # tel quel (validated_at encore présent) — le backend doit l'invalider.
    edited = dict(validated["study"])
    v3 = repo.save_version(p["project_id"], edited, "manual_edit", "ingénieur",
                           expected_version=2)
    assert v3["study"]["validated_at"] is None
    assert repo.get_project(p["project_id"])["status"] == "draft_reviewed"


def test_mark_exported(repo):
    p = repo.create_project("E", [])
    repo.save_version(p["project_id"], make_study(), "generation", "pipeline", None)
    repo.validate(p["project_id"], 1, "Laury")
    repo.mark_exported(p["project_id"], "xlsx")
    proj = repo.get_project(p["project_id"])
    assert proj["status"] == "exported" and proj["exports"][0]["format"] == "xlsx"
