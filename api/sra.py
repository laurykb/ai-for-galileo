"""API SRA — table Risk Objectives."""
import queue
import re
import tempfile
import threading
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from api.common import _sse
from core.llm_client import GenerationCancelled

router = APIRouter()
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_GENERATION_JOBS: dict[tuple[str, str], threading.Event] = {}
_GENERATION_JOBS_LOCK = threading.Lock()


def _start_generation(project_id: str, kind: str) -> threading.Event:
    key = (project_id, kind)
    with _GENERATION_JOBS_LOCK:
        if key in _GENERATION_JOBS:
            raise HTTPException(409, f"Une génération {kind.upper()} est déjà en cours")
        event = threading.Event()
        _GENERATION_JOBS[key] = event
        return event


def _finish_generation(project_id: str, kind: str, event: threading.Event) -> None:
    with _GENERATION_JOBS_LOCK:
        if _GENERATION_JOBS.get((project_id, kind)) is event:
            _GENERATION_JOBS.pop((project_id, kind), None)


def _repo():
    from core.sra.repository import SraRepository
    return SraRepository()


def _generate():
    from core.sra.generate import generate_risk_objectives
    return generate_risk_objectives


class CreateProjectBody(BaseModel):
    name: str
    document_ids: List[str] = []


class PutDocumentsBody(BaseModel):
    document_ids: List[str]


class PutGenerationConfigBody(BaseModel):
    config: dict


class PutTableBody(BaseModel):
    table: dict


class ExportBody(BaseModel):
    format: str



def _project_or_404(repo, project_id: str) -> dict:
    project = repo.get_project(project_id)
    if not project:
        raise HTTPException(404, f"Dossier SRA inconnu : {project_id}")
    return project


@router.post("/api/sra/projects")
def create_project(body: CreateProjectBody):
    if not body.name.strip():
        raise HTTPException(400, "Nom de dossier requis")
    return _repo().create_project(body.name.strip(), body.document_ids)


@router.get("/api/sra/projects")
def list_projects():
    return {"projects": _repo().list_projects()}


@router.get("/api/sra/projects/{project_id}")
def get_project(project_id: str):
    return _project_or_404(_repo(), project_id)


@router.put("/api/sra/projects/{project_id}/documents")
def put_documents(project_id: str, body: PutDocumentsBody):
    repo = _repo()
    _project_or_404(repo, project_id)
    return repo.update_documents(project_id, body.document_ids)


@router.put("/api/sra/projects/{project_id}/generation-config")
def put_generation_config(project_id: str, body: PutGenerationConfigBody):
    repo = _repo()
    _project_or_404(repo, project_id)
    try:
        return repo.update_generation_config(project_id, body.config)
    except ValueError as exc:
        raise HTTPException(422, f"Configuration SRA invalide : {exc}") from exc


@router.put("/api/sra/projects/{project_id}/risk-objectives")
def put_table(project_id: str, body: PutTableBody):
    repo = _repo()
    _project_or_404(repo, project_id)
    try:
        return repo.save_table(project_id, body.table)
    except ValueError as exc:
        raise HTTPException(422, f"Table RO invalide : {exc}") from exc


@router.post("/api/sra/projects/{project_id}/generations/{kind}/stop")
def stop_generation(project_id: str, kind: str):
    _project_or_404(_repo(), project_id)
    if kind not in {"ro", "to"}:
        raise HTTPException(404, f"Type de génération inconnu : {kind}")
    with _GENERATION_JOBS_LOCK:
        event = _GENERATION_JOBS.get((project_id, kind))
        if event is not None:
            event.set()
    return {"stopped": event is not None, "kind": kind}


@router.post("/api/sra/projects/{project_id}/risk-objectives/generate")
def generate(project_id: str):
    repo = _repo()
    project = _project_or_404(repo, project_id)
    generate_fn = _generate()
    documents = project.get("document_ids") or []
    if not documents:
        raise HTTPException(422, "Aucun document sélectionné")
    cancel_event = _start_generation(project_id, "ro")

    def stream():
        events: queue.Queue = queue.Queue()
        result: dict = {}

        def worker():
            try:
                result["table"] = generate_fn(
                    project_id,
                    documents,
                    generation_config=project.get("generation_config"),
                    progress=lambda stage, detail=None: events.put(
                        {"type": "stage", "stage": stage, **(detail or {})}
                    ),
                    cancel_event=cancel_event,
                )
            except GenerationCancelled:
                result["stopped"] = True
            except Exception as exc:  # noqa: BLE001
                result["error"] = str(exc)
            finally:
                events.put(None)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        while True:
            event = events.get()
            if event is None:
                break
            yield _sse(event)
        thread.join()
        try:
            if result.get("stopped"):
                yield _sse({"type": "stopped", "message": "Génération RO arrêtée"})
                return
            if "error" in result:
                yield _sse({"type": "error", "message": result["error"]})
                return
            project_doc = repo.save_table(project_id, result["table"])
            yield _sse({"type": "table", "table": project_doc["table"]})
            yield _sse({"type": "done"})
        finally:
            _finish_generation(project_id, "ro", cancel_event)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store"})


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "sra"


