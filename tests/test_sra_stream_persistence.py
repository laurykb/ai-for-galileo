import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.sra as sra
import core.sra.target_objectives as target_objectives


def _events(text):
    return [
        json.loads(line[6:])
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


class _Admin:
    def command(self, _name):
        return {"ok": 1}


class _Repo:
    projects = SimpleNamespace(
        database=SimpleNamespace(client=SimpleNamespace(admin=_Admin()))
    )

    def get_project(self, project_id):
        return {
            "project_id": project_id,
            "generation_config": {},
            "table": {
                "risk_origin_types": [
                    {"ro_type_id": "RO-01", "canonical_type": "Acteur étatique"}
                ]
            },
        }

    def get_generation_checkpoint(self, *_args, **_kwargs):
        return None

    def save_generation_checkpoint(self, *_args, **_kwargs):
        return {}

    def clear_generation_checkpoint(self, *_args, **_kwargs):
        return None

    def save_target_objectives_table(self, *_args, **_kwargs):
        raise RuntimeError("mongo lost before save")


def test_target_generation_reports_persistence_failure_as_sse(monkeypatch):
    monkeypatch.setattr(sra, "_repo", lambda: _Repo())
    monkeypatch.setattr(
        sra,
        "_to_workload",
        lambda *args, **kwargs: (
            {"max_llm_calls": 50, "max_wall_s": 1800, "max_total_tokens": 250000},
            {"documents": 1, "windows": 1, "ro_type_groups": 1,
             "estimated_map_calls": 1, "estimated_tokens": 1000},
        ),
    )
    monkeypatch.setattr(
        target_objectives,
        "generate_target_objectives",
        lambda *args, **kwargs: {
            "source_document_ids": ["architecture.md"],
            "target_objectives": [],
            "warnings": [],
        },
    )
    app = FastAPI()
    app.include_router(sra.router)

    response = TestClient(app).post(
        "/api/sra/projects/project-1/target-objectives/generate",
        json={"document_ids": ["architecture.md"]},
    )
    events = _events(response.text)

    assert response.status_code == 200
    assert next(event for event in events if event["type"] == "error")["message"] == (
        "RuntimeError: mongo lost before save"
    )
    metrics = next(event["metrics"] for event in events if event["type"] == "task_metrics")
    assert metrics["status"] == "failed"
    assert not any(event["type"] == "done" for event in events)
