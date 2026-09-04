from core.prompt_registry import template_fields, validate_template


def test_template_fields_extracts_required_variables():
    assert template_fields("Question {question} / contexte {context}") == {
        "question", "context",
    }


def test_validate_template_rejects_missing_variable():
    errors = validate_template("Question {question}", "{question} {context}")
    assert errors == ["Variable obligatoire manquante : {context}"]


def test_validate_template_accepts_extra_text_and_variables():
    assert validate_template(
        "Instruction {question} {context} {optional}",
        "{question} {context}",
    ) == []


def test_validate_template_rejects_empty_and_bad_braces():
    assert validate_template(" ", "{question}")
    assert validate_template("{question", "{question}")


def test_catalogue_expose_les_six_agents_sra_modifiables():
    from api.prompts import _catalog
    catalog = _catalog()
    expected = {
        "sra.ro.map", "sra.ro.adjudication", "sra.ro.finalization",
        "sra.to.map", "sra.to.adjudication", "sra.to.finalization",
    }
    assert expected <= catalog.keys()
    assert all(catalog[key]["editable"] is True for key in expected)
    assert validate_template(catalog["sra.to.adjudication"]["default"],
                             catalog["sra.to.adjudication"]["default"]) == []


def test_catalogues_rag_et_sra_sont_disjoints():
    from api.prompts import _catalog, _scope
    catalog = _catalog()
    rag = {key for key in catalog if _scope(key) == "rag"}
    sra = {key for key in catalog if _scope(key) == "sra"}
    assert rag and sra and rag.isdisjoint(sra)
    assert all(key.startswith("sra.") for key in sra)
