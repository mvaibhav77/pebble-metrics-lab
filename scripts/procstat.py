"""Runs INSIDE the app container (piped in by proc-usage.sh).

Samples /proc twice, attributes each process to a Pebble service by walking
up to its top-level ancestor (Pebble is PID 1), and prints per-service CPU and
memory next to cAdvisor's whole-container view from Prometheus.
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request

INTERVAL = max(int(sys.argv[1]) if len(sys.argv) > 1 else 30, 20)
CLK_TCK = os.sysconf("SC_CLK_TCK")
PROMETHEUS = "http://prometheus:9090"
CONTAINER = "metrics-lab-app"


def read_procs():
    procs = {}
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/stat") as f:
                stat = f.read()
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode().strip()
            rss_kb = pss_kb = 0
            with open(f"/proc/{pid}/smaps_rollup") as f:
                for line in f:
                    if line.startswith("Rss:"):
                        rss_kb = int(line.split()[1])
                    elif line.startswith("Pss:"):
                        pss_kb = int(line.split()[1])
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        # Fields after "(comm)": [0]=state [1]=ppid ... [11]=utime [12]=stime
        fields = stat[stat.rindex(")") + 2 :].split()
        procs[int(pid)] = {
            "ppid": int(fields[1]),
            "ticks": int(fields[11]) + int(fields[12]),
            "rss_kb": rss_kb,
            "pss_kb": pss_kb,
            "cmd": cmd,
        }
    return procs


def service_of(pid, procs):
    if pid == 1:
        return "pebble"
    while pid in procs and procs[pid]["ppid"] not in (0, 1):
        pid = procs[pid]["ppid"]
    top = procs.get(pid)
    if top is None or top["ppid"] == 0:
        return "docker-exec"
    if "gunicorn" in top["cmd"]:
        return "web"
    if "worker.py" in top["cmd"]:
        return "worker"
    return "other"


def prom(query):
    url = f"{PROMETHEUS}/api/v1/query?" + urllib.parse.urlencode({"query": query})
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            result = json.load(resp)["data"]["result"]
    except OSError as e:
        return f"error: {e}"
    return float(result[0]["value"][1]) if result else None


before = read_procs()
time.sleep(INTERVAL)
after = read_procs()

rows = []
for pid, p in sorted(after.items()):
    delta = p["ticks"] - before.get(pid, {"ticks": 0})["ticks"]
    rows.append((service_of(pid, after), pid, delta / CLK_TCK / INTERVAL, p["rss_kb"], p["pss_kb"], p["cmd"]))

print(f"== per-process, CPU averaged over {INTERVAL}s (processes that exited mid-window are not counted)")
print(f"{'SERVICE':<12} {'PID':>6} {'CPU(cores)':>10} {'RSS(MiB)':>9} {'PSS(MiB)':>9}  CMD")
for svc, pid, cpu, rss, pss, cmd in rows:
    print(f"{svc:<12} {pid:>6} {cpu:>10.3f} {rss / 1024:>9.1f} {pss / 1024:>9.1f}  {cmd[:60]}")

print("\n== per-service totals (RSS double-counts shared pages; PSS does not)")
print(f"{'SERVICE':<12} {'PROCS':>5} {'CPU(cores)':>10} {'RSS(MiB)':>9} {'PSS(MiB)':>9}")
totals = {}
for svc, _, cpu, rss, pss, _ in rows:
    t = totals.setdefault(svc, [0, 0.0, 0, 0])
    t[0] += 1
    t[1] += cpu
    t[2] += rss
    t[3] += pss
for svc, (n, cpu, rss, pss) in sorted(totals.items()):
    print(f"{svc:<12} {n:>5} {cpu:>10.3f} {rss / 1024:>9.1f} {pss / 1024:>9.1f}")
all_cpu = sum(t[1] for t in totals.values())
all_pss = sum(t[3] for t in totals.values())
print(f"{'ALL':<12} {len(rows):>5} {all_cpu:>10.3f} {'':>9} {all_pss / 1024:>9.1f}")

print(f"\n== cAdvisor (whole container, via Prometheus) -- name={CONTAINER}")
sel = f'{{name="{CONTAINER}"}}'
cpu = prom(f"sum(rate(container_cpu_usage_seconds_total{sel}[{INTERVAL}s]))")
for label, value, scale in [
    (f"CPU cores, rate over {INTERVAL}s", cpu, 1),
    ("memory working set (MiB)", prom(f"container_memory_working_set_bytes{sel}"), 1 / 2**20),
    ("memory rss (MiB)", prom(f"container_memory_rss{sel}"), 1 / 2**20),
    ("memory cache (MiB)", prom(f"container_memory_cache{sel}"), 1 / 2**20),
]:
    shown = f"{value * scale:.3f}" if isinstance(value, float) else value
    print(f"{label:<32} {shown}")
print("(cAdvisor has no per-service breakdown: one series per container.)")
