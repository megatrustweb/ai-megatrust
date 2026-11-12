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

echo
echo "Done. Next:"
echo "  1) Confirm .env has SITE_DOMAIN, ACME_EMAIL and ANTHROPIC_API_KEY set."
echo "  2) Ensure DNS A record  \$SITE_DOMAIN -> this server's public IP."
echo "  3) docker compose up -d"
echo "  4) Create your admin account — see README.md (register, then promote via mongo)."
