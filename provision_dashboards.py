#!/usr/bin/env python3
"""
provision_dashboards.py

Wire each project's Grafana to its Prometheus datasource and install a
project-specific dashboard. Run after start_all_stacks.sh.

Usage: python3 provision_dashboards.py
"""
from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request

AUTH = "admin:admin"

# project -> (grafana_port, prometheus_container, api_job, metric_prefix)
TARGETS = {
    "aeropredict": (3001, "prom_aeropredict", "aeropredict-api", "aeropredict"),
    "infralens": (3002, "prom_infralens", "infralens-api", "infralens"),
    "rossmann": (3003, "prom_rossmann", "rossmann-api", "rossmann"),
    "ovoscan": (3000, "prom_ovoscan", "ovoscan-api", "ovoscan"),
}

# project -> list of (title, expr, type)
PANELS: dict[str, list[tuple[str, str, str]]] = {
    "aeropredict": [
        ("API Up", 'up{job="aeropredict-api"}', "stat"),
        ("Model Loaded", "aeropredict_model_loaded", "stat"),
        ("Last Predicted RUL (cycles)", "aeropredict_last_rul_value", "stat"),
        ("Predictions by Risk Level", "aeropredict_predictions_total", "timeseries"),
        ("Prediction Latency p50", "histogram_quantile(0.50, rate(aeropredict_prediction_latency_seconds_bucket[5m]))", "timeseries"),
        ("Prediction Latency p95", "histogram_quantile(0.95, rate(aeropredict_prediction_latency_seconds_bucket[5m]))", "timeseries"),
        ("Max Feature Drift (PSI)", "aeropredict_max_drift_psi", "stat"),
        ("Drift PSI by Sensor", "aeropredict_feature_drift_psi", "timeseries"),
        ("Reports by Source", "aeropredict_reports_total", "timeseries"),
    ],
    "infralens": [
        ("API Up", 'up{job="infralens-api"}', "stat"),
        ("Model Loaded", "infralens_model_loaded", "stat"),
        ("Requests by Status", "infralens_requests_total", "timeseries"),
        ("Detections by Class", "infralens_detections_total", "timeseries"),
        ("Inference Latency p95", "histogram_quantile(0.95, rate(infralens_inference_latency_seconds_bucket[5m]))", "timeseries"),
        ("Detection Drift (PSI)", "infralens_detection_drift_psi", "stat"),
        ("Drift Status", "infralens_drift_status", "stat"),
    ],
    "rossmann": [
        ("API Up", 'up{job="rossmann-api"}', "stat"),
        ("Model Loaded", "rossmann_model_loaded", "stat"),
        ("Last Predicted Sales", "rossmann_last_predicted_sales", "stat"),
        ("Predictions by Source", "rossmann_predictions_total", "timeseries"),
        ("Prediction Latency p95", "histogram_quantile(0.95, rate(rossmann_prediction_latency_seconds_bucket[5m]))", "timeseries"),
        ("Max Feature Drift (PSI)", "rossmann_max_drift_psi", "stat"),
        ("Drift PSI by Feature", "rossmann_feature_drift_psi", "timeseries"),
    ],
    "ovoscan": [
        ("API Up", 'up{job="ovoscan-api"}', "stat"),
        ("Model Loaded", "ovoscan_model_loaded", "stat"),
        ("Predictions by Class", "ovoscan_predictions_total", "timeseries"),
        ("Mean Confidence", "rate(ovoscan_prediction_confidence_sum[5m]) / rate(ovoscan_prediction_confidence_count[5m])", "timeseries"),
        ("Inference Latency p95", "histogram_quantile(0.95, rate(ovoscan_prediction_latency_seconds_bucket[5m]))", "timeseries"),
        ("Prediction Drift (PSI)", "ovoscan_prediction_drift_psi", "stat"),
        ("Drift Status", "ovoscan_drift_status", "stat"),
        ("Inference Errors", "ovoscan_inference_errors_total", "timeseries"),
    ],
}


def ok(msg: str) -> None:
    print(f"  \033[0;32m✓\033[0m {msg}")


def bad(msg: str) -> None:
    print(f"  \033[0;31m✗\033[0m {msg}")


def api(port: int, path: str, method: str = "GET", payload: dict | None = None):
    url = f"http://localhost:{port}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    import base64

    token = base64.b64encode(AUTH.encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_body": e.read().decode()[:200]}
    except Exception as e:  # noqa: BLE001
        return {"_error": str(e)}


def prometheus_ip(container: str) -> str | None:
    try:
        out = subprocess.run(
            ["docker", "inspect", container, "--format",
             "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def build_panels(project: str, uid: str) -> list[dict]:
    panels, x, y, pid = [], 0, 0, 1
    for title, expr, ptype in PANELS[project]:
        h, w = (5, 6) if ptype == "stat" else (8, 12)
        if x + w > 24:
            x, y = 0, y + h
        panels.append({
            "id": pid,
            "title": title,
            "type": ptype,
            "datasource": {"type": "prometheus", "uid": uid},
            "gridPos": {"h": h, "w": w, "x": x, "y": y},
            "targets": [{
                "expr": expr, "refId": "A",
                "datasource": {"type": "prometheus", "uid": uid},
            }],
        })
        x += w
        pid += 1
    return panels


def main() -> None:
    import sys

    # Optional project filter so a single repository can provision just its own
    # dashboard: `python3 provision_dashboards.py aeropredict`.
    requested = sys.argv[1:]
    unknown = [p for p in requested if p not in TARGETS]
    if unknown:
        print(f"unknown project(s): {', '.join(unknown)}")
        print(f"known: {', '.join(TARGETS)}")
        raise SystemExit(2)
    selected = requested or list(TARGETS)

    for project in selected:
        gport, pcontainer, _job, _prefix = TARGETS[project]
        print(f"\n\033[1m═══ {project} (grafana :{gport}) ═══\033[0m")

        p_ip = prometheus_ip(pcontainer)
        if not p_ip:
            bad(f"prometheus container {pcontainer} not found")
            continue
        ok(f"prometheus at {p_ip}:9090")

        existing = api(gport, "/api/datasources")
        uid = ""
        if isinstance(existing, list):
            for d in existing:
                if d.get("name") == "Prometheus":
                    uid = d.get("uid", "")
                    api(gport, f"/api/datasources/uid/{uid}", method="DELETE")
                    break

        created = api(gport, "/api/datasources", method="POST", payload={
            "name": "Prometheus", "type": "prometheus",
            "url": f"http://{p_ip}:9090", "access": "proxy", "isDefault": True,
            "jsonData": {"httpMethod": "POST"},
        })
        uid = created.get("datasource", {}).get("uid", "") if isinstance(created, dict) else ""
        if not uid:
            bad(f"datasource failed: {str(created)[:140]}")
            continue
        ok(f"datasource uid={uid}")

        health = api(gport, f"/api/datasources/uid/{uid}/health")
        status = health.get("status") if isinstance(health, dict) else None
        ok(f"datasource health: {status}")

        dash = api(gport, "/api/dashboards/db", method="POST", payload={
            "dashboard": {
                "title": f"{project} — Live Monitoring",
                "tags": [project, "mlops"],
                "refresh": "10s",
                "schemaVersion": 39,
                "panels": build_panels(project, uid),
            },
            "overwrite": True,
        })
        url = dash.get("url") if isinstance(dash, dict) else None
        if url:
            ok(f"dashboard http://localhost:{gport}{url}")
        else:
            bad(f"dashboard failed: {str(dash)[:160]}")


if __name__ == "__main__":
    main()
