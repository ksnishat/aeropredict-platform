#!/usr/bin/env bash
# ============================================================
# generate_certs.sh -- create a self-signed certificate for local TLS.
#
# Self-signed is correct for a demo: it proves the stack can serve HTTPS and
# that the proxy config is right, without needing a real domain. A browser will
# warn until the certificate is trusted, which is expected.
#
# For a real deployment you would use Let's Encrypt (certbot) or a certificate
# from your organisation's CA, and terminate TLS at the load balancer rather
# than in a sidecar container.
#
# Usage: ./generate_certs.sh [hostname]
# ============================================================
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${1:-localhost}"
DAYS=825

if [ -f "$DIR/server.crt" ] && [ -f "$DIR/server.key" ]; then
  echo "certificate already exists at $DIR/server.crt (delete it to regenerate)"
  exit 0
fi

openssl req -x509 -nodes -newkey rsa:2048 -days "$DAYS" \
  -keyout "$DIR/server.key" \
  -out "$DIR/server.crt" \
  -subj "/C=DE/ST=Bavaria/L=Nuremberg/O=MLOps Portfolio/CN=$HOST" \
  -addext "subjectAltName=DNS:$HOST,DNS:*.localhost,IP:127.0.0.1" \
  2>/dev/null

chmod 600 "$DIR/server.key"
chmod 644 "$DIR/server.crt"

echo "generated:"
echo "  $DIR/server.crt"
echo "  $DIR/server.key"
openssl x509 -in "$DIR/server.crt" -noout -subject -dates | sed 's/^/  /'
