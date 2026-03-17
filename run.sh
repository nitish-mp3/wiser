#!/usr/bin/with-contenv bashio
set -euo pipefail

CONFIG_PATH=/data/options.json

if [ ! -f "$CONFIG_PATH" ]; then
  echo "Configuration file not found at $CONFIG_PATH"
  exit 1
fi

exec python3 /app/main.py "$CONFIG_PATH"
