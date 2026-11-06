"""Summarise a simulation CSV produced by scripts/simulate.sh.

For every sample, compares Pebble's real status with the best classification
Prometheus could make from today's metrics alone:

  running        pebble_service_active == 1
  crash-looping  active == 0 and the start counter rose in the last WINDOW_S
  down-unknown   active == 0 and the counter is flat (stopped? failed?)

Usage: python3 scripts/analyze_sim.py results/sim-*.csv [window_seconds]
"""

import csv
import sys
from collections import Counter, defaultdict

TRUTH = {"active": "running", "backoff": "crash-looping", "error": "failed", "inactive": "stopped"}
TRUTH_ORDER = ["running", "crash-looping", "failed", "stopped"]
INFERRED_ORDER = ["running", "crash-looping", "down-unknown"]
SYMBOL = {"running": "R", "crash-looping": "B", "failed": "E", "stopped": "S", "down-unknown": "?"}


def load(path):
    series = defaultdict(dict)
    with open(path) as f:
        for row in csv.DictReader(f):
            series[row["service"]][int(row["t"])] = row
    return series


def infer(samples, t, window):
    row = samples[t]
    if int(row["metric_active"]) == 1:
        return "running"
    earlier = samples[max(-1, t - window)]
    if int(row["metric_start_total"]) > int(earlier["metric_start_total"]):
        return "crash-looping"
    return "down-unknown"


def table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def main():
    path = sys.argv[1]
    window = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    series = load(path)
    confusion = Counter()
    per_service = []
    timelines = []
    identical = Counter()

    for name, samples in series.items():
        ticks = sorted(t for t in samples if t >= 0)
        truth = [TRUTH.get(samples[t]["pebble_status"], "?") for t in ticks]
        inferred = [infer(samples, t, window) for t in ticks]
        for tr, inf, t in zip(truth, inferred, ticks):
            confusion[(tr, inf)] += 1
            if tr in ("failed", "stopped"):
                row = samples[t]
                identical[(tr, row["metric_active"], inf)] += 1
        n = len(ticks)
        agree = sum(1 for tr, inf in zip(truth, inferred) if tr == inf)
        per_service.append((
            name,
            ", ".join(f"{k} {v * 100 // n}%" for k, v in Counter(truth).most_common()),
            ", ".join(f"{k} {v * 100 // n}%" for k, v in Counter(inferred).most_common()),
            f"{agree * 100 // n}%",
        ))
        timelines.append((name, "".join(SYMBOL[x] for x in truth[::2]), "".join(SYMBOL[x] for x in inferred[::2])))

    total = sum(confusion.values())
    print(f"# Simulation results\n\nSource: `{path}`, {total} samples, crash-loop window {window}s.\n")
    print("## Per scenario\n")
    print(table(["Service", "Pebble status (truth)", "Inferred from current metrics", "Match"], per_service))
    print("\n## Truth vs what current metrics allow (sample counts)\n")
    print(table(["Pebble status \\ inferred"] + INFERRED_ORDER,
                [[tr] + [confusion[(tr, inf)] for inf in INFERRED_ORDER] for tr in TRUTH_ORDER]))
    correct = sum(confusion[(k, k)] for k in TRUTH_ORDER)
    print(f"\nOverall match with today's metrics: {correct}/{total} ({correct * 100 // total}%). "
          "A status metric exposing Pebble's own status would match by construction.\n")
    print("## Failed vs stopped: what Prometheus receives\n")
    print(table(["Pebble status", "pebble_service_active", "Inferred", "Samples"],
                [[k[0], k[1], k[2], v] for k, v in sorted(identical.items())]))
    print("\n## Timelines (one character per 2s)\n")
    print("R running, B backoff (crash-looping), E error (failed), S stopped, ? down-unknown\n")
    print("```")
    for name, tr, inf in timelines:
        print(f"{name:<15} truth    {tr}")
        print(f"{'':<15} metrics  {inf}")
    print("```")


if __name__ == "__main__":
    main()
