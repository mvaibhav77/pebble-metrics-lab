#!/usr/bin/env bash
# Per-process (and per-Pebble-service) CPU/memory inside the app container,
# printed next to cAdvisor's whole-container numbers for the same window.
#
# Usage: scripts/proc-usage.sh [interval_seconds]   (default 30, minimum 20)
set -euo pipefail

APP_CONTAINER=${APP_CONTAINER:-metrics-lab-app}
INTERVAL=${1:-30}
HERE=$(cd "$(dirname "$0")" && pwd)

echo "== ps (inside $APP_CONTAINER)"
docker exec "$APP_CONTAINER" ps -eo pid,ppid,pgid,rss,etimes,pcpu,args --forest
echo
docker exec -i "$APP_CONTAINER" python3 - "$INTERVAL" < "$HERE/procstat.py"
