#!/usr/bin/env bash
# ============================================================
# run_stack.sh -- start this project's live stack.
#
# Self-contained: run it from the repository root after cloning. It starts
# the API plus MLflow, MinIO, Prometheus and Grafana on this project's
# assigned ports, so all four portfolio projects can run side by side.
#
#   API        :8000
#   MLflow     :5001
#   MinIO      :9000 (console :9001)
#   Prometheus :9090
#   Grafana    :3001
#
# Usage: ./run_stack.sh
# ============================================================
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$DIR/.stack-logs"
mkdir -p "$LOG_DIR"

API_PORT=8000
MLFLOW_PORT=5001
MINIO_PORT=9000
MINIO_CONSOLE_PORT=9001
PROM_PORT=9090
GRAFANA_PORT=3001

PROJECT="aeropredict"
ENV_NAME="aeropredict-env"
API_IMPORT="from api import app"
MODEL_ENV="MODEL_PATH=models/lstm_best.pt"

log()  { printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
ok()   { printf '  \033[0;32m\u2713\033[0m %s\n' "$*"; }
bad()  { printf '  \033[0;31m\u2717\033[0m %s\n' "$*"; }

wait_health() {
  local port=$1 tries=${2:-40}
  for _ in $(seq 1 "$tries"); do
    curl -sf -o /dev/null --max-time 2 "http://localhost:$port/" 2>/dev/null && return 0
    sleep 1
  done
  return 1
}

# ---------------------------------------------------------------- API
if curl -sf -o /dev/null --max-time 2 "http://localhost:$API_PORT/" 2>/dev/null; then
  ok "API already up on $API_PORT"
else
  log "starting API ($ENV_NAME) on :$API_PORT"
  cat > "$LOG_DIR/run_api.py" <<PY
import os
import sys
sys.path.insert(0, os.getcwd())
sys.path.insert(0, "src")
import uvicorn
$API_IMPORT
uvicorn.run(app, host="0.0.0.0", port=$API_PORT, log_level="error")
PY
  (
    cd "$DIR" || exit 1
    env $MODEL_ENV nohup conda run -n "$ENV_NAME" --cwd "$DIR" \
      python "$LOG_DIR/run_api.py" > "$LOG_DIR/api.log" 2>&1 &
  )
  if wait_health "$API_PORT"; then ok "API :$API_PORT healthy"; else bad "API did not start (see $LOG_DIR/api.log)"; fi
fi

# ------------------------------------------------------------- MLflow
if curl -sf -o /dev/null --max-time 2 -H "Host: localhost" "http://localhost:$MLFLOW_PORT/" 2>/dev/null; then
  ok "MLflow already up on $MLFLOW_PORT"
else
  log "starting MLflow on :$MLFLOW_PORT"
  (
    cd "$DIR" || exit 1
    # --allowed-hosts REPLACES MLflow's defaults and matching is exact, so every
    # host:port pair we use must be listed; "127.0.0.1" alone does not match a
    # "127.0.0.1:$MLFLOW_PORT" Host header.
    nohup conda run -n "$ENV_NAME" --cwd "$DIR" mlflow server \
      --host 0.0.0.0 --port "$MLFLOW_PORT" \
      --backend-store-uri "sqlite:///$DIR/mlflow.db" \
      --default-artifact-root "$DIR/mlruns" \
      --allowed-hosts "localhost,localhost:$MLFLOW_PORT,127.0.0.1,127.0.0.1:$MLFLOW_PORT,host.docker.internal,host.docker.internal:$MLFLOW_PORT,mlflow,mlflow:$MLFLOW_PORT" \
      > "$LOG_DIR/mlflow.log" 2>&1 &
  )
  for _ in $(seq 1 40); do
    curl -sf -o /dev/null --max-time 2 -H "Host: localhost" "http://localhost:$MLFLOW_PORT/" 2>/dev/null && break
    sleep 1
  done
  if curl -sf -o /dev/null --max-time 2 -H "Host: localhost" "http://localhost:$MLFLOW_PORT/" 2>/dev/null; then
    ok "MLflow :$MLFLOW_PORT"
  else
    bad "MLflow did not start (see $LOG_DIR/mlflow.log)"
  fi
fi

# -------------------------------------------------------------- MinIO
if curl -sf -o /dev/null --max-time 2 "http://localhost:$MINIO_PORT/minio/health/live" 2>/dev/null; then
  ok "MinIO already up on $MINIO_PORT"
else
  log "starting MinIO on :$MINIO_PORT"
  mkdir -p "$DIR/.minio-data"
  docker rm -f "minio_$PROJECT" >/dev/null 2>&1
  # minio/minio was removed from Docker Hub, so use the Bitnami legacy image.
  # It defaults to uid 1001, which cannot write a volume created by an older
  # root-owned container, hence --user root. The explicit command is required:
  # Bitnami's own run.sh does not bind the console port.
  if docker run -d --name "minio_$PROJECT" \
      -p "${MINIO_PORT}:9000" -p "${MINIO_CONSOLE_PORT}:9001" \
      --user root \
      -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin \
      -v "$DIR/.minio-data:/data" \
      bitnamilegacy/minio:latest server /data --console-address ":9001" >/dev/null 2>&1; then
    for _ in $(seq 1 30); do
      curl -sf -o /dev/null --max-time 2 "http://localhost:$MINIO_PORT/minio/health/live" 2>/dev/null && break
      sleep 1
    done
    if curl -sf -o /dev/null --max-time 2 "http://localhost:$MINIO_PORT/minio/health/live" 2>/dev/null; then
      ok "MinIO :$MINIO_PORT (console :$MINIO_CONSOLE_PORT, minioadmin/minioadmin)"
    else
      bad "MinIO did not become healthy"
    fi
  else
    bad "MinIO container failed to start"
  fi
fi

# --------------------------------------------------------- Prometheus
PROM_CFG="$LOG_DIR/prometheus.yml"
cat > "$PROM_CFG" <<EOF
global:
  scrape_interval: 15s
scrape_configs:
  - job_name: '$PROJECT-api'
    static_configs:
      - targets: ['host.docker.internal:$API_PORT']
        labels:
          service: '$PROJECT-api'
EOF

docker rm -f "prom_$PROJECT" >/dev/null 2>&1
if docker run -d --name "prom_$PROJECT" -p "${PROM_PORT}:9090" \
    --add-host=host.docker.internal:host-gateway \
    -v "$PROM_CFG:/etc/prometheus/prometheus.yml:ro" \
    prom/prometheus:v2.45.0 >/dev/null 2>&1; then
  sleep 6
  ok "Prometheus :$PROM_PORT scraping $PROJECT-api"
else
  bad "Prometheus failed to start"
fi

# ------------------------------------------------------------ Grafana
docker rm -f "graf_$PROJECT" >/dev/null 2>&1
if docker run -d --name "graf_$PROJECT" -p "${GRAFANA_PORT}:3000" \
    -e GF_SECURITY_ADMIN_USER=admin -e GF_SECURITY_ADMIN_PASSWORD=admin \
    -e GF_AUTH_ANONYMOUS_ENABLED=true -e GF_AUTH_ANONYMOUS_ORG_ROLE=Admin \
    grafana/grafana:latest >/dev/null 2>&1; then
  for _ in $(seq 1 30); do
    curl -sf -o /dev/null --max-time 2 "http://localhost:$GRAFANA_PORT/api/health" 2>/dev/null && break
    sleep 1
  done
  ok "Grafana :$GRAFANA_PORT (admin/admin)"
else
  bad "Grafana failed to start"
fi

printf '\n\033[1m═══ %s live ═══\033[0m\n' "$PROJECT"
printf '  API        http://localhost:%s/docs\n' "$API_PORT"
printf '  MLflow     http://localhost:%s\n' "$MLFLOW_PORT"
printf '  MinIO      http://localhost:%s (console)\n' "$MINIO_CONSOLE_PORT"
printf '  Prometheus http://localhost:%s\n' "$PROM_PORT"
printf '  Grafana    http://localhost:%s (admin/admin)\n' "$GRAFANA_PORT"
printf '\n  Next: python3 provision_dashboards.py %s\n' "$PROJECT"
