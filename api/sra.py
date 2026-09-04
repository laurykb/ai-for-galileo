"""API SRA — table Risk Objectives."""
import queue
import re
import tempfile
import threading
import math
import hashlib
import json
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


def _require_mongo(repo) -> None:
    """Échoue avant tout calcul coûteux si l'artefact ne pourra pas être persisté."""
    try:
        repo.projects.database.client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            503,
            "MongoDB indisponible : génération refusée avant les appels LLM",
        ) from exc


def _elastic_to_budget(document_chars: dict[str, int], ro_types: int,
                       batch_chars: int, batch_overlap: int) -> tuple[dict, dict]:
    """Dimensionne les garde-fous TO selon le travail réellement demandé."""
    step = max(1, batch_chars - min(batch_overlap, batch_chars - 1))
    windows = sum(
        max(1, math.ceil(max(0, chars) / step))
        for chars in document_chars.values()
    )
    groups = 1
    map_calls = windows
    # Le corpus est lu une seule fois. Le croisement avec les RO Types intervient
    # ensuite sur les cibles compactes, sans répéter les fenêtres documentaires.
    prompt_tokens = sum(max(0, chars) for chars in document_chars.values()) / 4
    output_tokens = map_calls * 4096
    expected_tokens = max(1, math.ceil(prompt_tokens + output_tokens))
    budget = {
        "max_llm_calls": max(50, math.ceil(map_calls * 1.5) + 20),
        "max_wall_s": max(1800, math.ceil(map_calls * 75)),
        "max_total_tokens": max(250000, math.ceil(expected_tokens * 1.5)),
    }
    estimate = {
        "documents": len(document_chars),
        "characters": sum(document_chars.values()),
        "windows": windows,
        "ro_types": ro_types,
        "ro_type_groups": groups,
        "estimated_map_calls": map_calls,
        "estimated_tokens": expected_tokens,
    }
    return budget, estimate


def _to_workload(repo, documents: list[str], ro_types: int, config: dict | None):
    from core.evidence_policy import select_primary_evidence_chunks
    collection = repo.projects.database["chunks"]
    chars = {}
    for document in documents:
        chunks = list(collection.find(
            {"source": document, "chunk_type": {"$ne": "summary"}},
            {"content": 1, "quality_status": 1, "content_provenance": 1},
        ))
        selected, _policy = select_primary_evidence_chunks(chunks)
        chars[document] = sum(len(str(chunk.get("content") or "")) for chunk in selected)
    config = config or {}
    return _elastic_to_budget(
        chars,
        ro_types,
        int(config.get("map_batch_chars") or 24000),
        int(config.get("map_batch_overlap") or 1500),
    )


def _ro_workload(repo, documents: list[str], config: dict | None):
    """Même estimation élastique pour RO : une lecture MAP par fenêtre."""
    budget, estimate = _to_workload(repo, documents, 1, config)
    estimate["analysis"] = "risk_origins"
    estimate.pop("ro_types", None)
    estimate.pop("ro_type_groups", None)
    return budget, estimate


def _to_fingerprint(documents: list[str], ro_types: list[dict], config: dict) -> str:
    payload = {"pipeline_version": "to-scenarios-v3-attack-axes", "documents": documents,
               "ro_types": ro_types, "config": config}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def _dynamic_to_budget(base: dict, map_calls: int, candidates: int, batch_chars: int):
    approximate_chars = max(1, candidates) * 650
    adjudication_batches = max(1, math.ceil(approximate_chars / max(1, batch_chars)))
    adjudication_calls = adjudication_batches * 2
    dynamic = dict(base)
    dynamic["max_llm_calls"] = max(dynamic.get("max_llm_calls", 0), map_calls + math.ceil(adjudication_calls * 1.25) + 8)
    dynamic["max_wall_s"] = min(7200, max(1800, map_calls * 45 + adjudication_calls * 60))
    return dynamic, {"candidates_after_dedup": candidates, "estimated_adjudication_batches": adjudication_batches, "estimated_adjudication_calls": adjudication_calls}

def _generate():
    from core.sra.generate import generate_risk_objectives
    return generate_risk_objectives


class CreateProjectBody(BaseModel):
    name: str
    document_ids: List[str] = []


class PatchProjectBody(BaseModel):
    name: str | None = None
    archived: bool | None = None


class DuplicateProjectBody(BaseModel):
    name: str | None = None


