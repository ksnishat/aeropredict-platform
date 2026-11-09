#!/usr/bin/env bash
# ============================================================
# start_tls_proxy.sh -- start the HTTPS reverse proxy for this project.
#
# Terminates TLS on port 8443 and forwards to the API on 8000.
# Generates a self-signed certificate on first run.
#
#   https://localhost:8443
#
# Usage: ./infrastructure/tls/start_tls_proxy.sh
# ============================================================
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTAINER="tls_aeropredict"

# Regenerate when either half is missing. The key and certificate are not
# tracked in git, so a fresh clone has neither; checking only the cert let a
# cert-without-key state reach nginx and fail.
if [ ! -f "$DIR/server.crt" ] || [ ! -f "$DIR/server.key" ]; then
  "$DIR/generate_certs.sh" localhost
fi

docker rm -f "$CONTAINER" >/dev/null 2>&1
if docker run -d --name "$CONTAINER" \
    --restart unless-stopped \
    --add-host=host.docker.internal:host-gateway \
    -p 8443:8443 \
    -v "$DIR/nginx.conf:/etc/nginx/nginx.conf:ro" \
    -v "$DIR:/etc/nginx/certs:ro" \
    nginx:alpine >/dev/null 2>&1; then
  sleep 5
  echo "TLS proxy up: https://localhost:8443"
  echo "  Self-signed certificate, so use -k with curl or accept the browser warning."
else
  echo "TLS proxy failed to start" >&2
  exit 1
fi
