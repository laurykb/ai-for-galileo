#!/usr/bin/env python3
"""Vérifie le contenu minimal du paquet air-gap avant transfert."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REQUIRED = (
    ".env.compose.example", "compose.yaml", "serve.py", "deploy/offline.sh",
    "docs/INSTALLATION_HORS_LIGNE.md", "images.tar",
    "models/ollama-models.tsv", "models/bge-reranker-v2-m3/model.safetensors",
    "models/ollama-store/blobs",
    "models/ollama-store/manifests", "data/mongo.archive", "SHA256SUMS",
)
FORBIDDEN_NAMES = {".env", ".env.local", ".offline-installed", "nohup.out"}
MACHINE_PATHS = ("/home/marsattacks", "/mnt/swap-files")
SECRETS = (
    re.compile(r"BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
)


def audit(root: Path) -> dict:
    errors: list[str] = []
    files = [path for path in root.rglob("*") if path.is_file()]
    for item in REQUIRED:
        if not (root / item).exists():
            errors.append(f"élément manquant : {item}")
    manifest = root / "models" / "ollama-models.tsv"
    if manifest.is_file() and not manifest.read_text(encoding="utf-8").strip():
        errors.append("manifeste Ollama vide")
    for path in files:
        if path.name == "OFFLINE_AUDIT.json":
            continue
        relative = path.relative_to(root)
        if path.stat().st_size > 2_000_000:
            continue
        if path.name in FORBIDDEN_NAMES:
            errors.append(f"artefact de machine interdit : {relative}")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if any(pattern.search(text) for pattern in SECRETS):
            errors.append(f"secret potentiel : {relative}")
        if any(machine_path in text for machine_path in MACHINE_PATHS):
            errors.append(f"chemin propre à la machine source : {relative}")
        endpoints = ("api." + "openai.com", "api." + "anthropic.com")
        if any(endpoint in text for endpoint in endpoints):
            errors.append(f"endpoint cloud : {relative}")
    return {"ok": not errors, "root": str(root), "files": len(files),
            "bytes": sum(path.stat().st_size for path in files),
            "errors": sorted(set(errors))}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = audit(args.root.resolve())
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.report:
        args.report.write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
