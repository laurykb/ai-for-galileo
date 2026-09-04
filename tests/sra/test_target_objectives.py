import csv
import json
from openpyxl import load_workbook
from core.sra.schemas import TargetObjectivesTable
from core.sra.target_objectives import generate_target_objectives

RO_TYPES = [{"ro_type_id": "RO-01", "canonical_type": "Acteur étatique", "definition": "Intérêts stratégiques", "characterization": ["Espionner et perturber"]},
            {"ro_type_id": "RO-03", "canonical_type": "Cybercriminel organisé", "definition": "Gain financier", "characterization": ["Extorquer"]}]

def test_generation_croise_ro_types_et_cibles():
    def gather(document): return [{"content": "The Galileo Control System (GCS) monitors and controls the constellation."}]
    def extract(prompt, format=None): return json.dumps({"cibles": [
        {"cible": "GCS", "categorie_cible": "segment",
         "description": "Contrôle de la constellation.",
         "extraits": ["The Galileo Control System (GCS) monitors and controls the constellation."]}]})
    def pipeline(question, **kwargs):
        yield {"type": "done", "result": {"extractions": [kwargs["extract"]("architecture-clean.md", question)], "document_coverage": {"documents_without_evidence": []}}}
    def adjudicate(prompt, format=None):
        payload = json.loads(prompt.split("[CANDIDATS]\n", 1)[1])
        return json.dumps({"decisions": [{"id": row["id"], "accepte": True, "cible_normalisee": "Galileo Control System (GCS)",
            "categorie_cible": "segment", "scenarios": [
                {"ro_type_id": "RO-01", "attaque": "Compromission du GCS", "objectif": "Perturber le contrôle", "justification": "Capacité étatique et objectif stratégique."},
                {"ro_type_id": "RO-03", "attaque": "Rançongiciel sur le GCS", "objectif": "Extorquer l opérateur",
                 "proprietes_securite": ["availability"], "domaines_cibles": ["ground_segment", "supervision"],
                 "motivations": ["financial_gain"], "justification": "Motivation financière et recherche d indisponibilité."},
            ], "motif_rejet": ""} for row in payload]})
    table = generate_target_objectives("p", RO_TYPES, ["architecture-clean.md"], pipeline=pipeline,
                                       extract_llm=extract, project_llm=adjudicate, gather=gather)
    model = TargetObjectivesTable.model_validate(table)
    assert [(row.ro_type_id, len(row.attacks)) for row in model.target_objectives] == [
        ("RO-01", 1), ("RO-03", 1)]
    assert [row.to_id for row in model.target_objectives] == ["TO-001", "TO-002"]
    assert model.target_objectives[0].attacks[0].target_names == ["Galileo Control System (GCS)"]
    assert model.target_objectives[0].attacks[0].evidence[0].source == "architecture-clean.md"
    criminal = model.target_objectives[1].attacks[0]
    assert criminal.security_properties == ["availability"]
    assert criminal.target_domains == ["ground_segment", "supervision"]
    assert criminal.motivations == ["financial_gain"]
    assert model.schema_version == "2.0"

def test_generation_exige_une_table_ro():
    try: generate_target_objectives("p", [], ["doc"], pipeline=lambda *args, **kwargs: iter(()))
    except ValueError as exc: assert "RO Type canonique" in str(exc)
    else: raise AssertionError("Une table RO vide doit être rejetée")

def test_extraction_rejette_preuve_non_textuelle():
    def gather(document): return [{"content": "GCS controls satellites."}]
    def extract(prompt, format=None): return '{"cibles":[{"cible":"GCS","categorie_cible":"segment","description":"Contrôle","extraits":["citation inventée"]}]}'
    def pipeline(question, **kwargs):
        yield {"type": "done", "result": {"extractions": [kwargs["extract"]("doc", question)], "document_coverage": {}}}
    table = generate_target_objectives("p", RO_TYPES[:1], ["doc"], pipeline=pipeline, extract_llm=extract,
        project_llm=lambda prompt, format=None: '{"decisions":[]}', gather=gather)
    assert table["target_objectives"] == []

def test_pre_adjudication_deduplicates_targets_and_aggregates_evidence():
    from core.sra.target_objectives import _deduplicate_candidates
    rows = _deduplicate_candidates([
        {"target_name": "GCS", "target_category": "segment", "description": "a", "source": "a.md", "evidence": "quote a"},
        {"target_name": " gcs ", "target_category": "SEGMENT", "description": "b", "source": "b.md", "evidence": "quote b"},
    ])
    assert len(rows) == 1
    assert rows[0]["evidence_items"] == [{"source": "a.md", "quote": "quote a"}, {"source": "b.md", "quote": "quote b"}]

