"""Endpoints EBIOS RM — Atelier 1 : dossiers, brouillons versionnés, validation, exports."""
from __future__ import annotations

import queue
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from api.common import _sse

router = APIRouter()

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _repo():
    from core.ebios.repository import EbiosRepository
    return EbiosRepository()


class CreateProjectBody(BaseModel):
    name: str
    document_ids: List[str] = []


class PutAtelierBody(BaseModel):
    study: dict
    expected_version: int
    author: str = "ingénieur"


class ValidateBody(BaseModel):
    expected_version: int
    validated_by: str = "ingénieur"


class ExportBody(BaseModel):
    format: str  # xlsx | docx | pdf


class PutDocumentsBody(BaseModel):
    document_ids: List[str]


def _get_project_or_404(repo, project_id: str) -> dict:
    proj = repo.get_project(project_id)
    if not proj:
        raise HTTPException(404, f"Dossier EBIOS inconnu : {project_id}")
    return proj


@router.post("/api/ebios/projects")
def create_project(body: CreateProjectBody):
    if not body.name.strip():
        raise HTTPException(400, "Nom de dossier requis")
    return _repo().create_project(body.name.strip(), body.document_ids)


@router.get("/api/ebios/projects")
def list_projects():
    return {"projects": _repo().list_projects()}


@router.get("/api/ebios/projects/{project_id}")
def get_project(project_id: str):
    from core.ebios.schemas import EbiosStudy, bounded_control
    repo = _repo()
    proj = _get_project_or_404(repo, project_id)
    latest = repo.latest_version(project_id)
    warnings: List[str] = []
    if latest:
        stored = list(latest["study"].get("warnings", []))
        fresh = bounded_control(EbiosStudy.model_validate(latest["study"]))
        warnings = stored + [w for w in fresh if w not in stored]
    return {"project": proj, "latest": latest, "warnings": warnings}


@router.put("/api/ebios/projects/{project_id}/documents")
def put_documents(project_id: str, body: PutDocumentsBody):
    repo = _repo()
    _get_project_or_404(repo, project_id)
    return repo.update_documents(project_id, body.document_ids)


@router.put("/api/ebios/projects/{project_id}/atelier-1")
def put_atelier(project_id: str, body: PutAtelierBody):
    from core.ebios.repository import VersionConflictError
    repo = _repo()
    _get_project_or_404(repo, project_id)
    try:
        return repo.save_version(project_id, body.study, origin="manual_edit",
                                 author=body.author, expected_version=body.expected_version)
    except VersionConflictError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:  # pydantic ValidationError incluse
        raise HTTPException(422, f"Étude invalide : {e}")


@router.post("/api/ebios/projects/{project_id}/atelier-1/validate")
def validate_atelier(project_id: str, body: ValidateBody):
    from core.ebios.repository import ValidationBlockedError, VersionConflictError
    repo = _repo()
    _get_project_or_404(repo, project_id)
    try:
        return repo.validate(project_id, body.expected_version, body.validated_by)
    except VersionConflictError as e:
        raise HTTPException(409, str(e))
    except ValidationBlockedError as e:
        raise HTTPException(422, f"Validation refusée : {e}")


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "ebios"


@router.post("/api/ebios/projects/{project_id}/atelier-1/exports")
def export_atelier(project_id: str, body: ExportBody):
    from core.ebios.exporters.xlsx import TemplateIntegrityError, export_xlsx
    repo = _repo()
    proj = _get_project_or_404(repo, project_id)
    if body.format in ("docx", "pdf"):
        raise HTTPException(409, f"Export {body.format.upper()} indisponible en V1 : "
                                 "template non validé")
    if body.format != "xlsx":
        raise HTTPException(400, f"Format inconnu : {body.format}")
    latest = repo.latest_version(project_id)
    if not latest or not latest["study"].get("validated_at"):
        raise HTTPException(409, "Export refusé : l'Atelier 1 doit d'abord être validé")

    tmp_dir = Path(tempfile.mkdtemp(prefix="ebios_export_"))
    filename = (f"{_safe_name(proj['name'])}_atelier1_v{latest['version']}"
                f"_{time.strftime('%Y%m%d')}.xlsx")
    out = tmp_dir / filename
    try:
        export_xlsx(latest["study"], out)
    except TemplateIntegrityError as e:
        raise HTTPException(500, str(e))
    repo.mark_exported(project_id, body.format)

    def _cleanup():
        try:
            out.unlink(missing_ok=True)
            tmp_dir.rmdir()
        except OSError:
            pass

    return FileResponse(out, media_type=_XLSX_MIME, filename=filename,
                        background=BackgroundTask(_cleanup))


def _generate():
    from core.ebios.atelier1 import generate_atelier1
    return generate_atelier1


@router.post("/api/ebios/projects/{project_id}/atelier-1/generate")
def generate(project_id: str):
    repo = _repo()
    proj = _get_project_or_404(repo, project_id)
    generate_fn = _generate()

    def gen():
        docs = proj.get("document_ids") or []
        if not docs:
            yield _sse({"type": "error", "message": "Aucun document sélectionné"})
            return
        events: "queue.Queue" = queue.Queue()
        result: dict = {}

        def worker():
            try:
                result["study"] = generate_fn(
                    project_id, docs,
                    progress=lambda stage, detail=None: events.put(
                        {"type": "stage", "stage": stage, **(detail or {})}),
                )
            except Exception as e:  # noqa: BLE001 — remonté au flux SSE
                result["error"] = str(e)
            finally:
                events.put(None)

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        while True:
            ev = events.get()
            if ev is None:
                break
            yield _sse(ev)
        t.join()
        if "error" in result:
            yield _sse({"type": "error", "message": result["error"]})
            return
        try:
            version = repo.save_version(project_id, result["study"], origin="generation",
                                        author="pipeline", expected_version=None)
        except ValueError as e:
            yield _sse({"type": "error", "message": f"Brouillon invalide : {e}"})
            return
        yield _sse({"type": "draft", "version": version["version"],
                    "study": result["study"],
                    "warnings": result["study"].get("warnings", [])})
        yield _sse({"type": "done"})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store"})
