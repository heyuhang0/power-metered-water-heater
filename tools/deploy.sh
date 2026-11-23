#!/usr/bin/env bash
# Copy the integration to the HA host over SSH, keeping a dated backup of the previous copy.
# Settings come from .env (see .env.example). Does not restart Home Assistant.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
: "${HA_SSH_HOST:?set HA_SSH_HOST in .env}"
PORT="${HA_SSH_PORT:-22}" USER_="${HA_SSH_USER:-root}" CFG="${HA_CONFIG_DIR:-/config}"
DOMAIN=power_metered_water_heater
STAMP=$(date +%Y-%m-%d-%H%M%S)
SSH=(ssh -o BatchMode=yes -p "$PORT" "$USER_@$HA_SSH_HOST")
tar -C custom_components --exclude='__pycache__' -czf - "$DOMAIN" | "${SSH[@]}" "set -e
  cd '$CFG'
  if [ -d custom_components/$DOMAIN ]; then
    mkdir -p backups-$DOMAIN && mv custom_components/$DOMAIN backups-$DOMAIN/$DOMAIN-$STAMP
    echo backup: $CFG/backups-$DOMAIN/$DOMAIN-$STAMP
  fi
  mkdir -p custom_components && tar -C custom_components -xzf -
  grep '\"version\"' custom_components/$DOMAIN/manifest.json"
echo "deployed. Restart Home Assistant to load it."
