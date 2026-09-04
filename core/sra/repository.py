"""Persistance des dossiers SRA, révisions immuables et artefacts Chat."""
import copy
import time
import uuid

from core.sra.schemas import (ClassifiedTargetObjectivesTable, RiskObjectivesTable,
                              SraGenerationConfig, TargetObjectivesTable, TreatmentPlan)

COLLECTION = "sra_projects"
REVISIONS_COLLECTION = "sra_revisions"
CHECKPOINTS_COLLECTION = "sra_generation_checkpoints"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class SraRepository:
    def __init__(self, db=None):
        if db is None:
            from utils.mongo import get_db
            db = get_db()
        self.projects = db[COLLECTION]
        self.revisions = db[REVISIONS_COLLECTION]
        self.checkpoints = db[CHECKPOINTS_COLLECTION]

    @staticmethod
    def _clean(doc):
        if doc:
            doc = dict(doc)
            doc.pop("_id", None)
        return doc

    def create_project(self, name: str, document_ids: list[str]) -> dict:
        doc = {
            "project_id": uuid.uuid4().hex, "name": name,
            "document_ids": list(document_ids),
            "generation_config": SraGenerationConfig().model_dump(),
            "status": "created", "archived": False,
            "created_at": _now(), "updated_at": _now(),
            "table": None, "target_objectives_table": None,
            "classified_target_objectives_table": None,
            "treatment_plan": None, "downstream_status": "no_decision",
            "active_ro_revision_id": None, "active_to_revision_id": None,
            "generation_jobs": {}, "exports": [],
        }
        self.projects.insert_one(dict(doc))
        return doc

    def get_project(self, project_id: str):
        return self._clean(self.projects.find_one({"project_id": project_id}))

    def list_projects(self, include_archived: bool = False) -> list[dict]:
        query = {} if include_archived else {"archived": {"$ne": True}}
        docs = [self._clean(doc) for doc in self.projects.find(query)]
        return sorted(docs, key=lambda doc: doc.get("updated_at", doc.get("created_at", "")), reverse=True)

    def update_project(self, project_id: str, name: str | None = None,
                       archived: bool | None = None) -> dict:
        updates = {"updated_at": _now()}
        if name is not None:
            updates["name"] = name.strip()
        if archived is not None:
            updates["archived"] = archived
        self.projects.update_one({"project_id": project_id}, {"$set": updates})
        return self.get_project(project_id)

    def duplicate_project(self, project_id: str, name: str | None = None) -> dict:
        source = self.get_project(project_id)
        if not source:
            raise ValueError("Dossier SRA inconnu")
        clone_name = name or source["name"] + " — copie"
        clone = self.create_project(clone_name, source.get("document_ids", []))
        self.projects.update_one({"project_id": clone["project_id"]}, {"$set": {
            "generation_config": copy.deepcopy(source.get("generation_config")),
            "status": source.get("status", "created"), "updated_at": _now()}})
        clone = self.get_project(clone["project_id"])
        if source.get("table"):
            clone = self.save_table(clone["project_id"], source["table"], reason="duplicate")
        if source.get("target_objectives_table"):
            clone = self.save_target_objectives_table(
                clone["project_id"], source["target_objectives_table"], reason="duplicate")
        if source.get("classified_target_objectives_table"):
            clone = self.save_classified_target_objectives_table(
                clone["project_id"], source["classified_target_objectives_table"])
        if source.get("treatment_plan"):
            clone = self.save_treatment_plan(clone["project_id"], source["treatment_plan"])
        return clone

    def update_documents(self, project_id: str, document_ids: list[str]) -> dict:
        self.projects.update_one({"project_id": project_id}, {"$set": {
            "document_ids": list(document_ids), "updated_at": _now()}})
        return self.get_project(project_id)

    def update_generation_config(self, project_id: str, config: dict) -> dict:
        data = SraGenerationConfig.model_validate(config).model_dump()
        self.projects.update_one({"project_id": project_id}, {"$set": {
            "generation_config": data, "updated_at": _now()}})
        return self.get_project(project_id)

    def _next_sequence(self, project_id: str, kind: str) -> int:
        latest = self.revisions.find_one(
            {"project_id": project_id, "kind": kind}, sort=[("sequence", -1)])
        return int((latest or {}).get("sequence", 0)) + 1

    def _save_revision(self, project_id: str, kind: str, table: dict, reason: str) -> dict:
        pointer = "active_ro_revision_id" if kind == "ro" else "active_to_revision_id"
        project = self.get_project(project_id) or {}
        revision = {
            "revision_id": uuid.uuid4().hex, "project_id": project_id,
            "project_name": project.get("name", project_id), "kind": kind,
            "sequence": self._next_sequence(project_id, kind), "reason": reason,
            "parent_revision_id": project.get(pointer), "created_at": _now(),
            "table": copy.deepcopy(table),
        }
        self.revisions.insert_one(copy.deepcopy(revision))
        table_field = "table" if kind == "ro" else "target_objectives_table"
        self.projects.update_one({"project_id": project_id}, {"$set": {
            table_field: copy.deepcopy(table), pointer: revision["revision_id"],
            "status": "ready", "updated_at": _now()}})
        return revision

    def save_table(self, project_id: str, table: dict, reason: str = "manual_review") -> dict:
        data = RiskObjectivesTable.model_validate(table).model_dump()
        data["project_id"] = project_id
        for index, item in enumerate(data.get("ambiguous_candidates", []), 1):
            item["candidate_id"] = item.get("candidate_id") or f"AMB-{index:04d}"
        self._save_revision(project_id, "ro", data, reason)
        return self.get_project(project_id)

    def save_target_objectives_table(self, project_id: str, table: dict,
                                     reason: str = "manual_review") -> dict:
        from core.sra.scenario_enrichment import enrich_scenario
        enriched = copy.deepcopy(table)
        for relation in enriched.get("target_objectives", []):
            ro_id = relation.get("ro_type_id", "")
            proof_count = len(relation.get("ro_evidence", []))
            if relation.get("attacks") is not None:
                relation["attacks"] = [
                    enrich_scenario(scenario, ro_id, proof_count)
                    for scenario in relation.get("attacks", [])
                ]
        data = TargetObjectivesTable.model_validate(enriched).model_dump()
        data["project_id"] = project_id
        self._save_revision(project_id, "to", data, reason)
        self.projects.update_one({"project_id": project_id}, {"$set": {
            "classified_target_objectives_table": None,
            "treatment_plan": None,
            "downstream_status": "stale_after_to_revision",
            "updated_at": _now(),
        }})
        return self.get_project(project_id)

    def save_classified_target_objectives_table(self, project_id: str, table: dict) -> dict:
        data = ClassifiedTargetObjectivesTable.model_validate(table).model_dump()
        data["project_id"] = project_id
        self.projects.update_one({"project_id": project_id}, {"$set": {
            "classified_target_objectives_table": copy.deepcopy(data),
            "treatment_plan": None, "downstream_status": "decision_current",
            "status": "ready", "updated_at": _now()}})
        return self.get_project(project_id)

    def save_treatment_plan(self, project_id: str, plan: dict) -> dict:
        data = TreatmentPlan.model_validate(plan).model_dump()
        data["project_id"] = project_id
        self.projects.update_one({"project_id": project_id}, {"$set": {
            "treatment_plan": copy.deepcopy(data),
            "downstream_status": "treatment_current", "updated_at": _now(),
        }})
        return self.get_project(project_id)

    def get_generation_checkpoint(self, project_id: str, kind: str,
                                  fingerprint: str) -> dict | None:
        return self._clean(self.checkpoints.find_one({
            "project_id": project_id, "kind": kind,
            "fingerprint": fingerprint,
        }))

    def save_generation_checkpoint(self, project_id: str, kind: str,
                                   fingerprint: str, **values) -> dict:
        """Upsert un état de reprise sans créer de révision métier prématurée."""
        values = copy.deepcopy(values)
        values["updated_at"] = _now()
        self.checkpoints.update_one(
            {"project_id": project_id, "kind": kind, "fingerprint": fingerprint},
            {"$set": values, "$setOnInsert": {
                "project_id": project_id, "kind": kind,
                "fingerprint": fingerprint, "created_at": _now(),
            }}, upsert=True)
        return self.get_generation_checkpoint(project_id, kind, fingerprint) or {}

    def clear_generation_checkpoint(self, project_id: str, kind: str,
                                    fingerprint: str) -> None:
        self.checkpoints.delete_one({
            "project_id": project_id, "kind": kind,
            "fingerprint": fingerprint,
        })

    def list_revisions(self, project_id: str, kind: str | None = None) -> list[dict]:
        query = {"project_id": project_id}
        if kind:
            query["kind"] = kind
        rows = [self._clean(row) for row in self.revisions.find(query)]
        rows.sort(key=lambda row: (row.get("created_at", ""), row.get("sequence", 0)), reverse=True)
        return [{key: value for key, value in row.items() if key != "table"} for row in rows]

    def get_revision(self, revision_id: str) -> dict | None:
        return self._clean(self.revisions.find_one({"revision_id": revision_id}))

    def compare_revisions(self, left_id: str, right_id: str) -> dict:
        left, right = self.get_revision(left_id), self.get_revision(right_id)
        if not left or not right:
            raise ValueError("Révision SRA inconnue")
        if left["kind"] != right["kind"]:
            raise ValueError("Les révisions comparées doivent être du même type")
        kind = left["kind"]
        rows_key = "risk_origin_types" if kind == "ro" else "target_objectives"
        id_key = "ro_type_id" if kind == "ro" else "to_id"
        left_rows = {row.get(id_key): row for row in left["table"].get(rows_key, [])}
        right_rows = {row.get(id_key): row for row in right["table"].get(rows_key, [])}
        added = [right_rows[key] for key in sorted(right_rows.keys() - left_rows.keys())]
        removed = [left_rows[key] for key in sorted(left_rows.keys() - right_rows.keys())]
        changed = [{"id": key, "before": left_rows[key], "after": right_rows[key]}
                   for key in sorted(left_rows.keys() & right_rows.keys())
                   if left_rows[key] != right_rows[key]]
        return {"kind": kind, "left": left_id, "right": right_id,
                "added": added, "removed": removed, "changed": changed}

    def set_job(self, project_id: str, kind: str, status: str, **detail) -> None:
        payload = {"status": status, "updated_at": _now(), **detail}
        self.projects.update_one({"project_id": project_id}, {"$set": {
            f"generation_jobs.{kind}": payload, "updated_at": _now()}})

    def list_artifacts(self) -> list[dict]:
        artifacts = []
        for project in self.list_projects():
            specs = (("ro", "table", "active_ro_revision_id"),
                     ("to", "target_objectives_table", "active_to_revision_id"))
            for kind, field, pointer in specs:
                table = project.get(field)
                if not table:
                    continue
                revision_id = project.get(pointer) or "current:{}:{}".format(project["project_id"], kind)
                rows_key = "risk_origin_types" if kind == "ro" else "target_objectives"
                count = len(table.get(rows_key, []))
                label = "{} — {} ({} lignes)".format(project["name"], kind.upper(), count)
                revision = self.get_revision(revision_id) if not revision_id.startswith("current:") else None
                artifacts.append({"revision_id": revision_id, "project_id": project["project_id"],
                                  "project_name": project["name"], "kind": kind,
                                  "label": label, "count": count,
                                  "sequence": (revision or {}).get("sequence", 0),
                                  "created_at": (revision or {}).get("created_at", table.get("generated_at", "")),
                                  "generated_at": table.get("generated_at", "")})
        return artifacts

    def artifact_chunks(self, revision_ids: list[str]) -> list[dict]:
        chunks = []
        for revision_id in revision_ids:
            revision = None
            if revision_id.startswith("current:"):
                _, project_id, kind = revision_id.split(":", 2)
                project = self.get_project(project_id)
                if project:
                    field = "table" if kind == "ro" else "target_objectives_table"
                    revision = {"project_name": project.get("name", project_id),
                                "kind": kind, "sequence": "active", "table": project.get(field) or {}}
            else:
                revision = self.get_revision(revision_id)
            if not revision:
                continue
            kind, table = revision["kind"], revision["table"]
            rows_key = "risk_origin_types" if kind == "ro" else "target_objectives"
            for row in table.get(rows_key, []):
                proof = " | ".join("{}: {}".format(item.get("source"), item.get("quote"))
                                   for item in row.get("evidence", []))
                if kind == "ro":
                    content = "RO Type {}: {}\nDéfinition: {}\nCaractérisation: {}\nStatut revue: {}\nPreuves: {}".format(
                        row.get("ro_type_id"), row.get("canonical_type"), row.get("definition"),
                        "; ".join(row.get("characterization", [])), row.get("review_status", "proposed"), proof)
                else:
                    if row.get("attacks") is not None:
                        scenarios = "\n".join(
                            "{}: {} | Objectif: {} | Cibles: {} | Justification: {} | Preuves: {}".format(
                                item.get("scenario_id"), item.get("attack"), item.get("objective"),
                                ", ".join(item.get("target_names", [])), item.get("rationale"),
                                " | ".join("{}: {}".format(e.get("source"), e.get("quote"))
                                           for e in item.get("evidence", [])))
                            for item in row.get("attacks", []))
                        content = "TO {}: {}\nDéfinition: {}\nScénarios d attaque:\n{}\nStatut revue: {}".format(
                            row.get("to_id"), row.get("ro_canonical_type"), row.get("ro_definition", ""),
                            scenarios, row.get("review_status", "proposed"))
                    else:
                        content = "Relation {}: {} -> {}\nCatégorie cible: {}\nJustification: {}\nStatut revue: {}\nPreuves: {}".format(
                            row.get("to_id"), row.get("ro_canonical_type"), row.get("target_name"),
                            row.get("target_category"), row.get("rationale"), row.get("review_status", "proposed"), proof)
                source = "SRA — {} — {} v{}".format(
                    revision.get("project_name"), kind.upper(), revision.get("sequence"))
                chunks.append({"source": source, "content": content,
                               "artifact_revision_id": revision_id, "kind": kind})
        return chunks

    def mark_exported(self, project_id: str, fmt: str) -> None:
        self.projects.update_one({"project_id": project_id}, {"$push": {
            "exports": {"format": fmt, "at": _now()}}})
