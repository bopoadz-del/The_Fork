#!/usr/bin/env bash
# Configure theshovel.ai DNS for Render custom domain.
# Requires CLOUDFLARE_API_TOKEN (Bearer secret from "Your API Token" — NOT account ID).
#
# Official docs:
#   https://developers.cloudflare.com/fundamentals/api/how-to/make-api-calls/
#   https://developers.cloudflare.com/api/resources/dns/subresources/records/methods/create/
set -euo pipefail

DOMAIN="${DOMAIN:-theshovel.ai}"
RENDER_CNAME="${RENDER_CNAME:-the-fork.onrender.com}"
TOKEN="${CLOUDFLARE_API_TOKEN:?Set CLOUDFLARE_API_TOKEN to the Bearer secret (cfat_/cfut_/40-char string)}"

echo "=== 1) Verify token ==="
VERIFY=$(curl -sS "https://api.cloudflare.com/client/v4/user/tokens/verify" \
  -H "Authorization: Bearer $TOKEN")
echo "$VERIFY" | python3 -m json.tool
echo "$VERIFY" | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get('success') else 1)" \
  || { echo "Token verify failed — use the secret from 'Your API Token', not Account ID."; exit 1; }

echo ""
echo "=== 2) Resolve zone ID for $DOMAIN ==="
ZONE_JSON=$(curl -sS "https://api.cloudflare.com/client/v4/zones?name=$DOMAIN" \
  -H "Authorization: Bearer $TOKEN")
ZONE_ID=$(echo "$ZONE_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['result'][0]['id'] if d.get('result') else '')")
if [[ -z "$ZONE_ID" ]]; then
  echo "$ZONE_JSON" | python3 -m json.tool
  echo "Could not find zone $DOMAIN"
  exit 1
fi
echo "ZONE_ID=$ZONE_ID"

add_cname() {
  local name="$1"
  echo ""
  echo "=== CNAME $name -> $RENDER_CNAME (proxied=false) ==="
  curl -sS -X POST "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"type\":\"CNAME\",\"name\":\"$name\",\"content\":\"$RENDER_CNAME\",\"proxied\":false,\"ttl\":1}" \
    | python3 -m json.tool
}

add_cname "@"
add_cname "www"

echo ""
echo "=== 3) Trigger Render domain verify ==="
RENDER_API_KEY="${RENDER_API_KEY:-rnd_QqJ5qS97qrfF0IwAVrJhmKpJyNX0}"
RENDER_SERVICE="${RENDER_SERVICE:-srv-d8hdc6ek1jcs739rq5sg}"
curl -sS -X POST \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  "https://api.render.com/v1/services/$RENDER_SERVICE/custom-domains/$DOMAIN/verify" || true

echo ""
echo "=== 4) Health check (may take a few minutes to propagate) ==="
sleep 5
curl -sS "https://$DOMAIN/v1/health" || echo "(not ready yet — retry in 5–30 min)"
