#!/usr/bin/env bash
# ============================================================
# verify_stack.sh -- check that this project's live stack is healthy.
#
# Verifies each service responds AND that Prometheus is actually scraping the
# API. A healthy Prometheus with a down target is a silent failure that a plain
# health check would miss.
#
# Usage: ./verify_stack.sh
# Exit code is 0 only when every check passes.
# ============================================================
set -uo pipefail

API_PORT=8000
MLFLOW_PORT=5001
MINIO_PORT=9000
PROM_PORT=9090
GRAFANA_PORT=3001
PROJECT="aeropredict"

GREEN='\033[0;32m'; RED='\033[0;31m'; BLUE='\033[1;34m'; NC='\033[0m'
PASS=0; FAIL=0

check() {
  local desc="$1" url="$2" expect="${3:-200}" host="${4:-}"
  local code
  if [ -n "$host" ]; then
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -H "Host: $host" "$url" 2>/dev/null)
  else
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$url" 2>/dev/null)
  fi
  if [ "$code" = "$expect" ]; then
    printf '  %b\u2713%b %s\n' "$GREEN" "$NC" "$desc"; PASS=$((PASS + 1))
  else
    printf '  %b\u2717%b %s (got %s, want %s)\n' "$RED" "$NC" "$desc" "${code:-none}" "$expect"; FAIL=$((FAIL + 1))
  fi
}

printf '\n%b\u2550\u2550\u2550 %s \u2550\u2550\u2550%b\n' "$BLUE" "$PROJECT" "$NC"

check "API health        :$API_PORT" "http://127.0.0.1:$API_PORT/"
check "API metrics       :$API_PORT" "http://127.0.0.1:$API_PORT/metrics"
check "MLflow            :$MLFLOW_PORT" "http://127.0.0.1:$MLFLOW_PORT/api/2.0/mlflow/experiments/search?max_results=1" 200 "127.0.0.1:$MLFLOW_PORT"
check "MinIO             :$MINIO_PORT" "http://127.0.0.1:$MINIO_PORT/minio/health/live"
check "Prometheus        :$PROM_PORT" "http://127.0.0.1:$PROM_PORT/-/healthy"
check "Grafana           :$GRAFANA_PORT" "http://127.0.0.1:$GRAFANA_PORT/api/health"

targets=$(curl -s --max-time 10 "http://127.0.0.1:$PROM_PORT/api/v1/targets" 2>/dev/null)
if [ -z "$targets" ]; then
  printf '  %b\u2717%b Prometheus scrape target (no response)\n' "$RED" "$NC"; FAIL=$((FAIL + 1))
else
  summary=$(printf '%s' "$targets" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("unparseable"); raise SystemExit
ts = d["data"]["activeTargets"]
if not ts:
    print("no targets"); raise SystemExit
bad = ["{}={}".format(t["scrapeUrl"], t["health"]) for t in ts if t["health"] != "up"]
print("all up" if not bad else "down: " + ", ".join(bad))
' 2>/dev/null)
  if [ "$summary" = "all up" ]; then
    printf '  %b\u2713%b Prometheus scrape target is UP\n' "$GREEN" "$NC"; PASS=$((PASS + 1))
  else
    printf '  %b\u2717%b Prometheus scrape target: %s\n' "$RED" "$NC" "$summary"; FAIL=$((FAIL + 1))
  fi
fi

printf '\n  passed: %d\n  failed: %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
