"""Persistance Mongo des dossiers EBIOS : snapshots immuables + versionnement optimiste."""
from __future__ import annotations

import time
import uuid
from typing import Optional

from core.ebios.schemas import EbiosStudy, check_references

_PROJECTS = "ebios_projects"
_VERSIONS = "ebios_workshop_versions"
_ATELIER = "atelier_1"


class VersionConflictError(RuntimeError):
    """La version attendue n'est plus la dernière : ne pas écraser une correction."""


class ValidationBlockedError(ValueError):
    """Références internes invalides : la validation est refusée."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class EbiosRepository:
    def __init__(self, db=None):
        if db is None:
            from utils.mongo import get_db
            db = get_db()
        self._projects = db[_PROJECTS]
        self._versions = db[_VERSIONS]

    # -- projets -----------------------------------------------------------
    def create_project(self, name: str, document_ids: list[str]) -> dict:
        doc = {
            "project_id": uuid.uuid4().hex,
            "name": name,
            "document_ids": list(document_ids),
            "status": "created",
            "created_at": _now(),
            "exports": [],
        }
        self._projects.insert_one(dict(doc))
        return doc

    def get_project(self, project_id: str) -> Optional[dict]:
        doc = self._projects.find_one({"project_id": project_id})
        if doc:
            doc.pop("_id", None)
        return doc

    def list_projects(self) -> list[dict]:
        docs = list(self._projects.find({}))
        for d in docs:
            d.pop("_id", None)
        return sorted(docs, key=lambda d: d.get("created_at", ""), reverse=True)

    def update_documents(self, project_id: str, document_ids: list[str]) -> dict:
        self._projects.update_one({"project_id": project_id},
                                  {"$set": {"document_ids": list(document_ids)}})
        return self.get_project(project_id)

    # -- versions ----------------------------------------------------------
    def latest_version(self, project_id: str) -> Optional[dict]:
        doc = self._versions.find_one({"project_id": project_id, "atelier": _ATELIER},
                                      sort=[("version", -1)])
        if doc:
            doc.pop("_id", None)
        return doc

    def save_version(self, project_id: str, study: dict, origin: str, author: str,
                     expected_version: Optional[int], status: Optional[str] = None) -> dict:
        if origin == "manual_edit":
            # Une édition manuelle révoque toujours une validation précédente :
            # le front peut renvoyer le study tel quel (validated_at hérité de
            # la version validée) — l'export ne doit rester possible qu'après
            # une nouvelle validation explicite.
            study = dict(study)
            study["validated_at"] = None
        EbiosStudy.model_validate(study)  # snapshot toujours conforme au schéma
        latest = self.latest_version(project_id)
        current = latest["version"] if latest else 0
        if expected_version is not None and expected_version != current:
            raise VersionConflictError(
                f"Version attendue {expected_version}, dernière en base {current}"
            )
        doc = {
            "project_id": project_id,
            "atelier": _ATELIER,
            "version": current + 1,
            "parent_version": current or None,
            "origin": origin,
            "author": author,
            "created_at": _now(),
            "study": study,
        }
        self._versions.insert_one(dict(doc))
        new_status = status or {"generation": "draft_generated",
                                "manual_edit": "draft_reviewed"}.get(origin)
        if new_status:
            self._projects.update_one({"project_id": project_id},
                                      {"$set": {"status": new_status}})
        return doc

    def validate(self, project_id: str, expected_version: int, validated_by: str) -> dict:
        latest = self.latest_version(project_id)
        if not latest or latest["version"] != expected_version:
            raise VersionConflictError(
                f"Version attendue {expected_version}, dernière en base "
                f"{latest['version'] if latest else 0}"
            )
        study = dict(latest["study"])
        errs = check_references(EbiosStudy.model_validate(study))
        if errs:
            raise ValidationBlockedError(" ; ".join(errs))
        study["validated_at"] = _now()
        return self.save_version(project_id, study, origin="validation",
                                 author=validated_by, expected_version=expected_version,
                                 status="validated")

    def mark_exported(self, project_id: str, fmt: str) -> None:
        self._projects.update_one(
            {"project_id": project_id},
            {"$set": {"status": "exported"},
             "$push": {"exports": {"format": fmt, "at": _now()}}},
        )
