"""Tiny Flask app managed by Pebble as the "web" service."""

import os
import time

from flask import Flask, request

# Touch this file inside the container to make /health fail.
UNHEALTHY_FLAG = "/tmp/web-unhealthy"

app = Flask(__name__)


@app.get("/")
def index():
    return {"service": "web", "pid": os.getpid()}


@app.get("/health")
def health():
    if os.path.exists(UNHEALTHY_FLAG):
        return {"status": "unhealthy"}, 503
    return {"status": "ok"}


@app.get("/burn")
def burn():
    """Busy-loop for ?ms=N milliseconds (max 5000) to generate CPU load."""
    ms = min(int(request.args.get("ms", "200")), 5000)
    deadline = time.monotonic() + ms / 1000
    while time.monotonic() < deadline:
        pass
    return {"burned_ms": ms, "pid": os.getpid()}
