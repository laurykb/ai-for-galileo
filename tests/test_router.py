"""
Tests unitaires du routeur de requêtes (core/router) - 100 % hors-ligne, déterministe.

On vérifie que les questions complexes (comparaison, relationnel, multi-documents,
multi-questions) partent vers l'agent, que les questions factuelles directes restent
en RAG classique, et que seules les questions « à enjeu » déclenchent la vérification.
"""
from core.router import route_query, should_verify, _norm


def test_norm_strips_accents_and_case():
    assert _norm("Différence ÉVALuée") == "difference evaluee"


# -- route_query -> agent (complexité) ------------------------------------------
def test_comparison_routes_to_agent():
    d = route_query("Quelle est la différence entre EAL3 et EAL4 ?")
    assert d["mode"] == "agent"
    assert "comparaison" in d["signals"]


def test_relational_routes_to_agent():
    d = route_query("Quelle relation entre les menaces et les objectifs de sécurité ?")
    assert d["mode"] == "agent"
    assert "relationnel" in d["signals"]


def test_multi_documents_routes_to_agent():
    d = route_query("Compare les hypothèses dans les deux documents.")
    assert d["mode"] == "agent"


def test_multi_questions_routes_to_agent():
    d = route_query("Quel est le niveau EAL ? Et quelles sont les hypothèses ?")
    assert d["mode"] == "agent"
    assert "multi_questions" in d["signals"]


def test_natural_comparison_routes_to_agent():
    for q in ["Compare les menaces des deux cibles.",
              "Quelle est la différence entre les exigences de A et de B ?",
              "En quoi la cible X diffère-t-elle de la cible Y ?"]:
        assert route_query(q)["mode"] == "agent", q


def test_relational_multi_hop_routes_to_agent():
    for q in ["Quels liens entre les hypothèses et les objectifs de sécurité ?",
              "Quelles conséquences des menaces sur les exigences ?"]:
        assert route_query(q)["mode"] == "agent", q


def test_multi_document_natural_phrasings_route_to_agent():
    for q in ["Pour chaque cible, donne le niveau EAL.",
              "Recense les exigences à travers les documents."]:
        assert route_query(q)["mode"] == "agent", q


# -- route_query -> rag (cas courant) -------------------------------------------
def test_direct_factual_routes_to_rag():
    d = route_query("Quel est le niveau EAL de la TOE ?")
    assert d["mode"] == "rag"
    assert d["signals"] == []


def test_simple_lookup_routes_to_rag():
    d = route_query("Donne la définition de la TOE.")
    assert d["mode"] == "rag"


def test_simple_factual_still_rag():
    for q in ["Quel est le niveau EAL de la TOE ?",
              "Donne la définition de la TOE.",
              "C'est quoi un IDS ?"]:
        assert route_query(q)["mode"] == "rag", q


# -- should_verify -------------------------------------------------------------
def test_high_stake_question_triggers_verification():
    assert should_verify("Quel niveau d'assurance EAL est visé ?")["verify"] is True
    assert should_verify("Quelle version exacte est certifiée ?")["verify"] is True


def test_general_question_skips_verification():
    assert should_verify("De quoi parle le document ?")["verify"] is False
    assert should_verify("Présente brièvement la cible.")["verify"] is False
