#!/usr/bin/env python3
"""Fail CI when a README's test counts disagree with the actual test suite.

`check_docs.py` catches values that were *known* to be wrong and hard-coded into
a blocklist. That only works for staleness someone already noticed. The counts
in these READMEs drifted by 3-6x precisely because nobody noticed, so a
blocklist could never have caught them.

This script closes that gap by deriving the truth instead of asserting it: it
runs pytest's collection, counts the tests, and compares the result against
every count the README publishes. A README that says "208 passed" when the
suite collects 209 fails the build.

Two kinds of claim are checked:

* **Command comments** — ``pytest tests/ -v   # 208 passed, 8 deselected``.
  The number before "passed" must equal the number of non-integration tests.
* **Prose and trees** — ``117 unit + 8 live integration tests`` and
  ``# 208 unit + 8 integration tests``. The first number must equal the
  non-integration count, the second the integration count.

The integration count is derived from the ``integration`` marker, which is how
the suite already separates the two groups, so the two numbers cannot drift
apart independently.

Run with ``--fix`` to rewrite the counts in place instead of failing.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# ``# 208 passed, 8 deselected`` — the count a reader would trust. Anchored to
# a line that runs the whole suite, so the separate integration-test command
# (``pytest tests/test_integration_live.py -v   # 8 passed``) is not matched:
# its count is the integration total, not the unit total.
COMMAND_CLAIM = re.compile(
    r"(?m)^(?P<prefix>[^\n#]*pytest\s+tests/\s[^\n#]*#\s*)(?P<count>\d+)(?P<suffix>\s+passed\b)"
)

# ``117 unit + 8 live integration tests`` and ``# 208 unit + 8 integration tests``
PROSE_CLAIM = re.compile(
    r"(\d+)\s+unit\s*\+\s*(\d+)\s+(?:live\s+)?integration\s+tests?"
)


def collect_counts() -> tuple[int, int]:
    """Return (non_integration_passed, integration_total).

    The unit count is the number that actually *passed*, not the number
    collected: OvoScan collects 173 non-integration tests but skips one, and a
    README claiming "172 unit tests passing" is correct while "173" would not
    be. Collection cannot distinguish those, so the suite is run once.

    The integration count comes from collection, which is enough because those
    tests are deselected in CI and their number is what the README quotes.
    """
    run = subprocess.run(
        [
            sys.executable, "-m", "pytest", "tests/",
            "-q", "--disable-warnings", "-m", "not integration",
        ],
        cwd=ROOT, capture_output=True, text=True,
    )
    m = re.search(r"(\d+)\s+passed", run.stdout)
    if not m:
        print(run.stdout[-2000:], file=sys.stderr)
        raise SystemExit("could not parse pytest results; did the suite run?")
    unit = int(m.group(1))

    collected = subprocess.run(
        [
            sys.executable, "-m", "pytest", "tests/",
            "--collect-only", "-q", "--disable-warnings", "-m", "integration",
        ],
        cwd=ROOT, capture_output=True, text=True,
    )
    m = re.search(r"(\d+)(?:/\d+)?\s+tests? collected", collected.stdout)
    if not m:
        print(collected.stdout[-2000:], file=sys.stderr)
        raise SystemExit("could not parse pytest collection output")
    return unit, int(m.group(1))


def check_file(path: Path, unit: int, integration: int, fix: bool) -> list[str]:
    """Return a list of problems; rewrite the file when ``fix`` is set."""
    text = path.read_text(encoding="utf-8")
    original = text
    problems: list[str] = []

    def replace_command(m: re.Match) -> str:
        claimed = int(m.group("count"))
        if claimed == unit:
            return m.group(0)
        problems.append(
            f"{path}: command comment claims {claimed} passed, suite collects {unit}"
        )
        return f"{m.group('prefix')}{unit}{m.group('suffix')}"

    def replace_prose(m: re.Match) -> str:
        claimed_unit, claimed_int = int(m.group(1)), int(m.group(2))
        if claimed_unit == unit and claimed_int == integration:
            return m.group(0)
        problems.append(
            f"{path}: prose claims {claimed_unit} unit + {claimed_int} integration, "
            f"actual is {unit} + {integration}"
        )
        return m.group(0).replace(
            f"{claimed_unit} unit", f"{unit} unit", 1
        ).replace(
            f"{claimed_int} integration", f"{integration} integration", 1
        ).replace(
            f"{claimed_int} live integration", f"{integration} live integration", 1
        )

    text = COMMAND_CLAIM.sub(replace_command, text)
    text = PROSE_CLAIM.sub(replace_prose, text)

    if fix and text != original:
        path.write_text(text, encoding="utf-8")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fix", action="store_true",
        help="Rewrite the counts in place instead of failing.",
    )
    args = parser.parse_args(argv)

    unit, integration = collect_counts()
    print(f"pytest: {unit} non-integration passed, {integration} integration collected")

    readme = ROOT / "README.md"
    if not readme.exists():
        print("no README.md at the repository root", file=sys.stderr)
        return 1

    problems = check_file(readme, unit, integration, args.fix)

    if problems:
        for p in problems:
            print(f"  {p}")
        if args.fix:
            print(f"\nrewrote {len(problems)} claim(s) in {readme}")
            return 0
        print(
            "\nThe README disagrees with the test suite. Run "
            "`python .github/scripts/check_readme_counts.py --fix` to update it."
        )
        return 1

    print(f"{readme.name} agrees with the suite.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
