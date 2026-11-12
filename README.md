# Mega AI — self-hosted internal AI chat for Mega Trust Group

Branded **Mega AI** platform (built on [LibreChat](https://github.com/danny-avila/LibreChat)) for teams in
**Egypt / KSA / UAE**. Web + installable desktop/Android app (PWA), per-user token budgets for API models,
unlimited for local models, roles/permissions, and an Agentic mode with MCP tools.

This repo is the **deployment layer only** (compose + config + branding). It pulls the official
LibreChat images — no application source lives here, and **no secrets are committed**.

---

## What's inside
| File | Purpose |
|---|---|
| `docker-compose.yml` | Full stack: LibreChat API, MongoDB, Meilisearch, pgvector, RAG, admin panel, Caddy (auto-HTTPS) |
| `Caddyfile` | Reverse proxy + Let's Encrypt TLS for `$SITE_DOMAIN` |
| `librechat.yaml` | App config: token balance, Ollama endpoint, agent capabilities |
| `brand/` | Mega Trust logo, favicons/PWA icons, and the restyled `index.html` (navy+gold login) |
| `.env.example` | Environment template (copy to `.env`, fill, never commit) |
| `setup.sh` | Generates the blank secrets in `.env` |

---

## Deploy on the Hetzner server (`ai.megatrust.net`)

### 0. DNS
Point an **A record** `ai.megatrust.net` → the server's public IP (`188.40.242.124`).

### 1. Prerequisites on the server
- Docker Engine + Compose plugin (`docker --version`, `docker compose version`).
- **Ports 80 and 443 must be free.** `megatrust.net` already runs on this host — see **"Port 80/443 already in use"** below if so.

### 2. Clone & configure
```bash
git clone <this-repo-url> mega-ai && cd mega-ai
cp .env.example .env
nano .env     # set SITE_DOMAIN, ACME_EMAIL, and ANTHROPIC_API_KEY (your org key)
./setup.sh    # fills CREDS_KEY / JWT / MEILI / etc.
```

### 3. Start
```bash
docker compose up -d
docker compose logs -f api   # wait for "Server listening ... readiness checks passing"
```
Caddy obtains HTTPS automatically. Open **https://ai.megatrust.net**.

### 4. Create the admin account
Registration is locked (`ALLOW_REGISTRATION=false`), so create the first user by CLI, then promote:
```bash
docker compose exec api npm run create-user you@megatrust.net "Your Name" yourusername
docker compose exec mongodb mongosh --quiet LibreChat --eval \
  'db.users.updateOne({email:"you@megatrust.net"},{$set:{role:"ADMIN"}})'
docker compose exec api npm run set-balance you@megatrust.net 5000000
```
Add staff from the **Admin Panel** (internal only — open via SSH tunnel):
```bash
ssh -L 3000:localhost:3000 user@server     # then browse http://localhost:3000
```

---

## Token / cost model (MegaBot-style)
- API models (Claude/OpenAI) spend each user's **credit balance** — set per user in the admin panel / `set-balance`.
- Local models (Ollama) are effectively unlimited (cheap) — leave a generous balance or disable balance.
- The org API key lives **server-side** in `.env` (`ANTHROPIC_API_KEY`) so employees never see or hold it.
  (Set it to `user_provided` only if you want each user to paste their own key.)

## Local model (optional)
Run Ollama on the host and make it reachable to the containers **without exposing it to the LAN**:
```bash
# bind Ollama to the docker bridge only (not 0.0.0.0):
sudo mkdir -p /etc/systemd/system/ollama.service.d
printf '[Service]\nEnvironment="OLLAMA_HOST=172.17.0.1:11434"\n' | sudo tee /etc/systemd/system/ollama.service.d/override.conf
sudo systemctl daemon-reload && sudo systemctl restart ollama
```
`librechat.yaml` already points the "Ollama (Local)" endpoint at `http://host.docker.internal:11434/v1`.

## Port 80/443 already in use (shared host with megatrust.net)
If another web server already owns 80/443 on this host, **do not run this Caddy**. Instead:
1. In `docker-compose.yml`, remove the `caddy` service and, on the `api` service, publish `127.0.0.1:3080:3080`.
2. Add a vhost to the existing proxy (nginx/Caddy) for `ai.megatrust.net` → `http://127.0.0.1:3080`.

## Backups
Back up the Docker named volumes `mongo_data` (chats/users) and `app_uploads` regularly.

## Security notes
- Only Caddy (80/443) is public; DB/search/admin are internal. Admin panel is bound to localhost (SSH tunnel).
- Keep `.env` out of git (already in `.gitignore`). Rotate the API key centrally when needed.
- Registration stays locked; staff are invited/created by an admin.