def test_adjudication_resumes_completed_batch_without_llm_call():
    from core.sra.target_objectives import _adjudicate
    cached = {"1": [{"target_name": "GCS", "target_category": "segment", "source": "doc", "evidence": "q", "ro_type_id": "RO-01", "attack": "Compromission", "objective": "Perturber", "rationale": "r"}]}
    accepted, warnings, issues = _adjudicate(
        [{"target_name": "GCS", "target_category": "segment", "source": "doc", "evidence": "q"}],
        [{"ro_type_id": "RO-01", "definition": "d"}],
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("LLM ne doit pas être rappelé")),
        lambda *_args, **_kwargs: None, 18000, 1, cached)
    assert accepted == cached["1"]
    assert warnings == []
    assert issues == []

def test_adjudication_incomplete_conserve_candidat_et_lot_rejouable():
    from core.sra.target_objectives import _adjudicate
    checkpoints = {}
    accepted, warnings, issues = _adjudicate(
        [{"target_name": "GCS", "target_category": "segment", "source": "doc", "evidence": "citation"}],
        [{"ro_type_id": "RO-01", "definition": "d"}],
        lambda *_args, **_kwargs: '{"decisions":[]}',
        lambda *_args, **_kwargs: None, 18000, 1, checkpoints)
    assert accepted == []
    assert len(warnings) == 1
    assert issues == [{"batch": 1, "candidate_id": 1, "target_name": "GCS",
        "target_category": "segment", "evidence": [{"source": "doc", "quote": "citation"}],
        "reason": "decision_llm_absente_apres_reessai"}]
    assert "1" not in checkpoints


def test_adjudication_reessaie_les_omissions_en_micro_lots():
    from core.sra.target_objectives import _adjudicate
    candidates = [{"target_name": f"T-{index}", "target_category": "service",
                   "source": "doc", "evidence": f"q-{index}"}
                  for index in range(1, 9)]
    call_sizes = []

    def llm(prompt, format=None):
        payload = json.loads(prompt.split("[CANDIDATS]\n", 1)[1])
        call_sizes.append(len(payload))
        # Reproduit une réponse JSON valide qui omet la fin du lot initial.
        selected = payload[:2] if len(payload) > 4 else payload
        return json.dumps({"decisions": [{"id": row["id"], "accepte": True,
            "cible_normalisee": row["target_name"], "categorie_cible": "service",
            "scenarios": [{"ro_type_id": "RO-01", "attaque": "Compromission",
                "objectif": "Perturber", "justification": "Profil compatible"}],
            "motif_rejet": ""} for row in selected]})

    accepted, warnings, issues = _adjudicate(
        candidates, [{"ro_type_id": "RO-01", "definition": "d"}], llm,
        lambda *_args, **_kwargs: None, 18000, 1)
    assert len(accepted) == 8
    assert warnings == [] and issues == []
    assert call_sizes[0] == 8
    assert all(size <= 4 for size in call_sizes[1:])

def test_adjudication_tronquee_divise_une_cible_par_groupes_ro():
    from core.llm_client import GenerationTruncated
    from core.sra.target_objectives import _adjudicate
    ro_types = [{"ro_type_id": f"RO-{index:02d}", "type_canonique": f"Type {index}",
                 "definition": "d"} for index in range(1, 5)]
    events = []

    def llm(prompt, format=None):
        configured = json.loads(prompt.split("[RO TYPES AUTORISÉS]\n", 1)[1].split("\n[RÈGLES]", 1)[0])
        candidate = json.loads(prompt.split("[CANDIDATS]\n", 1)[1])[0]
        if len(configured) > 2:
            raise GenerationTruncated("sortie plafonnée")
        return json.dumps({"decisions": [{"id": candidate["id"], "accepte": True,
            "cible_normalisee": "GCS", "categorie_cible": "segment", "motif_rejet": "",
            "scenarios": [{"ro_type_id": ro["ro_type_id"], "attaque": "Compromission",
                "objectif": "Perturber", "justification": "Profil compatible"}
                for ro in configured]}]})

    accepted, warnings, issues = _adjudicate(
        [{"target_name": "GCS", "target_category": "segment", "source": "doc", "evidence": "q"}],
        ro_types, llm, lambda stage, detail=None: events.append((stage, detail or {})), 18000, 0)
    assert {row["ro_type_id"] for row in accepted} == {"RO-01", "RO-02", "RO-03", "RO-04"}
    assert warnings == [] and issues == []
    assert any(stage == "sra_to_adjudication_split" and detail["split"] == "ro_types"
               for stage, detail in events)

