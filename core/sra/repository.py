"""Persistance simple des dossiers SRA et de leur table RO."""
import time
import uuid

from core.sra.schemas import RiskObjectivesTable, SraGenerationConfig, TargetObjectivesTable

COLLECTION = "sra_projects"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class SraRepository:
    def __init__(self, db=None):
        if db is None:
            from utils.mongo import get_db
            db = get_db()
        self.projects = db[COLLECTION]

    @staticmethod
    def _clean(doc):
        if doc:
            doc.pop("_id", None)
        return doc

    def create_project(self, name: str, document_ids: list[str]) -> dict:
        doc = {
            "project_id": uuid.uuid4().hex,
            "name": name,
            "document_ids": list(document_ids),
            "generation_config": SraGenerationConfig().model_dump(),
            "status": "created",
            "created_at": _now(),
            "table": None,
            "target_objectives_table": None,
            "exports": [],
        }
        self.projects.insert_one(dict(doc))
        return doc

    def get_project(self, project_id: str):
        return self._clean(self.projects.find_one({"project_id": project_id}))

    def list_projects(self) -> list[dict]:
        docs = [self._clean(doc) for doc in self.projects.find({})]
        return sorted(docs, key=lambda doc: doc.get("created_at", ""), reverse=True)

    def update_documents(self, project_id: str, document_ids: list[str]) -> dict:
        self.projects.update_one(
            {"project_id": project_id},
            {"$set": {"document_ids": list(document_ids)}},
        )
        return self.get_project(project_id)

    def update_generation_config(self, project_id: str, config: dict) -> dict:
        data = SraGenerationConfig.model_validate(config).model_dump()
        self.projects.update_one(
            {"project_id": project_id},
            {"$set": {"generation_config": data}},
        )
        return self.get_project(project_id)

    def save_table(self, project_id: str, table: dict) -> dict:
        data = RiskObjectivesTable.model_validate(table).model_dump()
        self.projects.update_one(
            {"project_id": project_id},
            {"$set": {"table": data, "status": "ready"}},
        )
        return self.get_project(project_id)

    def save_target_objectives_table(self, project_id: str, table: dict) -> dict:
        data = TargetObjectivesTable.model_validate(table).model_dump()
        self.projects.update_one(
            {"project_id": project_id},
            {"$set": {"target_objectives_table": data, "status": "ready"}},
        )
        return self.get_project(project_id)

    def mark_exported(self, project_id: str, fmt: str) -> None:
        self.projects.update_one(
            {"project_id": project_id},
            {"$push": {"exports": {"format": fmt, "at": _now()}}},
        )