@router.post("/api/sra/projects/{project_id}/risk-objectives/exports")
def export(project_id: str, body: ExportBody):
    from core.sra.exporters import export_csv, export_xlsx

    repo = _repo()
    project = _project_or_404(repo, project_id)
    table = project.get("table")
    if not table:
        raise HTTPException(409, "Aucune table Risk Objectives à exporter")
    if body.format not in {"csv", "xlsx"}:
        raise HTTPException(400, f"Format inconnu : {body.format}")

    tmp_dir = Path(tempfile.mkdtemp(prefix="sra_export_"))
    filename = f"{_safe_name(project['name'])}_risk_objectives.{body.format}"
    output = tmp_dir / filename
    if body.format == "csv":
        export_csv(table, output)
        media_type = "text/csv; charset=utf-8"
    else:
        export_xlsx(table, output)
        media_type = XLSX_MIME
    repo.mark_exported(project_id, body.format)

    def cleanup():
        try:
            output.unlink(missing_ok=True)
            tmp_dir.rmdir()
        except OSError:
            pass

    return FileResponse(output, media_type=media_type, filename=filename,
                        background=BackgroundTask(cleanup))


@router.put("/api/sra/projects/{project_id}/target-objectives")
def put_target_objectives_table(project_id: str, body: PutTableBody):
    repo = _repo(); _project_or_404(repo, project_id)
    try:
        return repo.save_target_objectives_table(project_id, body.table)
    except ValueError as exc:
        raise HTTPException(422, f"Table TO invalide : {exc}") from exc


@router.post("/api/sra/projects/{project_id}/target-objectives/generate")
def generate_target_objectives_route(project_id: str):
    from core.sra.target_objectives import generate_target_objectives
    repo = _repo(); project = _project_or_404(repo, project_id)
    if not (project.get("table") or {}).get("risk_origin_types"):
        raise HTTPException(422, "Générez d abord une table RO contenant des RO Types canoniques")
    cancel_event = _start_generation(project_id, "to")
    def stream():
        events: queue.Queue = queue.Queue(); result: dict = {}
        def worker():
            try:
                result["table"] = generate_target_objectives(project_id, (project.get("table") or {}).get("risk_origin_types", []),
                    generation_config=project.get("generation_config"),
                    progress=lambda stage, detail=None: events.put({"type": "stage", "stage": stage, **(detail or {})}),
                    cancel_event=cancel_event)
            except GenerationCancelled:
                result["stopped"] = True
            except Exception as exc:  # noqa: BLE001
                result["error"] = str(exc)
            finally: events.put(None)
        thread = threading.Thread(target=worker, daemon=True); thread.start()
        while True:
            event = events.get()
            if event is None: break
            yield _sse(event)
        thread.join()
        try:
            if result.get("stopped"):
                yield _sse({"type": "stopped", "message": "Génération TO arrêtée"}); return
            if "error" in result:
                yield _sse({"type": "error", "message": result["error"]}); return
            project_doc = repo.save_target_objectives_table(project_id, result["table"])
            yield _sse({"type": "target_objectives_table", "table": project_doc["target_objectives_table"]})
            yield _sse({"type": "done"})
        finally:
            _finish_generation(project_id, "to", cancel_event)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


@router.post("/api/sra/projects/{project_id}/target-objectives/exports")
def export_target_objectives(project_id: str, body: ExportBody):
    from core.sra.target_exporters import export_target_csv, export_target_xlsx
    repo = _repo(); project = _project_or_404(repo, project_id)
    table = project.get("target_objectives_table")
    if not table: raise HTTPException(409, "Aucune table Target Objectives à exporter")
    if body.format not in {"csv", "xlsx"}: raise HTTPException(400, f"Format inconnu : {body.format}")
    tmp_dir = Path(tempfile.mkdtemp(prefix="sra_to_export_"))
    filename = f"{_safe_name(project['name'])}_target_objectives.{body.format}"; output = tmp_dir / filename
    if body.format == "csv": export_target_csv(table, output); media_type = "text/csv; charset=utf-8"
    else: export_target_xlsx(table, output); media_type = XLSX_MIME
    repo.mark_exported(project_id, f"to_{body.format}")
    def cleanup():
        try: output.unlink(missing_ok=True); tmp_dir.rmdir()
        except OSError: pass
    return FileResponse(output, media_type=media_type, filename=filename, background=BackgroundTask(cleanup))
