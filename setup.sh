#!/usr/bin/env bash
# Mega AI — one-time secret generator. Idempotent: only fills EMPTY secret fields.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example — edit SITE_DOMAIN, ACME_EMAIL, ANTHROPIC_API_KEY before starting."
fi

gen() { openssl rand -hex "$1"; }

set_if_empty() {
  local key="$1" val="$2" cur
  # Add the key if this .env predates it (ensure the file ends with a newline first).
  if ! grep -qE "^${key}=" .env; then
    [ -n "$(tail -c1 .env)" ] && echo >> .env
    echo "${key}=" >> .env
  fi
  cur="$(grep -E "^${key}=" .env | cut -d= -f2- || true)"
  if [ -z "${cur}" ]; then
    awk -v k="$key" -v v="$val" 'BEGIN{FS="="} $1==k{print k"="v; next} {print}' .env > .env.tmp && mv .env.tmp .env
    echo "  generated ${key}"
  else
    echo "  kept existing ${key}"
  fi
}

echo "Generating secrets (empty fields only)…"
set_if_empty CREDS_KEY                 "$(gen 32)"
set_if_empty CREDS_IV                  "$(gen 16)"
set_if_empty JWT_SECRET                "$(gen 32)"
set_if_empty JWT_REFRESH_SECRET        "$(gen 32)"
set_if_empty MEILI_MASTER_KEY          "$(gen 16)"
set_if_empty ADMIN_PANEL_SESSION_SECRET "$(gen 32)"
set_if_empty POSTGRES_PASSWORD         "$(gen 16)"
set_if_empty MEGA_TOOLS_KEY            "$(gen 24)"
set_if_empty SEARXNG_SECRET            "$(gen 32)"

echo
echo "Done. Next:"
echo "  1) Confirm .env has SITE_DOMAIN and ACME_EMAIL set."
echo "  2) Ensure DNS A record  \$SITE_DOMAIN -> this server's public IP."
echo "  3) docker compose up -d --build"
echo "  4) Register YOUR account at https://\$SITE_DOMAIN (first account), then promote it:"
echo "       docker compose exec mongodb mongosh --quiet LibreChat --eval 'db.users.updateOne({email:\"you@megatrust.net\"},{\$set:{role:\"ADMIN\"}})'"
echo "  5) ./scripts/provision.sh     # creates the access tiers + sign-up gate"
echo "  6) Add API keys in the Admin Panel (ssh -L 3000:localhost:3000 …) → Configuration."
