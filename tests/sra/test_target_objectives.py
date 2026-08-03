import csv
import json
from openpyxl import load_workbook
from core.sra.schemas import TargetObjectivesTable
from core.sra.target_objectives import generate_target_objectives

RO_TYPES = [{"ro_type_id": "RO-01", "canonical_type": "Acteur étatique", "definition": "Intérêts stratégiques", "characterization": ["Espionner et perturber"]},
            {"ro_type_id": "RO-03", "canonical_type": "Cybercriminel organisé", "definition": "Gain financier", "characterization": ["Extorquer"]}]

def test_generation_croise_ro_types_et_cibles():
    def gather(document): return [{"content": "The Galileo Control System (GCS) monitors and controls the constellation."}]
    def extract(prompt, format=None): return json.dumps({"relations": [
        {"ro_type_id": "RO-01", "cible": "GCS", "categorie_cible": "segment", "justification": "Permet le contrôle de la constellation.",
         "extraits": ["The Galileo Control System (GCS) monitors and controls the constellation."]},
        {"ro_type_id": "RO-03", "cible": "GCS", "categorie_cible": "segment", "justification": "Une indisponibilité peut soutenir une extorsion.",
         "extraits": ["The Galileo Control System (GCS) monitors and controls the constellation."]}]})
    def pipeline(question, **kwargs):
        yield {"type": "done", "result": {"extractions": [kwargs["extract"]("architecture-clean.md", question)], "document_coverage": {"documents_without_evidence": []}}}
    def adjudicate(prompt, format=None):
        payload = json.loads(prompt.split("[CANDIDATS]\n", 1)[1])
        return json.dumps({"decisions": [{"id": row["id"], "accepte": True, "cible_normalisee": "Galileo Control System (GCS)",
            "categorie_cible": "segment", "justification": row["rationale"], "motif_rejet": ""} for row in payload]})
    table = generate_target_objectives("p", RO_TYPES, ["architecture-clean.md"], pipeline=pipeline,
                                       extract_llm=extract, project_llm=adjudicate, gather=gather)
    model = TargetObjectivesTable.model_validate(table)
    assert [(row.ro_type_id, row.target_name) for row in model.target_objectives] == [
        ("RO-01", "Galileo Control System (GCS)"), ("RO-03", "Galileo Control System (GCS)")]
    assert [row.to_id for row in model.target_objectives] == ["TO-001", "TO-002"]
    assert model.target_objectives[0].evidence[0].source == "architecture-clean.md"

def test_generation_exige_une_table_ro():
    try: generate_target_objectives("p", [], ["doc"], pipeline=lambda *args, **kwargs: iter(()))
    except ValueError as exc: assert "RO Type canonique" in str(exc)
    else: raise AssertionError("Une table RO vide doit être rejetée")

def test_extraction_rejette_preuve_non_textuelle():
    def gather(document): return [{"content": "GCS controls satellites."}]
    def extract(prompt, format=None): return '{"relations":[{"ro_type_id":"RO-01","cible":"GCS","categorie_cible":"segment","justification":"Contrôle","extraits":["citation inventée"]}]}'
    def pipeline(question, **kwargs):
        yield {"type": "done", "result": {"extractions": [kwargs["extract"]("doc", question)], "document_coverage": {}}}
    table = generate_target_objectives("p", RO_TYPES[:1], ["doc"], pipeline=pipeline, extract_llm=extract,
        project_llm=lambda prompt, format=None: '{"decisions":[]}', gather=gather)
    assert table["target_objectives"] == []

def test_exports_to_relations(tmp_path):
    from core.sra.target_exporters import TO_HEADERS, export_target_csv, export_target_xlsx
    table = {"project_id": "p", "target_objectives": [{"to_id": "TO-001", "ro_type_id": "RO-01",
        "ro_canonical_type": "Acteur étatique", "target_name": "GCS", "target_category": "segment",
        "rationale": "Cible de contrôle", "evidence": [{"source": "doc", "quote": "citation"}]}]}
    csv_path, xlsx_path = tmp_path / "to.csv", tmp_path / "to.xlsx"
    export_target_csv(table, csv_path); export_target_xlsx(table, xlsx_path)
    with csv_path.open(encoding="utf-8-sig", newline="") as stream: rows = list(csv.reader(stream))
    assert tuple(rows[0]) == TO_HEADERS and len(rows[1]) == 7
    sheet = load_workbook(xlsx_path).active
    assert sheet.title == "TO par RO Type" and tuple(cell.value for cell in sheet[1]) == TO_HEADERS

def test_garde_fou_mongo_utilise_pipeline_valide(monkeypatch):
    import utils.mongo
    seen = {}
    class Collection:
        def aggregate(self, pipeline):
            seen["pipeline"] = pipeline
            return [{"_id": "doc"}]
    class Db:
        def __getitem__(self, name):
            assert name == "chunks"
            return Collection()
    monkeypatch.setattr(utils.mongo, "get_db", lambda: Db())
    table = generate_target_objectives(
        "p", RO_TYPES[:1], ["doc"],
        pipeline=lambda *args, **kwargs: iter(()),
        extract_llm=lambda *args, **kwargs: "{}",
        project_llm=lambda *args, **kwargs: '{"decisions":[]}',
    )
    assert seen["pipeline"] == [
        {"$match": {"source": {"$in": ["doc"]}}},
        {"$group": {"_id": "$source"}},
    ]
    assert table["target_objectives"] == []


def test_generation_to_plafonne_fenetres_et_sorties_llm(monkeypatch):
    import core.model_router
    builds = []
    prompts = []

    class Client:
        def invoke_cancellable(self, prompt, format=None, cancel_event=None):
            prompts.append(prompt)
            return "{\"decisions\":[]}" if "[CANDIDATS]" in prompt else "{\"relations\":[]}"

    def build_llm(role, **kwargs):
        builds.append((role, kwargs))
        return Client()

    monkeypatch.setattr(core.model_router, "build_llm", build_llm)

    def pipeline(question, **kwargs):
        extraction = kwargs["extract"]("doc", question)
        yield {"type": "done", "result": {
            "extractions": [extraction], "document_coverage": {}}}

    generate_target_objectives(
        "p", RO_TYPES, ["doc"], pipeline=pipeline,
        gather=lambda _: [{"content": "A" * 20_000}],
        generation_config={"map_batch_chars": 24_000, "map_batch_overlap": 1_500},
    )

    map_prompts = [prompt for prompt in prompts if "[TEXTE]" in prompt]
    assert len(map_prompts) == 1
    assert builds[0][1]["num_predict"] == 4_096
    assert builds[1][1]["num_predict"] == 4_096


def test_extraction_to_reessaie_par_ro_type_si_sortie_tronquee():
    from core.llm_client import GenerationTruncated
    from core.sra.target_objectives import extract_targets_from_document
    calls = []
    risk_origins = [{"ro_type_id": f"RO-{index:02d}", "type_canonique": "Type", "definition": "D"} for index in range(1, 4)]

    def llm(prompt, format=None):
        payload = json.loads(prompt.split("[RO TYPES RETENUS]\n", 1)[1].split("\n[DÉFINITION]", 1)[0])
        calls.append(len(payload))
        if len(payload) > 1:
            raise GenerationTruncated("sortie plafonnée")
        return "{\"relations\":[]}"

    result = extract_targets_from_document(
        "doc", "aspect", risk_origins, llm,
        gather=lambda _: [{"content": "GCS controls satellites."}],
    )
    assert result["candidates"] == []
    assert calls == [3, 1, 2, 1, 1]
