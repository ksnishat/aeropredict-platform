#!/usr/bin/env python3
"""Fail when this repository references something outside itself.

Each project repository is meant to stand alone: clone it, run it, reproduce
it. A reference to a sibling project or to the workspace root breaks that
promise silently — the file exists on the author's machine and nowhere else.

This caught `load_test.py` pointing at `job_preparation/load_test_results.json`
and at three sibling projects' datasets, which made `make load-test` fail on
every fresh clone.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Paths that only exist in the workspace root, never in a standalone clone.
FORBIDDEN = [
    "job_preparation",
    "mlops-portfolio",
]

# Sibling repository directory names.
SIBLINGS = [
    "aeropredict-platform",
    "infralens",
    "rossmann-sales-pipeline",
    "ovoscan-pipeline",
]

TEXT_SUFFIXES = {".py", ".sh", ".yml", ".yaml", ".md", ".toml", ".cfg", ".ini", ".json"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "runs", "mlruns"}


def tracked_files() -> list[pathlib.Path]:
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
    return [ROOT / f for f in out.stdout.split() if f]


def main() -> int:
    own_name = ROOT.name
    problems: list[str] = []

    for path in tracked_files():
        if path.suffix not in TEXT_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        # This script necessarily contains the forbidden strings in its own
        # lists, so it would always flag itself.
        if path.name == "check_selfcontained.py":
            continue

        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue

        rel = path.relative_to(ROOT)

        for token in FORBIDDEN:
            for m in re.finditer(re.escape(token), text):
                line = text[: m.start()].count("\n") + 1
                problems.append(f"{rel}:{line}: references '{token}', which is not in this repository")

        for sib in SIBLINGS:
            if sib == own_name:
                continue
            # Only flag it when used as a path, not when merely named in prose.
            for m in re.finditer(rf"(?:\.\./|/|\"|'){re.escape(sib)}/", text):
                line = text[: m.start()].count("\n") + 1
                problems.append(f"{rel}:{line}: references sibling repository '{sib}'")

    if problems:
        print("This repository is not self-contained:")
        for p in problems:
            print(f"  {p}")
        return 1

    print(f"Checked {len(tracked_files())} tracked files: no external references.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
