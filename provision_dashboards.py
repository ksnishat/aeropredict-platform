#!/usr/bin/env python3
"""
provision_dashboards.py

Verify that each project's Grafana has its datasource and dashboards, and
repair them if provisioning did not run.

The dashboards are provisioned from files at startup (see the grafana service
in docker-compose.yml and start_grafana in run_stack.sh). Those JSON files are
the single source of truth. This script reads them rather than defining its own
panels — an earlier version kept a second copy of every expression, and the two
drifted until the script was installing dashboards full of metric names that
exist in no project.

Usage:
    python3 provision_dashboards.py            # verify all four
    python3 provision_dashboards.py aeropredict  # verify one
"""
from __future__ import annotations

import base64
import json
import pathlib
import sys
import urllib.error
import urllib.request

# project -> (grafana_port, prometheus_container, password)
TARGETS = {
    "aeropredict": (3001, "prom_aeropredict", "admin"),
    "infralens": (3002, "prom_infralens", "infralens"),
    "rossmann": (3003, "prom_rossmann", "rossmann"),
    "ovoscan": (3000, "prom_ovoscan", "ovoscan"),
}

DATASOURCE_UID = "prometheus"


def ok(msg: str) -> None:
    print(f"  \033[32m✓\033[0m {msg}")


def bad(msg: str) -> None:
    print(f"  \033[31m✗\033[0m {msg}")


def api(port: int, path: str, method: str = "GET", payload: dict | None = None,
        password: str = "admin") -> object:
    url = f"http://localhost:{port}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    token = base64.b64encode(f"admin:{password}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode()
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as exc:
        return {"_error": f"HTTP {exc.code}", "_body": exc.read().decode()[:200]}
    except urllib.error.URLError as exc:
        return {"_error": f"unreachable: {exc.reason}"}


# project -> repository directory, relative to this script. The root copy
# serves all four; a per-repository copy serves only its own.
REPO_DIRS = {
    "aeropredict": "aeropredict-platform",
    "infralens": "infralens",
    "rossmann": "rossmann-sales-pipeline",
    "ovoscan": "ovoscan-pipeline",
}


def dashboard_files(project: str) -> list[pathlib.Path]:
    here = pathlib.Path(__file__).resolve().parent
    candidates = [
        here / "infrastructure/monitoring/grafana/dashboards",          # per-repo copy
        here / REPO_DIRS[project] / "infrastructure/monitoring/grafana/dashboards",
    ]
    for d in candidates:
        if d.is_dir() and any(d.glob("*.json")):
            return sorted(d.glob("*.json"))
    return []


def verify(project: str) -> bool:
    gport, pcontainer, password = TARGETS[project]
    print(f"\n\033[1m═══ {project} (grafana :{gport}) ═══\033[0m")

    files = dashboard_files(project)
    if not files:
        bad("no dashboard JSON files found")
        return False

    # 1. Datasource present, pinned uid, healthy.
    sources = api(gport, "/api/datasources", password=password)
    if not isinstance(sources, list):
        bad(f"cannot list datasources: {sources}")
        return False

    ds = next((d for d in sources if d.get("name") == "Prometheus"), None)
    if not ds:
        bad("Prometheus datasource missing — is the provisioning directory mounted?")
        return False
    if ds.get("uid") != DATASOURCE_UID:
        bad(f"datasource uid is {ds.get('uid')!r}, dashboards expect {DATASOURCE_UID!r}")
        return False
    ok(f"datasource uid={DATASOURCE_UID} url={ds.get('url')}")

    health = api(gport, f"/api/datasources/uid/{DATASOURCE_UID}/health", password=password)
    status = health.get("status") if isinstance(health, dict) else None
    if status != "OK":
        bad(f"datasource health: {status} — can Grafana resolve {pcontainer}?")
        return False
    ok("datasource health OK")

    # 2. Every dashboard file is provisioned.
    installed = api(gport, "/api/search?type=dash-db", password=password)
    titles = {d.get("title") for d in installed} if isinstance(installed, list) else set()

    missing = []
    for f in files:
        data = json.loads(f.read_text())
        if "dashboard" in data and "title" not in data:
            bad(f"{f.name} is wrapped in {{'dashboard': ...}} — file provisioning "
                "needs the dashboard object at the top level")
            return False
        title = data.get("title")
        if title in titles:
            ok(f"dashboard '{title}' ({len(data.get('panels', []))} panels)")
        else:
            missing.append((f, data))

    # 3. Repair anything missing by importing it through the API.
    for f, data in missing:
        payload = {"dashboard": {**data, "id": None}, "overwrite": True}
        res = api(gport, "/api/dashboards/db", method="POST", payload=payload,
                  password=password)
        if isinstance(res, dict) and res.get("status") == "success":
            ok(f"imported '{data.get('title')}' (was not provisioned)")
        else:
            bad(f"could not import {f.name}: {str(res)[:140]}")
            return False

    return True


def main() -> int:
    requested = sys.argv[1:]
    unknown = [p for p in requested if p not in TARGETS]
    if unknown:
        print(f"unknown project(s): {', '.join(unknown)}")
        print(f"known: {', '.join(TARGETS)}")
        return 2

    # A per-repository copy serves only its own project; the root copy serves
    # all four. Without this the per-repo script would try to reach the other
    # three Grafanas and import dashboards into whichever one answered.
    # The script's own directory name identifies which copy this is: the root
    # copy sits in a directory named after none of the repositories, so it
    # serves all four. A per-repository copy sits inside its own directory.
    here = pathlib.Path(__file__).resolve().parent
    own = next((p for p, d in REPO_DIRS.items() if here.name == d), None)
    default = [own] if own else list(TARGETS)

    selected = requested or default
    results = {p: verify(p) for p in selected}

    print()
    failed = [p for p, good in results.items() if not good]
    if failed:
        print(f"\033[31mFAILED\033[0m: {', '.join(failed)}")
        return 1
    print(f"\033[32mAll {len(results)} project(s) verified: {', '.join(results)}\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
