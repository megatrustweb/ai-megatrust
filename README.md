# Mega AI — self-hosted internal AI platform for Mega Trust Group

Branded **Mega AI** platform (built on [LibreChat](https://github.com/danny-avila/LibreChat)) for teams in
**Egypt / KSA / UAE**. Web + installable desktop/Android app (PWA), free models with zero cost to users,
per-user daily credit quotas for paid models, access tiers with an **admin-activation gate**,
**Arabic dialect voices** (Egyptian, Saudi, Gulf…), private web search, and image/video generation.

This repo is the **deployment layer** (compose + config + branding + a small companion service).
It pulls the official LibreChat images, and **no secrets are committed**.

---

## What's inside
| Path | Purpose |
|---|---|
| `docker-compose.yml` | Full stack: LibreChat, MongoDB, Meilisearch, pgvector, RAG, Admin Panel, **mega-tools**, **SearXNG**, Caddy (auto-HTTPS) |
| `Caddyfile` | Reverse proxy + Let's Encrypt TLS; `/media/*` → generated voice-overs |
| `librechat.yaml` | Providers (Groq, Gemini, OpenRouter, Claude), free-model zero pricing, voices, web search, MCP tools |
| `services/mega-tools/` | Companion service: Arabic dialect TTS (read-aloud + MP3 voice-overs), free web search/reader MCP tools, and the "pending activation" endpoint |
| `searxng/settings.yml` | Private metasearch engine config (no API key, no tracking) |
| `scripts/provision.sh` | Creates the access tiers + sign-up gate through the official admin API (idempotent) |
| `docs/ROLES.md` | **Admin guide (Arabic)**: tiers, activating employees, delegating approvers, adding API keys |
| `brand/` | Mega Trust logo, favicons/PWA icons, restyled navy+gold login |
| `.env.example` / `setup.sh` | Environment template / secret generator |

## Features
- **Sign up with any email → ⏳ pending** until an admin (or a delegated approver) moves the user into a tier.
  Enforced server-side: bypass attempts are rejected (`Model spec mismatch`).
- **Tiers**: 💬 `CHAT_MEDIA` (free models, voice, images/video) · 💻 `CODER` (+ Claude, agents, code, GitHub) · ⭐ `POWER` (everything).
- **Free models never consume credits** (zero `tokenConfig`); Claude is billed at real prices against a **daily quota** per tier.
- **All API keys are managed from the Admin Panel** (stored encrypted), so nobody has to edit `.env`.
- **Arabic voices** (free Microsoft neural voices): Egyptian, Saudi, Emirati, Kuwaiti, Qatari, Bahraini, Omani, Levantine, Iraqi, Moroccan.
  Optional premium engine: ElevenLabs (key via MCP header `X-ElevenLabs-Key`).
- **Tools (MCP)**: `mega-tools` (voice-overs, web search, page reader, SSRF-guarded), `fal-ai` (Veo/Kling/Sora/Flux image & video),
  `github` (each coder adds their own token).

---

## Deploy on the Hetzner server (`ai.megatrust.net`)

### 0. DNS
Point an **A record** `ai.megatrust.net` → the server's public IP (`188.40.242.124`).

### 1. Prerequisites
- Docker Engine + Compose plugin.
- **Ports 80 and 443 must be free.** `megatrust.net` already runs on this host: see **"Port 80/443 already in use"** below.

### 2. Configure & start
```bash
git clone git@github.com:megatrustweb/ai-megatrust.git mega-ai && cd mega-ai
cp .env.example .env && nano .env      # SITE_DOMAIN, ACME_EMAIL
./setup.sh                              # generates all secrets
docker compose up -d --build
docker compose logs -f api              # wait for "Server readiness checks passing"
```

### 3. Admin account + tiers
1. Open **https://ai.megatrust.net** and **register your own account first**. The first account becomes ADMIN.
   (If it didn't, promote it: `docker compose exec mongodb mongosh --quiet LibreChat --eval 'db.users.updateOne({email:"you@megatrust.net"},{$set:{role:"ADMIN"}})'`)
2. Create the tiers and the sign-up gate:
   ```bash
   ./scripts/provision.sh
   ```
3. Open the **Admin Panel** through an SSH tunnel and add the API keys (Configuration → Endpoints):
   ```bash
   ssh -L 3000:localhost:3000 user@server    # then browse http://localhost:3000
   ```
See **[docs/ROLES.md](docs/ROLES.md)** for the full admin workflow.

---

## Port 80/443 already in use (shared host with megatrust.net)
If another web server already owns 80/443, **do not run this Caddy**:
1. Remove the `caddy` service; publish `127.0.0.1:3080:3080` on `api` and `127.0.0.1:8095:8000` on `mega-tools`.
2. In the existing proxy, add a vhost for `ai.megatrust.net`: `/media/*` → `127.0.0.1:8095`, everything else → `127.0.0.1:3080`,
   and make sure it sends `X-Forwarded-For` (LibreChat uses it with `TRUST_PROXY=1` to ban only the offending client).

## Backups
Back up the Docker volumes `mongo_data` (users, chats, encrypted keys, tier config) and `app_uploads` regularly.
Keep `.env` safe: `CREDS_KEY`/`CREDS_IV` decrypt the stored API keys.

## Security notes
- Only Caddy (80/443) is public; DB, search, tools and the Admin Panel are internal.
- SSRF protection stays on everywhere. Only `mega-tools:8000` (MCP + TTS) and `searxng:8080` (web search) are exempted, each scoped to its own section.
- Free provider tiers may log or train on prompts (Gemini free, some OpenRouter models). Groq doesn't train on API data. Don't send sensitive data to free tiers.
- fal.ai usage is billed by fal directly. **Set a spending limit in the fal.ai dashboard.**
