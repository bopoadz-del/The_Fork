#!/usr/bin/env bash
# Route theshovel.ai through a Cloudflare named tunnel → Render origin.
# Prerequisite: cloudflared tunnel login (cert.pem in ~/.cloudflared/)
#
# NOTE: This is an alternative to direct CNAME → the-fork.onrender.com.
# Render custom-domain SSL verification expects direct CNAME; tunnel routing
# proxies via Cloudflare. Prefer setup-theshovel-dns.sh + API token when possible.
set -euo pipefail

TUNNEL_NAME="${TUNNEL_NAME:-theshovel}"
DOMAIN="${DOMAIN:-theshovel.ai}"
WWW="${WWW:-www.theshovel.ai}"
ORIGIN="${ORIGIN:-https://the-fork.onrender.com}"
CF_DIR="${HOME}/.cloudflared"

if [[ ! -f "${CF_DIR}/cert.pem" ]]; then
  echo "Missing ${CF_DIR}/cert.pem — run: cloudflared tunnel login"
  echo "Open the URL in a browser, select zone ${DOMAIN}, click Authorize."
  exit 1
fi

echo "=== Create tunnel ${TUNNEL_NAME} (idempotent if exists) ==="
cloudflared tunnel list | rg -q "${TUNNEL_NAME}" || cloudflared tunnel create "${TUNNEL_NAME}"

TUNNEL_ID=$(cloudflared tunnel list --output json | python3 -c "
import json,sys,os
name=os.environ['TUNNEL_NAME']
for t in json.load(sys.stdin):
    if t.get('name')==name:
        print(t['id']); break
" TUNNEL_NAME="${TUNNEL_NAME}")

echo "TUNNEL_ID=${TUNNEL_ID}"

CREDS="${CF_DIR}/${TUNNEL_ID}.json"
CONFIG="${CF_DIR}/config-theshovel.yml"

cat > "${CONFIG}" <<YAML
tunnel: ${TUNNEL_ID}
credentials-file: ${CREDS}
ingress:
  - hostname: ${DOMAIN}
    service: ${ORIGIN}
  - hostname: ${WWW}
    service: ${ORIGIN}
  - service: http_status:404
YAML

echo "=== Route DNS (uses login cert, no API token) ==="
cloudflared tunnel route dns "${TUNNEL_NAME}" "${DOMAIN}" || true
cloudflared tunnel route dns "${TUNNEL_NAME}" "${WWW}" || true

echo ""
echo "=== Config written: ${CONFIG} ==="
echo "Run tunnel (must stay up): cloudflared tunnel --config ${CONFIG} run ${TUNNEL_NAME}"
echo "Or install as service: cloudflared service install --config ${CONFIG}"
