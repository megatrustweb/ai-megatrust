#!/usr/bin/env bash
# Mega AI — create the access tiers + sign-up gate through LibreChat's official admin API.
# A temporary service admin (random password, never shown) is created, used, and deletes itself.
# Idempotent: safe to re-run after config changes.
#   Production:  ./scripts/provision.sh
#   Other stack: COMPOSE_DIR=/path/to/compose/dir ./scripts/provision.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${COMPOSE_DIR:-$ROOT}"

TS="$(date +%s)"
EMAIL="svc-provision-${TS}@megatrust.local"
PW="$(openssl rand -hex 20)"

cleanup() {
  # Safety net: if the self-delete did not happen, remove the (data-less) service account.
  docker compose exec -T mongodb mongosh --quiet LibreChat --eval "
    const u = db.users.findOne({email:'${EMAIL}'});
    if (u) { db.balances.deleteMany({user:u._id}); db.users.deleteOne({_id:u._id}); print('  removed leftover service account'); }
  " >/dev/null 2>&1 || true
  unset PW
}
trap cleanup EXIT

echo "• Creating temporary service admin…"
docker compose exec -T api npm run create-user -- "$EMAIL" "Mega Provision Service" "svc-provision-${TS}" "$PW" --email-verified=true >/dev/null
docker compose exec -T mongodb mongosh --quiet LibreChat --eval "db.users.updateOne({email:'${EMAIL}'},{\$set:{role:'ADMIN'}})" >/dev/null

echo "• Provisioning tiers (USER=pending, CHAT_MEDIA, CODER, POWER)…"
docker compose exec -T -e MEGA_SVC_EMAIL="$EMAIL" -e MEGA_SVC_PASSWORD="$PW" mega-tools \
  python - http://api:3080 < "$ROOT/scripts/provision_tiers.py"

echo "• Done. Activate people in the Admin Panel → Users (move them out of USER into a tier)."
