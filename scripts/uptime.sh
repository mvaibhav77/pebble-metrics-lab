#!/usr/bin/env bash
# Derive per-service "uptime" from Pebble's GET /v1/services "current-since".
#
# /v1/services needs read access over the unix socket or HTTPS; Pebble's plain
# HTTP listener only serves metrics-level identities, so we docker exec in and
# talk to the socket as root (admin).
#
# Note: current-since is the time of the service's last *status* change
# (active/inactive/backoff/error), so it is an uptime only when current=active.
set -euo pipefail

APP_CONTAINER=${APP_CONTAINER:-metrics-lab-app}

docker exec -i "$APP_CONTAINER" python3 - <<'EOF'
import http.client, json, re, socket
from datetime import datetime, timezone

class UnixConn(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect("/var/lib/pebble/default/.pebble.socket")

conn = UnixConn("localhost")
conn.request("GET", "/v1/services")
services = json.load(conn.getresponse())["result"]

now = datetime.now(timezone.utc)
print(f"{'SERVICE':<10} {'CURRENT':<9} {'CURRENT-SINCE':<32} {'SECONDS-IN-STATE':>16}")
for svc in services:
    since = svc.get("current-since")
    seconds = "-"
    if since:
        # Go emits nanoseconds; Python's fromisoformat wants at most 6 digits.
        ts = datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", since).replace("Z", "+00:00"))
        seconds = f"{(now - ts).total_seconds():.1f}"
    print(f"{svc['name']:<10} {svc['current']:<9} {since or '-':<32} {seconds:>16}")
EOF
