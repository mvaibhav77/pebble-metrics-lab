#!/usr/bin/env bash
# Fetch Pebble's OpenMetrics output with the lab's dev-only basic-auth identity.
# Extra args are passed to curl (e.g. -i to see response headers).
set -euo pipefail

PEBBLE_URL=${PEBBLE_URL:-http://localhost:4000}
METRICS_USER=${METRICS_USER:-metrics-lab}
METRICS_PASSWORD=${METRICS_PASSWORD:-pebble-lab-dev}

curl -fsS -u "$METRICS_USER:$METRICS_PASSWORD" "$@" "$PEBBLE_URL/v1/metrics"
