#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8088}"
CHANNEL_ID="${1:-}"

if [[ -z "$CHANNEL_ID" ]]; then
  echo "usage: $0 <channel-id>"
  exit 2
fi

echo "Health:"
curl -fsS "$BASE_URL/health"
echo

echo "Stream diagnostic:"
curl -fsS "$BASE_URL/api/checks/stream/$CHANNEL_ID"
echo

echo "Live startup diagnostic:"
curl -fsS "$BASE_URL/api/checks/stream/$CHANNEL_ID?live=true"
echo

echo "Testing AAC stream headers:"
curl -sSI "$BASE_URL/stream/$CHANNEL_ID.aac" | head
