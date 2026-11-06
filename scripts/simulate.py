"""Run inside the app container (see scripts/simulate.sh).

Starts five scenario services side by side, then samples once per second:
- Pebble's own status for each service (GET /v1/services over the unix socket)
- what Prometheus would scrape (GET /v1/metrics over HTTP with basic auth)

Writes CSV to stdout: t,service,pebble_status,metric_active,metric_start_total
"""

import base64
import http.client
import json
import re
import socket
import subprocess
import sys
import time

DURATION_S = int(sys.argv[1]) if len(sys.argv) > 1 else 120
STOP_AT_S = 10
SOCKET = "/var/lib/pebble/default/.pebble.socket"
AUTH = base64.b64encode(b"metrics-lab:pebble-lab-dev").decode()

WORKER = "python3 /app/worker.py"
QUIET = {"WORKER_LEAK_MB_PER_S": "0", "WORKER_CPU_PERCENT": "0"}

SCENARIOS = {
    # Runs forever.
    "sim-healthy": {"env": {"WORKER_CRASH_AFTER_S": "0"}, "on-failure": "restart"},
    # Crashes 2s after each start, Pebble keeps restarting it (backoff capped at 10s).
    "sim-crashloop": {"env": {"WORKER_CRASH_AFTER_S": "2"}, "on-failure": "restart"},
    # Crashes once after 5s, Pebble does not restart it.
    "sim-failed": {"env": {"WORKER_CRASH_AFTER_S": "5"}, "on-failure": "ignore"},
    # Runs until an operator stops it at STOP_AT_S.
    "sim-stopped": {"env": {"WORKER_CRASH_AFTER_S": "0"}, "on-failure": "restart"},
    # Finishes successfully after 5s (exit 0), Pebble does not restart it.
    "sim-clean-exit": {
        "command": 'python3 -c "import time; time.sleep(5)"',
        "on-success": "ignore",
    },
}


def layer_yaml():
    lines = ["services:"]
    for name, spec in SCENARIOS.items():
        lines += [
            f"  {name}:",
            "    override: replace",
            f"    command: {spec.get('command', WORKER)}",
            "    startup: disabled",
            f"    on-failure: {spec.get('on-failure', 'restart')}",
            f"    on-success: {spec.get('on-success', 'restart')}",
            "    backoff-delay: 1s",
            "    backoff-factor: 2",
            "    backoff-limit: 10s",
        ]
        if "env" in spec:
            lines.append("    environment:")
            for key, value in {**QUIET, **spec["env"]}.items():
                lines.append(f'      {key}: "{value}"')
    return "\n".join(lines) + "\n"


class UnixConn(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(SOCKET)


def pebble_statuses():
    conn = UnixConn("localhost")
    conn.request("GET", "/v1/services")
    return {s["name"]: s["current"] for s in json.load(conn.getresponse())["result"]}


def scraped_metrics():
    conn = http.client.HTTPConnection("localhost", 4000)
    conn.request("GET", "/v1/metrics", headers={"Authorization": f"Basic {AUTH}"})
    body = conn.getresponse().read().decode()
    values = {}
    for m in re.finditer(r'^(pebble_service_\w+)\{service="([^"]+)"\} (\d+)$', body, re.M):
        values[(m.group(2), m.group(1))] = int(m.group(3))
    return values


def sample(tick):
    statuses = pebble_statuses()
    metrics = scraped_metrics()
    for name in SCENARIOS:
        active = metrics.get((name, "pebble_service_active"), 0)
        starts = metrics.get((name, "pebble_service_start_total"), 0)
        print(f"{tick},{name},{statuses.get(name, '')},{active},{starts}", flush=True)


def pebble(*args):
    subprocess.run(["pebble", *args], check=True, stdout=sys.stderr, stderr=sys.stderr)


def main():
    with open("/tmp/sim.yaml", "w") as f:
        f.write(layer_yaml())
    pebble("add", "--combine", "sim", "/tmp/sim.yaml")
    pebble("stop", *SCENARIOS)

    print("t,service,pebble_status,metric_active,metric_start_total")
    # t=-1 is a baseline taken before the scenarios start.
    sample(-1)
    pebble("start", *SCENARIOS)
    start = time.monotonic()
    stopped = False
    for tick in range(DURATION_S + 1):
        time.sleep(max(0.0, start + tick - time.monotonic()))
        if not stopped and tick >= STOP_AT_S:
            pebble("stop", "sim-stopped")
            stopped = True
        sample(tick)

    pebble("stop", *SCENARIOS)


if __name__ == "__main__":
    main()
