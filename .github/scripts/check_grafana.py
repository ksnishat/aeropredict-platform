#!/usr/bin/env python3
"""Fail when a Grafana dashboard references a metric the API does not expose.

A PromQL expression naming a missing metric does not error — the panel just
renders nothing. That is how all four projects shipped dashboards full of
AeroPredict's metric names without anyone noticing.

This checks the dashboard JSON against the metric names declared in the
project's own monitoring module, so it runs in CI without a live stack.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
DASHBOARDS = ROOT / "infrastructure/monitoring/grafana/dashboards"

# PromQL functions and keywords that are not metric names.
KEYWORDS = {
    "rate", "irate", "increase", "sum", "avg", "min", "max", "count",
    "histogram_quantile", "by", "without", "le", "on", "ignoring", "group_left",
    "group_right", "offset", "bool", "and", "or", "unless",
}


def declared_metrics() -> set[str]:
    """Metric names declared anywhere in the project's Python source."""
    names: set[str] = set()
    for path in ROOT.rglob("*.py"):
        if ".git" in path.parts or "__pycache__" in path.parts:
            continue
        text = path.read_text(errors="ignore")
        # prometheus_client declares names as the first argument of
        # Counter/Gauge/Histogram/Summary, or via name="...".
        for m in re.finditer(
            r'\b(?:Counter|Gauge|Histogram|Summary)\s*\(\s*["\']([a-z][a-z0-9_]*)["\']',
            text,
        ):
            names.add(m.group(1))
        for m in re.finditer(r'\bname\s*=\s*["\']([a-z][a-z0-9_]*)["\']', text):
            names.add(m.group(1))
    return names


def metric_names(expr: str) -> set[str]:
    s = re.sub(r"\{[^}]*\}", "", expr)          # drop label selectors
    s = re.sub(r"\bby\s*\([^)]*\)", "", s)      # drop grouping clauses
    s = re.sub(r"\b(?:rate|irate|increase|histogram_quantile|sum|avg|min|max|count)\s*\(", "(", s)
    return {
        m for m in re.findall(r"\b([a-z][a-z0-9_]{3,})\b", s)
        if m not in KEYWORDS
    }


def main() -> int:
    if not DASHBOARDS.is_dir():
        print(f"no dashboards directory at {DASHBOARDS}")
        return 0

    declared = declared_metrics()
    # Prometheus' own metrics and Airflow's are scraped from elsewhere.
    external_prefixes = ("up", "prometheus_", "airflow_", "process_", "python_", "go_")

    problems: list[str] = []
    checked = 0

    for path in sorted(DASHBOARDS.glob("*.json")):
        data = json.loads(path.read_text())

        # File provisioning reads the dashboard object directly. The
        # {"dashboard": {...}} wrapper is the HTTP API import shape and is
        # rejected with "Dashboard title cannot be empty".
        if "dashboard" in data and "title" not in data:
            problems.append(
                f"{path.name}: wrapped in {{'dashboard': ...}} — file provisioning "
                "needs the dashboard object at the top level"
            )
            data = data["dashboard"]

        if not data.get("title"):
            problems.append(f"{path.name}: empty title")

        for panel in data.get("panels", []):
            for target in panel.get("targets", []):
                expr = target.get("expr", "")
                checked += 1
                for name in metric_names(expr):
                    if name.startswith(external_prefixes):
                        continue
                    # Prometheus appends _bucket/_sum/_count to a histogram, so
                    # the declared name is the prefix, not the full series name.
                    base = re.sub(r"_(bucket|sum|count)$", "", name)
                    if name in declared or base in declared:
                        continue
                    if True:
                        problems.append(
                            f"{path.name}: panel '{panel.get('title')}' references "
                            f"unknown metric '{name}'"
                        )

    if problems:
        print("Grafana dashboard problems:")
        for p in problems:
            print(f"  {p}")
        return 1

    print(f"Checked {checked} panel queries against {len(declared)} declared metrics.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