def test_finalisation_fusionne_sans_perdre_cibles_et_preuves():
    from core.sra.target_objectives import _finalize_scenarios
    rows = [
        {"ro_type_id": "RO-01", "attack": "Brouillage", "objective": "Perturber SIS", "rationale": "r1",
         "target_names": ["E1"], "target_categories": ["signal"], "evidence": [{"source": "a", "quote": "qa"}]},
        {"ro_type_id": "RO-01", "attack": "Jamming", "objective": "Dégrader SIS", "rationale": "r2",
         "target_names": ["E5"], "target_categories": ["signal"], "evidence": [{"source": "b", "quote": "qb"}]},
    ]
    finalized, warnings = _finalize_scenarios(rows, {"ro_type_id": "RO-01"},
        lambda *_args, **_kwargs: json.dumps({"groupes": [{"scenario_ids": ["TMP-1", "TMP-2"],
            "attaque": "Brouillage des signaux", "objectif": "Dégrader le SIS", "justification": "Même mécanisme radiofréquence."}]}),
        lambda *_args, **_kwargs: None, 18000)
    assert warnings == [] and len(finalized) == 1
    assert finalized[0]["target_names"] == ["E1", "E5"]
    assert len(finalized[0]["evidence"]) == 2

def test_finalisation_invalide_conserve_tous_les_scenarios():
    from core.sra.target_objectives import _finalize_scenarios
    rows = [{"ro_type_id": "RO-01", "attack": "A", "objective": "O", "rationale": "R",
             "target_names": ["T"], "target_categories": ["service"], "evidence": []},
            {"ro_type_id": "RO-01", "attack": "B", "objective": "P", "rationale": "S",
             "target_names": ["U"], "target_categories": ["service"], "evidence": []}]
    finalized, warnings = _finalize_scenarios(rows, {"ro_type_id": "RO-01"},
        lambda *_args, **_kwargs: '{"groupes":[{"scenario_ids":["TMP-1"],"attaque":"A","objectif":"O","justification":"R"}]}',
        lambda *_args, **_kwargs: None, 18000)
    assert finalized == rows and len(warnings) == 1

def test_budget_exceeded_returns_recoverable_partial_table():
    from utils.task_metrics import TaskBudgetExceeded
    checkpoints = []
    def pipeline(question, **kwargs):
        yield {"type": "done", "result": {"extractions": [{"document": "doc", "candidates": [{"target_name": "GCS", "target_category": "segment", "description": "control", "source": "doc", "evidence": "GCS controls satellites."}]}], "document_coverage": {}}}
    table = generate_target_objectives(
        "p", RO_TYPES[:1], ["doc"], pipeline=pipeline,
        extract_llm=lambda *_args, **_kwargs: "{}",
        project_llm=lambda *_args, **_kwargs: (_ for _ in ()).throw(TaskBudgetExceeded("Budget d appels LLM atteint")),
        gather=lambda _: [{"content": "GCS controls satellites."}],
        save_checkpoint=lambda **values: checkpoints.append(values))
    assert table["completion_status"] == "partial"
    assert table["resume_available"] is True
    assert checkpoints

def test_exports_to_relations(tmp_path):
    from core.sra.target_exporters import TO_HEADERS, export_target_csv, export_target_xlsx
    table = {"project_id": "p", "target_objectives": [{"to_id": "TO-001", "ro_type_id": "RO-01",
        "ro_canonical_type": "Acteur étatique", "target_name": "GCS", "target_category": "segment",
        "rationale": "Cible de contrôle", "evidence": [{"source": "doc", "quote": "citation"}]}]}
    csv_path, xlsx_path = tmp_path / "to.csv", tmp_path / "to.xlsx"
    export_target_csv(table, csv_path); export_target_xlsx(table, xlsx_path)
    with csv_path.open(encoding="utf-8-sig", newline="") as stream: rows = list(csv.reader(stream))
    assert tuple(rows[0]) == TO_HEADERS and len(rows[1]) == 11
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
            return "{\"decisions\":[]}" if "[CANDIDATS]" in prompt else "{\"cibles\":[]}"

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


def test_extraction_to_divise_le_texte_si_sortie_tronquee():
    from core.llm_client import GenerationTruncated
    from core.sra.target_objectives import extract_targets_from_document
    calls = []
    risk_origins = [{"ro_type_id": "RO-01", "type_canonique": "Type", "definition": "D"}]

    def llm(prompt, format=None):
        text = prompt.split("[TEXTE]\n", 1)[1]
        calls.append(len(text))
        if len(text) > 4_000:
            raise GenerationTruncated("sortie plafonnée")
        return "{\"cibles\":[]}"

    result = extract_targets_from_document(
        "doc", "aspect", risk_origins, llm,
        gather=lambda _: [{"content": "GCS controls satellites. " * 300}],
    )
    assert result["candidates"] == []
    assert calls[0] > 4_000
    assert len(calls) == 7
    assert sum(size <= 4_000 for size in calls) == 4