class PutDocumentsBody(BaseModel):
    document_ids: List[str]


class GenerateTargetObjectivesBody(BaseModel):
    document_ids: List[str]
    confirmed: bool = False
    resume: bool = True


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
def list_projects(include_archived: bool = False):
    return {"projects": _repo().list_projects(include_archived=include_archived)}


@router.get("/api/sra/artifacts")
def list_artifacts():
    return {"artifacts": _repo().list_artifacts()}


@router.get("/api/sra/projects/{project_id}")
def get_project(project_id: str):
    return _project_or_404(_repo(), project_id)


@router.patch("/api/sra/projects/{project_id}")
def patch_project(project_id: str, body: PatchProjectBody):
    repo = _repo(); _project_or_404(repo, project_id)
    if body.name is not None and not body.name.strip():
        raise HTTPException(400, "Nom de dossier requis")
    return repo.update_project(project_id, name=body.name, archived=body.archived)


@router.post("/api/sra/projects/{project_id}/duplicate")
def duplicate_project(project_id: str, body: DuplicateProjectBody):
    repo = _repo(); _project_or_404(repo, project_id)
    return repo.duplicate_project(project_id, body.name)


@router.get("/api/sra/projects/{project_id}/revisions")
def list_revisions(project_id: str, kind: str | None = None):
    repo = _repo(); _project_or_404(repo, project_id)
    if kind not in {None, "ro", "to"}:
        raise HTTPException(400, "Type de révision inconnu")
    return {"revisions": repo.list_revisions(project_id, kind)}


@router.get("/api/sra/revisions/{revision_id}")
def get_revision(revision_id: str):
    revision = _repo().get_revision(revision_id)
    if not revision:
        raise HTTPException(404, "Révision SRA inconnue")
    return revision


