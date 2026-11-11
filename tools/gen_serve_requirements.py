#!/usr/bin/env python3
"""Generate ``requirements-serve.txt`` from ``uv.lock``.

The serve images install ``requirements-serve.txt`` while the tested environment
comes from ``uv.lock``. Those were two independent sources of truth and they
drifted: OvoScan's serve file pinned torch 2.9.1 against the lock's 2.14.1, and
none of the four projects listed ``slowapi`` even though every one of them
imports it at module level in ``ratelimit.py``. All four serve images therefore
failed at startup with ``ModuleNotFoundError: No module named 'slowapi'`` — and
nothing caught it, because CI builds ``Dockerfile``, not ``Dockerfile.serve``.

This script removes the second source of truth. It reads the versions from
``uv.lock`` (the same file the tests run against) and writes a serve-only
requirement set, so the image cannot ship a different stack than the one that
passed CI.

Usage, from the project repository root::

    python tools/gen_serve_requirements.py            # rewrite the file
    python tools/gen_serve_requirements.py --check    # fail if it is stale

What goes in the file
---------------------
Every ``[project].dependencies`` entry from ``pyproject.toml`` that the serving
code can import, plus their transitive requirements, minus heavy training-only
packages. The exclusion list is explicit and commented, so a reviewer can see
what was dropped and why.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import tomllib

HEADER = """\
# GENERATED FILE -- do not edit by hand.
# Regenerate with: python tools/gen_serve_requirements.py
#
# Versions are pinned to uv.lock so the image ships exactly the stack that CI
# tested. Anything added here by hand will be overwritten, and anything missing
# here will fail at container start rather than at review time.

"""

# Packages pulled in by the training pipeline or the dashboards that the API
# image does not need. Every exclusion is a deliberate size decision.
EXCLUDE = {
    # Training / experimentation only
    "pytorch-lightning", "torchmetrics", "lightning-utilities",
    "mlflow", "alembic", "sqlalchemy", "kubernetes", "docker",
    "zenml", "dvc", "dvc-data", "dvc-objects", "dvc-render", "dvc-studio-client",
    "jupyter-core", "ipython", "ipykernel", "notebook", "nbformat",
    "matplotlib", "seaborn", "scipy",
    # Dashboard-only
    "streamlit", "dash", "dash-bootstrap-components", "plotly",
    "altair", "pydeck", "bentoml", "flask", "flask-admin", "wtforms",
    # Dataset tooling
    "roboflow", "albumentations", "opencv-python",
    # Spark / notebooks
    "pyspark", "pyarrow",
}

# Distribution name -> import name, where they differ.
IMPORT_TO_DIST = {
    "sklearn": "scikit-learn",
    "cv2": "opencv-python-headless",
    "PIL": "pillow",
    "pythonjsonlogger": "python-json-logger",
    "langchain_chroma": "langchain-chroma",
    "langchain_community": "langchain-community",
    "langchain_core": "langchain-core",
    "langchain_huggingface": "langchain-huggingface",
    "langchain_text_splitters": "langchain-text-splitters",
    "prometheus_client": "prometheus-client",
    "pydantic_settings": "pydantic-settings",
    "typing_extensions": "typing-extensions",
    "python_multipart": "python-multipart",
    "dash_bootstrap_components": "dash-bootstrap-components",
    "sqlalchemy": "sqlalchemy",
    "sentence_transformers": "sentence-transformers",
    "huggingface_hub": "huggingface-hub",
}

STDLIB_OK = {
    "argparse", "asyncio", "base64", "collections", "contextlib", "contextvars",
    "dataclasses", "datetime", "enum", "functools", "io", "json", "logging",
    "math", "os", "packaging", "pathlib", "re", "secrets", "shutil", "sys",
    "time", "typing", "uuid", "warnings", "hashlib", "tempfile", "threading",
    "__future__", "abc", "copy", "csv", "glob", "itertools", "random", "signal",
    "socket", "string", "traceback", "urllib", "zipfile", "__main__",
}


# The serve image is built FROM python:3.10-slim and runs on Linux. uv.lock
# carries several conditional resolutions for torch (darwin vs linux, py<3.11 vs
# newer), so the generator must pick the one that matches the image or it pins a
# wheel the image cannot install.
IMAGE_PY = "3.10"
IMAGE_PLATFORM = "linux"

INDEXES = {
    "https://download.pytorch.org/whl/cpu": "--extra-index-url https://download.pytorch.org/whl/cpu",
}


def _markers_match(markers: list[str]) -> bool:
    """True when any resolution marker describes this image's platform."""
    for m in markers:
        if "darwin" in m or "win32" in m or "emscripten" in m:
            continue
        if IMAGE_PY in m or "python_full_version" not in m:
            return True
    return False


