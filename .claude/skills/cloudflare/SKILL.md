---
name: cloudflare
description: Use when configuring Cloudflare DNS, tunnels, Account API tokens, or custom domains for Render (e.g. theshovel.ai → the-fork.onrender.com). Prefer API token + CNAME; alternative is cloudflared tunnel login (no API token).
---

# Cloudflare (DNS + API tokens)

Cloudflare **deprecated Global API Key and Origin CA Key** for new work. Use **Account API Tokens** or **User API Tokens** only. All API calls use:

```http
Authorization: Bearer <API_TOKEN>
```

Never use `X-Auth-Key` / `X-Auth-Email` unless you are explicitly maintaining a legacy integration.

## Official auth model (from Cloudflare docs)

Per [Make API calls](https://developers.cloudflare.com/fundamentals/api/how-to/make-api-calls/):

1. **Every request** uses one header: `Authorization: Bearer <API_TOKEN>`
2. **Account ID / Zone ID** (32 hex) go in **URLs or query params** — never as Bearer
3. **Token secret** is shown **once** on create; copy from **"Your API Token"** (below the yellow warning)

Example from Cloudflare docs:

```bash
export ZONE_ID='f2ea6707005a4da1af1b431202e96ac5'      # identifier in path
export CLOUDFLARE_API_TOKEN='YQSn-xWAQiiEh9qM58wZNnyQS7FUdoqGIUAbrh7T'  # Bearer secret

curl "https://api.cloudflare.com/client/v4/zones/$ZONE_ID" \
  --header "Authorization: Bearer $CLOUDFLARE_API_TOKEN"
```

Verify before any work:

```bash
curl "https://api.cloudflare.com/client/v4/user/tokens/verify" \
  --header "Authorization: Bearer $CLOUDFLARE_API_TOKEN"
# success: true, status: active
```

## Do not confuse these three fields

On the "Token created successfully" modal, Cloudflare shows **Account ID** in one box and **Your API Token** in a separate box below. Only the latter is Bearer auth.

| Field | Example | Used for API auth? |
|-------|---------|-------------------|
| **Token name** | `steep-queen-72db` | No — human label only |
| **Account ID** | `f698312569a008255d809d9d48c41dfd` (also in URL: `dash.cloudflare.com/<account_id>/...`) | No — path/query identifier |
| **API token secret** | `cfat_…` or `cfut_…` or 40-char alphanumeric (shown **once** under "Your API Token") | **Yes** |

Combining token name + account ID does **not** work. Cloudflare returns `6111 Invalid format for Authorization header` for account IDs and names.

### Token secret formats (2026)

| Type | Prefix | Where to create |
|------|--------|----------------|
| Account API Token | `cfat_` | Manage Account → Account API tokens |
| User API Token | `cfut_` | My Profile → API Tokens |

Older tokens may be unprefixed 40-character alphanumeric strings; they still work until rolled.

### Verify a token before use

```bash
curl -sS "https://api.cloudflare.com/client/v4/user/tokens/verify" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" | python3 -m json.tool
```

Success: `"success": true`, `"status": "active"`.  
Failure: `6111` = wrong string (not a token). `1003` = invalid/expired token.

## Credentials in this repo

| Service | Location | Notes |
|---------|----------|-------|
| Render API | `scripts/windows/fork-tunnel-update.ps1` | `RenderApiKey`, `RenderService` |
| Cloudflare | env var `CLOUDFLARE_API_TOKEN` | **Not** committed — operator provides at runtime |

Do not commit Cloudflare tokens. If a token is leaked, roll it in the dashboard immediately.

## DNS: custom domain on Render (theshovel.ai)

**Render service:** `the-fork` (`srv-d8hdc6ek1jcs739rq5sg`)  
**Render hostname (CNAME target):** `the-fork.onrender.com`

### 1. Add domains on Render (API)

```bash
RENDER_API_KEY='rnd_…'
RENDER_SERVICE='srv-d8hdc6ek1jcs739rq5sg'

curl -sS -X POST \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"theshovel.ai"}' \
  "https://api.render.com/v1/services/$RENDER_SERVICE/custom-domains"
```

Adding the apex domain also creates `www.theshovel.ai` (redirects to apex).

Trigger verification after DNS propagates:

```bash
curl -sS -X POST \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  "https://api.render.com/v1/services/$RENDER_SERVICE/custom-domains/theshovel.ai/verify"
```

### 2. Add DNS on Cloudflare

**Dashboard (fastest):** theshovel.ai → DNS → Add record

| Type | Name | Target | Proxy |
|------|------|--------|-------|
| CNAME | `@` | `the-fork.onrender.com` | **DNS only** (grey cloud) |
| CNAME | `www` | `the-fork.onrender.com` | **DNS only** (grey cloud) |

Proxy must be **OFF** until Render issues SSL. Remove any `AAAA` records for the zone.

**API:** token needs **Zone → DNS → Edit** and **Zone → Zone → Read** for `theshovel.ai`. Workers AI / Websearch permissions are **not** sufficient.

```bash
# Resolve zone ID
curl -sS -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/zones?name=theshovel.ai"

ZONE_ID='…'

# Apex (@)
curl -sS -X POST \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"type":"CNAME","name":"@","content":"the-fork.onrender.com","proxied":false}' \
  "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records"

# www
curl -sS -X POST \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"type":"CNAME","name":"www","content":"the-fork.onrender.com","proxied":false}' \
  "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records"
```

### 3. Verify

```bash
curl -sS https://theshovel.ai/v1/health
curl -sS https://www.theshovel.ai/v1/health
```

Both should return the same healthy JSON as `https://the-fork.onrender.com/v1/health`.

Public DNS check (no auth):

```bash
curl -sS "https://cloudflare-dns.com/dns-query?name=theshovel.ai&type=CNAME" \
  -H "accept: application/dns-json"
```

## Create a token with correct permissions

1. **Manage Account → Account API tokens → Create Token**
2. Permissions: **Zone → DNS → Edit**, **Zone → Zone → Read**
3. Zone resources: include `theshovel.ai`
4. On success: copy **Your API Token** (the long secret) — **not** Account ID, **not** token name
5. If you clicked Confirm without copying: **Roll** the token to generate a new secret

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `6111 Invalid format for Authorization header` | Passed account ID, token name, or wrong string | Use full `cfat_`/`cfut_` secret from "Your API Token" |
| `1003 Authentication error` | Expired/revoked token or missing DNS permission | Roll token; add Zone DNS Edit |
| Render domain stays `unverified` | DNS missing or proxied (orange cloud) | Add CNAME records; set Proxy OFF |
| `409 domain already exists` | www created automatically with apex | Only POST apex once |
| API Keys page shows Global API Key | Legacy UI | Ignore; use Account API tokens instead |

## Alternative: cloudflared tunnel login (no API token)

When API token copy/paste is blocked, use the **cloudflared CLI cert** instead. This authorizes tunnel + DNS route operations via browser OAuth — no `cfat_` secret needed.

### 1. Login (one-time, browser)

```bash
cloudflared tunnel login
```

Opens a URL like `https://dash.cloudflare.com/argotunnel?...` — click **Authorize**, select zone **theshovel.ai**. Writes `~/.cloudflared/cert.pem`.

### 2. Route via named tunnel → Render (requires tunnel process running)

```bash
bash .claude/skills/cloudflare/setup-theshovel-tunnel.sh
cloudflared tunnel --config ~/.cloudflared/config-theshovel.yml run theshovel
```

**Caveat:** Render custom-domain SSL verification expects **direct CNAME** to `the-fork.onrender.com`. Tunnel routing proxies through Cloudflare (`*.cfargotunnel.com`). Prefer direct CNAME when possible.

### Preferred: direct CNAME (Render-verified SSL)

```bash
export CLOUDFLARE_API_TOKEN='cfat_…'   # from "Your API Token", NOT account ID
bash .claude/skills/cloudflare/setup-theshovel-dns.sh
```

## References

- [Create API token](https://developers.cloudflare.com/fundamentals/api/get-started/create-token/)
- [Make API calls](https://developers.cloudflare.com/fundamentals/api/how-to/make-api-calls/)
- [Account API tokens](https://developers.cloudflare.com/fundamentals/api/get-started/account-owned-tokens/)
- [Token formats (`cfat_` / `cfut_`)](https://developers.cloudflare.com/fundamentals/api/get-started/token-formats/)
- [Create DNS record API](https://developers.cloudflare.com/api/resources/dns/subresources/records/methods/create/)
- [Cloudflare DNS for Render](https://render.com/docs/configure-cloudflare-dns)
- [Render custom domains API](https://api-docs.render.com/reference/create-custom-domain)