@router.get("/api/sra/revisions/compare/{left_id}/{right_id}")
def compare_revisions(left_id: str, right_id: str):
    try:
        return _repo().compare_revisions(left_id, right_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


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
    _require_mongo(repo)
    project = _project_or_404(repo, project_id)
    generate_fn = _generate()
    documents = project.get("document_ids") or []
    if not documents:
        raise HTTPException(422, "Aucun document sélectionné")
    budget, estimate = _ro_workload(
        repo, documents, project.get("generation_config")
    )
    cancel_event = _start_generation(project_id, "ro")

    def stream():
        events: queue.Queue = queue.Queue()
        result: dict = {}

        def worker():
            from utils import task_metrics
            task = task_metrics.begin("sra.ro", budget=budget, project_id=project_id,
                                      requested_documents=list(documents),
                                      workload_estimate=estimate)
            token = task_metrics.bind(task["task_id"])
            events.put({"type": "task", "task_id": task["task_id"], "status": "running"})
            events.put({"type": "stage", "stage": "sra_ro_budget",
                        "estimate": estimate, "budget": budget})
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
                result["project_doc"] = repo.save_table(
                    project_id, result["table"], reason="generation"
                )
            except GenerationCancelled:
                result["stopped"] = True
            except Exception as exc:  # noqa: BLE001
                result["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                metrics = task_metrics.finish_sra(task["task_id"], result, documents, "risk_origin_types")
                events.put({"type": "task_metrics", "metrics": metrics})
                task_metrics.unbind(token)
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
            project_doc = result["project_doc"]
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


@router.post("/api/sra/projects/{project_id}/target-objectives/estimate")
def estimate_target_objectives_route(project_id: str, body: GenerateTargetObjectivesBody):
    repo = _repo(); project = _project_or_404(repo, project_id)
    ro_types = (project.get("table") or {}).get("risk_origin_types", [])
    if not ro_types or not body.document_ids:
        raise HTTPException(422, "RO Types et documents TO requis")
    budget, estimate = _to_workload(repo, body.document_ids, len(ro_types), project.get("generation_config"))
    estimate["confirmation_required"] = estimate.get("windows", 0) >= 20
    estimate["estimated_wall_s"] = min(7200, max(1800, estimate.get("estimated_map_calls", 0) * 45))
    fingerprint = _to_fingerprint(body.document_ids, ro_types, project.get("generation_config") or {})
    estimate["resume_available"] = bool(repo.get_generation_checkpoint(project_id, "to", fingerprint))
    return {"estimate": estimate, "budget": budget}

@router.post("/api/sra/projects/{project_id}/target-objectives/generate")
def generate_target_objectives_route(project_id: str, body: GenerateTargetObjectivesBody):
    from core.sra.target_objectives import generate_target_objectives
    repo = _repo(); _require_mongo(repo)
    project = _project_or_404(repo, project_id)
    if not (project.get("table") or {}).get("risk_origin_types"):
        raise HTTPException(422, "Générez d abord une table RO contenant des RO Types canoniques")
    if not body.document_ids:
        raise HTTPException(422, "Sélectionnez au moins un document pour générer les Target Objectives")
    ro_types = (project.get("table") or {}).get("risk_origin_types", [])
    budget, estimate = _to_workload(
        repo, body.document_ids, len(ro_types), project.get("generation_config")
    )
    estimate["confirmation_required"] = estimate.get("windows", 0) >= 20
    if estimate["confirmation_required"] and not body.confirmed:
        raise HTTPException(409, {"code": "confirmation_required", "message": "Cette génération TO est longue; confirmez après lecture de l estimation.", "estimate": estimate, "budget": budget})
    fingerprint = _to_fingerprint(body.document_ids, ro_types, project.get("generation_config") or {})
    checkpoint = repo.get_generation_checkpoint(project_id, "to", fingerprint) if body.resume else None
    cancel_event = _start_generation(project_id, "to")
    def stream():
        events: queue.Queue = queue.Queue(); result: dict = {}
        def worker():
            from utils import task_metrics
            task = task_metrics.begin("sra.to", budget=budget, project_id=project_id, requested_documents=list(body.document_ids), workload_estimate=estimate)
            token = task_metrics.bind(task["task_id"])
            events.put({"type": "task", "task_id": task["task_id"], "status": "running"})
            events.put({"type": "stage", "stage": "sra_to_budget",
                        "estimate": estimate, "budget": budget})
            checkpoint_lock = threading.Lock()
            try:
                def save_checkpoint(**values):
                    with checkpoint_lock:
                        repo.save_generation_checkpoint(project_id, "to", fingerprint, **values)
                def update_dynamic(map_calls, candidates, batch_chars):
                    dynamic, detail = _dynamic_to_budget(budget, map_calls, candidates, batch_chars)
                    task_metrics.update_budget(task["task_id"], **dynamic)
                    events.put({"type": "stage", "stage": "sra_to_budget_updated", "estimate": detail, "budget": dynamic})
                result["table"] = generate_target_objectives(project_id, (project.get("table") or {}).get("risk_origin_types", []),
                    document_ids=body.document_ids,
                    generation_config=project.get("generation_config"),
                    progress=lambda stage, detail=None: events.put({"type": "stage", "stage": stage, **(detail or {})}),
                    cancel_event=cancel_event, checkpoint=checkpoint, save_checkpoint=save_checkpoint, update_budget=update_dynamic)
                result["table"]["source_ro_revision_id"] = project.get("active_ro_revision_id", "")
                result["project_doc"] = repo.save_target_objectives_table(
                    project_id, result["table"], reason=("generation_partial" if result["table"].get("completion_status") == "partial" else "generation")
                )
                if result["table"].get("completion_status") == "complete":
                    repo.clear_generation_checkpoint(project_id, "to", fingerprint)
            except GenerationCancelled:
                result["stopped"] = True
            except Exception as exc:  # noqa: BLE001
                result["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                metrics = task_metrics.finish_sra(task["task_id"], result, body.document_ids, "target_objectives")
                events.put({"type": "task_metrics", "metrics": metrics})
                task_metrics.unbind(token)
                events.put(None)
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
            project_doc = result["project_doc"]
            yield _sse({"type": "target_objectives_table", "table": project_doc["target_objectives_table"]})
            yield _sse({"type": "done", "completion_status": result["table"].get("completion_status", "complete")})
        finally:
            _finish_generation(project_id, "to", cancel_event)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


@router.post("/api/sra/projects/{project_id}/target-objectives/classify")
def classify_target_objectives_route(project_id: str):
    from core.sra.classify_target_objectives import classify_target_objectives

    repo = _repo(); _require_mongo(repo)
    project = _project_or_404(repo, project_id)
    source = project.get("target_objectives_table")
    if not source:
        raise HTTPException(409, "Générez d abord la table TO exhaustive")
    generation_event = _start_generation(project_id, "to_classify")

    def stream():
        events: queue.Queue = queue.Queue(); result: dict = {}
        def worker():
            try:
                prepare_event = {"type": "stage", "stage": "sra_to_classification_prepare",
                                 "scenarios": sum(len(row.get("attacks") or []) for row in source.get("target_objectives", []))}
                events.put(prepare_event)
                table = classify_target_objectives(
                    project_id, source,
                    source_to_revision_id=project.get("active_to_revision_id", ""),
                    source_ro_revision_id=source.get("source_ro_revision_id") or project.get("active_ro_revision_id", ""),
                    progress=lambda stage, detail=None: events.put({"type": "stage", "stage": stage, **(detail or {})}),
                )
                saved_event = {"type": "stage", "stage": "sra_to_classification_saved",
                               "groups": len(table.get("classified_objectives", []))}
                table["trace"] = [{key: value for key, value in prepare_event.items() if key != "type"},
                                  *(table.get("trace") or []),
                                  {key: value for key, value in saved_event.items() if key != "type"}]
                result["project"] = repo.save_classified_target_objectives_table(project_id, table)
                events.put(saved_event)
            except Exception as exc:
                result["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                events.put(None)
        thread = threading.Thread(target=worker, daemon=True); thread.start()
        while True:
            event = events.get()
            if event is None: break
            yield _sse(event)
        thread.join()
        try:
            if result.get("error"):
                yield _sse({"type": "error", "message": result["error"]}); return
            table = result["project"]["classified_target_objectives_table"]
            yield _sse({"type": "classified_target_objectives_table", "table": table})
            yield _sse({"type": "done", "download": "xlsx"})
        finally:
            _finish_generation(project_id, "to_classify", generation_event)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


@router.post("/api/sra/projects/{project_id}/target-objectives/classified/exports")
def export_classified_target_objectives(project_id: str, body: ExportBody):
    from core.sra.target_exporters import export_classified_target_csv, export_classified_target_xlsx
    repo = _repo(); project = _project_or_404(repo, project_id)
    table = project.get("classified_target_objectives_table")
    if not table:
        raise HTTPException(409, "Aucune table TO classée à exporter")
    if body.format not in {"csv", "xlsx"}:
        raise HTTPException(400, f"Format inconnu : {body.format}")
    tmp_dir = Path(tempfile.mkdtemp(prefix="sra_to_classified_export_"))
    safe_project_name = _safe_name(project.get("name", project_id))
    filename = f"{safe_project_name}_TO_decisionnels.{body.format}"
    output = tmp_dir / filename
    if body.format == "csv":
        export_classified_target_csv(table, output)
        media_type = "text/csv; charset=utf-8"
    else:
        export_classified_target_xlsx(table, output)
        media_type = XLSX_MIME
    repo.mark_exported(project_id, f"to_classified_{body.format}")
    def cleanup():
        try: output.unlink(missing_ok=True); tmp_dir.rmdir()
        except OSError: pass
    return FileResponse(output, media_type=media_type, filename=filename,
                        background=BackgroundTask(cleanup))


@router.put("/api/sra/projects/{project_id}/target-objectives/decision")
def put_decision_target_objectives(project_id: str, body: PutTableBody):
    repo = _repo(); _project_or_404(repo, project_id)
    try:
        return repo.save_classified_target_objectives_table(project_id, body.table)
    except ValueError as exc:
        raise HTTPException(422, f"Table TO décisionnelle invalide : {exc}") from exc


@router.post("/api/sra/projects/{project_id}/treatment-plan/initialize")
def initialize_treatment_plan_route(project_id: str):
    from core.sra.treatment import initialize_treatment_plan
    repo = _repo(); project = _project_or_404(repo, project_id)
    decision = project.get("classified_target_objectives_table")
    if not decision:
        raise HTTPException(409, "Générez et révisez d abord les TO décisionnels")
    try:
        plan = initialize_treatment_plan(project_id, decision)
        return repo.save_treatment_plan(project_id, plan)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/api/sra/projects/{project_id}/treatment-plan")
def put_treatment_plan(project_id: str, body: PutTableBody):
    repo = _repo(); _project_or_404(repo, project_id)
    try:
        return repo.save_treatment_plan(project_id, body.table)
    except ValueError as exc:
        raise HTTPException(422, f"Plan de traitement invalide : {exc}") from exc



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