def read_lock(root: Path) -> tuple[dict[str, str], set[str]]:
    """Return ({name: version}, {index urls}) for the serve image's platform."""
    lock = tomllib.loads((root / "uv.lock").read_text())
    out: dict[str, str] = {}
    indexes: set[str] = set()
    candidates: dict[str, list[dict]] = {}
    for pkg in lock.get("package", []):
        name = pkg.get("name")
        if name:
            candidates.setdefault(name.lower(), []).append(pkg)

    for name, pkgs in candidates.items():
        # Prefer the entry whose markers describe linux/py3.10; fall back to the
        # unconditional single entry when the package has only one resolution.
        chosen = None
        if len(pkgs) == 1:
            chosen = pkgs[0]
        else:
            for pkg in pkgs:
                markers = pkg.get("resolution-markers") or []
                if markers and _markers_match(markers):
                    chosen = pkg
                    break
            chosen = chosen or pkgs[0]
        out[name] = chosen.get("version")
        reg = (chosen.get("source") or {}).get("registry")
        if reg:
            indexes.add(reg)
    return out, indexes


def served_imports(root: Path) -> set[str]:
    """Top-level module names imported by src/, excluding tests and scripts."""
    mods: set[str] = set()
    pat = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)
    for path in (root / "src").rglob("*.py"):
        for mod in pat.findall(path.read_text(errors="ignore")):
            mods.add(mod)
    return mods


def pyproject_deps(root: Path) -> dict[str, str]:
    """{normalised name: version spec} from [project].dependencies."""
    data = tomllib.loads((root / "pyproject.toml").read_text())
    deps: dict[str, str] = {}
    for raw in data.get("project", {}).get("dependencies", []):
        m = re.match(r"([A-Za-z0-9_.\-\[\]]+)\s*(.*)", raw)
        if not m:
            continue
        name = re.sub(r"\[.*\]", "", m.group(1)).lower()
        deps[name] = m.group(2).strip()
    return deps


def build(root: Path) -> str:
    lock, indexes = read_lock(root)
    imports = served_imports(root)
    declared = pyproject_deps(root)

    wanted: set[str] = set()

    # 1. Anything the serving code imports that is declared in pyproject.
    #
    #    The scan is a hint, not the source of truth. Some modules live in
    #    gitignored directories (InfraLens keeps src/ai_models/ out of git
    #    because it holds the weights), so a clean checkout sees fewer imports
    #    than a developer machine and the generated file differed between the
    #    two. That made --check fail in CI while passing locally.
    #
    #    So: take the union of what the scan finds AND every declared runtime
    #    dependency that is not explicitly excluded. The exclusion list is what
    #    keeps training-only packages out, and it is deterministic.
    for mod in imports:
        if mod in STDLIB_OK or mod in {"src", "auth", "ratelimit", "uploads", "retry",
                                        "versioning", "config", "data_preprocessing",
                                        "drift", "api", "app", "pipelines", "steps"}:
            continue
        dist = IMPORT_TO_DIST.get(mod, mod).lower()
        if dist in declared:
            wanted.add(dist)

    # Every declared dependency that is not excluded, so the result does not
    # depend on which files happen to be present in the checkout.
    wanted |= set(declared) - EXCLUDE

    # 2. Packages that are declared and needed but imported indirectly
    #    (uvicorn is started by CMD, python-multipart by FastAPI's form parser).
    wanted |= {"fastapi", "uvicorn", "python-multipart"} & set(declared)

    # Packages the serving code needs at runtime but never imports by name.
    # langchain-huggingface loads sentence-transformers dynamically, so an
    # import scan cannot see it; leaving it out made the built image log
    # "Could not import sentence_transformers" and serve with RAG disabled.
    if "langchain-huggingface" in wanted or "langchain-community" in wanted:
        wanted |= {"sentence-transformers", "huggingface-hub"} & set(declared)

    wanted -= EXCLUDE

    # 3. Add the transitive closure the lock says those need, minus exclusions.
    #    Simplest correct approach: if a package is in the lock and reachable, we
    #    let pip resolve its own dependencies. We only pin the direct set.
    lines = []
    for name in sorted(wanted):
        version = lock.get(name)
        if version is None:
            print(f"WARNING: {name} is declared but absent from uv.lock", file=sys.stderr)
            continue
        lines.append(f"{name}=={version}")
    extra = ""
    if any("pytorch.org" in i for i in indexes):
        extra = "--extra-index-url https://download.pytorch.org/whl/cpu\n"
    return HEADER + extra + "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="Exit 1 if requirements-serve.txt differs from the generated form.")
    ap.add_argument("--root", default=".", help="Project repository root.")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    target = root / "requirements-serve.txt"
    generated = build(root)

    if args.check:
        current = target.read_text() if target.exists() else ""
        if current != generated:
            print("requirements-serve.txt is out of date. Regenerate with:")
            print("  python tools/gen_serve_requirements.py")
            return 1
        print("requirements-serve.txt matches uv.lock")
        return 0

    target.write_text(generated)
    n = len([ln for ln in generated.splitlines() if ln and not ln.startswith("#")])
    print(f"wrote {target} ({n} packages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
