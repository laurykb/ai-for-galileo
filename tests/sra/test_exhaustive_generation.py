import json

from core.sra.generate import _content_batches, extract_sra_from_document


def test_document_entier_parcouru_par_lots_sans_troncature():
    content = "A" * 24_000 + "MARQUEUR_FIN"
    batches = _content_batches([{"content": content}])

    assert len(batches) == 2
    assert batches[0] == content[:24_000]
    assert "MARQUEUR_FIN" in batches[-1]
    assert batches[0][-1_500:] == batches[1][:1_500]
    assert all(len(batch) <= 24_000 for batch in batches)

    seen_prompts = []

    def llm(prompt, format=None):
        seen_prompts.append(prompt)
        if "MARQUEUR_FIN" in prompt:
            return json.dumps({"risk_objectives": [{
                "categorie_attaquant": "Attaquant de fin",
                "objectifs_vises": "Atteindre la cible de la dernière section.",
                "extraits": ["MARQUEUR_FIN"],
            }]})
        return json.dumps({"risk_objectives": []})

    result = extract_sra_from_document(
        "long.md", "", llm=llm, gather=lambda _: [{"content": content}],
    )

    assert len(seen_prompts) == 2
    assert len(result["items"]) == 1


def test_ro_sans_preuve_exacte_est_rejete():
    answer = {"risk_objectives": [{
        "categorie_attaquant": "Catégorie inventée",
        "objectifs_vises": "Objectif non prouvé.",
        "extraits": ["citation absente du document"],
    }]}

    result = extract_sra_from_document(
        "doc.md", "",
        llm=lambda *_args, **_kwargs: json.dumps(answer),
        gather=lambda _: [{"content": "Contenu réel sans cette citation."}],
    )

    assert result["items"] == []
