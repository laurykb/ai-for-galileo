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
