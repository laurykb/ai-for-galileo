#!/usr/bin/env python3
"""Validation bloquante exécutée avant chaque export."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*command: str, cwd: Path = ROOT, env: dict | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True, env=env)


def node_env() -> dict:
    env = os.environ.copy()
    candidates = [Path(path).parent for path in [shutil.which("node")] if path]
    candidates += sorted((Path.home() / ".nvm/versions/node").glob("*/bin"), reverse=True)
    for directory in candidates:
        node = directory / "node"
        if node.is_file():
            major = int(subprocess.check_output([node, "-p", "process.versions.node.split('.')[0]"], text=True))
            if major >= 20:
                env["PATH"] = f"{directory}:{env.get('PATH', '')}"
                return env
    raise RuntimeError("Node.js >= 20 est requis sur la machine de préparation.")


def main() -> int:
    run(str(ROOT / ".venv/bin/pytest"), "-q", "-p", "no:cacheprovider")
    env = node_env()
    run("npm", "run", "lint", cwd=ROOT / "web", env=env)
    run("npm", "run", "build", cwd=ROOT / "web", env=env)
    if not (ROOT / "web/.next/standalone/server.js").is_file():
        raise RuntimeError("Build Next.js standalone absent.")
    run("bash", "-n", str(ROOT / "deploy/offline.sh"))
    run("docker", "compose", "config", "--quiet")
    for relative in ("serve.py", "scripts/audit_export.py"):
        path = ROOT / relative
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    print("Application validée pour l'export.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
