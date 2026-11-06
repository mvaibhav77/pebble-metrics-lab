#!/usr/bin/env bash
# Run the failed-vs-stopped simulation and summarise it.
# Usage: scripts/simulate.sh [duration_seconds] [crash_loop_window_seconds]
set -euo pipefail

APP_CONTAINER=${APP_CONTAINER:-metrics-lab-app}
DURATION=${1:-120}
WINDOW=${2:-30}
cd "$(dirname "$0")/.."
mkdir -p results
stamp=$(date +%Y%m%d-%H%M%S)
csv="results/sim-$stamp.csv"
report="results/sim-$stamp.md"

docker exec -i "$APP_CONTAINER" python3 - "$DURATION" <scripts/simulate.py >"$csv"
python3 scripts/analyze_sim.py "$csv" "$WINDOW" | tee "$report"
echo
echo "Raw data: $csv"
echo "Report:   $report"
