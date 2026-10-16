#!/usr/bin/env python3
"""Fail CI when a document carries a claim that is no longer true.

The most common defect in these repositories has not been a broken script — it
has been a number that was correct when written and silently became false. A
README kept showing AeroPredict's pre-fix error for weeks after the model was
fixed, because nothing checked the number against the code.

This script encodes the specific stale values that have actually appeared, so a
regression fails the build instead of waiting for someone to notice.

Each entry is (pattern, explanation). The pattern is a regex; a match fails CI.
"""

import re
import subprocess
import sys
from pathlib import Path

# Values that were once published and are now wrong. Keep the explanation
# specific — it is what the failure message shows.
STALE = [
    (r"\b869\.51\b",
     "Rossmann MAE 869.51 came from a random train/test split that leaked. "
     "The time-based split gives 876.27."),
    (r"\b1295\.21\b",
     "Rossmann RMSE 1295.21 was never computed by the code. The measured value "
     "is 1284.26."),
    (r"\b844,392\b",
     "Rossmann has 844,338 rows, not 844,392."),
    (r"\b53\.70\b",
     "AeroPredict MAE 53.70 is the pre-fix value. The model now scores 9.51. "
     "It is fine to mention 53.70 as the 'before' number, but not as current."),
    (r"\b109 unit\b",
     "AeroPredict has 138 unit tests, not 109."),
    (r"\b52 unit\b",
     "InfraLens has 75 unit tests, not 52."),
    (r"\b62 unit\b",
     "OvoScan has 86 unit tests, not 62."),
    (r"\bproduction-grade\b",
     "The systems are production-shaped, not production-grade. Auth, TLS, "
     "upload limits and self-healing are in place; RBAC, a secret manager, "
     "multi-node HA and a soak test are not."),
]

# Phrases that mark a line as deliberate history rather than a current claim.
EXEMPT_PHRASES = [
    "was 53.70",
    "earlier 869.51",
    "before three",
    "pre-fix",
    "was never computed",
    "verified against Postgres",
    "came from a random",
    "not 844,392",
    "not 109",
    "not 52",
    "not 62",
    "not production-grade",
    "production-shaped, not production-grade",
    "originally predicted",
    "took test MAE from",
    "took MAE from",
    "went **53.70",
    "Test MAE 53.70",
    "test MAE **53.70",
    "53.70 → 9.51",
    "53.70 to 9.51",
]

# A line that pairs a stale value with its replacement is a before/after
# comparison, which is the correct way to describe the history.
REPLACEMENTS = {
    "53.70": ["9.51"],
    "869.51": ["876.27"],
    "1295.21": ["1284.26"],
    "844,392": ["844,338"],
    "109 unit": ["138"],
    "52 unit": ["75"],
    "62 unit": ["86"],
}


def is_deliberate_history(line: str) -> bool:
    """True when the line is explaining the history rather than asserting it."""
    if any(phrase in line for phrase in EXEMPT_PHRASES):
        return True
    for stale, replacements in REPLACEMENTS.items():
        if stale in line and any(r in line for r in replacements):
            return True
    return False


def tracked_docs() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.md"], capture_output=True, text=True, check=True
    ).stdout.split()
    return [Path(f) for f in out]


def main() -> int:
    failures = []
    for path in tracked_docs():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(lines, 1):
            for pattern, why in STALE:
                if re.search(pattern, line) and not is_deliberate_history(line):
                    failures.append((path, lineno, line.strip(), why))

    if failures:
        print("Stale claims found in documentation:\n")
        for path, lineno, line, why in failures:
            print(f"  {path}:{lineno}")
            print(f"    {line[:100]}")
            print(f"    -> {why}\n")
        print(f"{len(failures)} stale claim(s). Update the document or add an "
              f"exemption in .github/scripts/check_docs.py if the mention is "
              f"deliberate history.")
        return 1

    print(f"Checked {len(tracked_docs())} documents, no stale claims.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
