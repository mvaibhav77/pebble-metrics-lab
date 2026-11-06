# Pebble metrics lab

A small Docker Compose setup for poking at [Pebble](https://github.com/canonical/pebble)'s
existing OpenMetrics endpoint (`GET /v1/metrics`) and deciding, with evidence,
whether new metrics are worth adding. It tries to answer three questions:

1. Can restart loops be detected with the existing `pebble_service_start_total`?
2. How hard is it to get per-service uptime/start time without a new
   `pebble_service_start_time_seconds` metric?
3. What does cAdvisor (per container) show for CPU/memory compared with the
   real per-process usage of two services in one container? Is per-service
   resource telemetry a real gap?
4. Can the metrics tell a failed service from a stopped one?

The resulting proposal is [canonical/pebble#1150](https://github.com/canonical/pebble/issues/1150)
(draft in [`proposal/github-issue.md`](proposal/github-issue.md)).

## What's in the stack

| Container         | What it is                                                        | URL                     |
|-------------------|-------------------------------------------------------------------|-------------------------|
| `metrics-lab-app` | Pebble (built from your local fork) as PID 1, running `web` + `worker` | http://localhost:4000 (Pebble), http://localhost:8080 (web) |
| `prometheus`      | Scrapes Pebble `/v1/metrics` (basic auth) and cAdvisor every 5s   | http://localhost:9090   |
| `cadvisor`        | Per-container CPU/memory                                          | http://localhost:8081   |
| `grafana`         | Provisioned Prometheus datasource + "Pebble metrics lab" dashboard | http://localhost:3000   |

The Pebble services are defined in [`app/layers/001-lab.yaml`](app/layers/001-lab.yaml):

- `web`: Flask under gunicorn (2 workers) on :8080, with `/health`, and a
  `/burn?ms=N` endpoint that busy-loops to generate CPU. The HTTP check
  `web-up` hits `/health` every 5s (threshold 3) and restarts `web` on failure.
  `touch /tmp/web-unhealthy` inside the container makes `/health` return 503.
- `worker`: a Python loop that, per the env vars below, crashes, leaks memory and
  burns CPU. It uses `on-failure: restart` with `backoff-delay: 1s`,
  `backoff-factor: 2`, `backoff-limit: 20s`.

| Env var                | Default | Meaning                                       |
|------------------------|---------|-----------------------------------------------|
| `WORKER_CRASH_AFTER_S` | `30`    | `exit(1)` after this many seconds (0 = never) |
| `WORKER_LEAK_MB_PER_S` | `1`     | MB allocated and kept per second (0 = off)    |
| `WORKER_CPU_PERCENT`   | `20`    | Busy-loop share of each second (0-100)        |

Set them in a `.env` file next to `docker-compose.yml` or inline, e.g.
`WORKER_CRASH_AFTER_S=2 docker compose up -d app` (this recreates the app
container, which resets Pebble's counters).

### Credentials (dev only)

| What                         | Username      | Password         |
|------------------------------|---------------|------------------|
| Pebble `/v1/metrics` (basic) | `metrics-lab` | `pebble-lab-dev` |
| Grafana                      | `admin`       | `admin` (anonymous viewing is also on) |

The Pebble identity lives in [`app/identities.yaml`](app/identities.yaml) as a
sha512-crypt hash (`openssl passwd -6`) and is seeded when the container starts
via `pebble run --identities=...` (same effect as
`pebble update-identities --replace`). The plaintext is repeated in
`prometheus/prometheus.yml` and `scripts/metrics.sh`. To change it, regenerate the hash
with `openssl passwd -6 '<new password>'` and update all three places.

### How the Pebble binary is built

`app/Dockerfile` is multi-stage. Stage 1 (`golang:1.27.3-trixie`) runs
`go build ./cmd/pebble` on a second build context named `pebble`, which
`docker-compose.yml` points at `${PEBBLE_SRC:-../pebble}` via
`build.additional_contexts`. So by default it builds whatever is checked out in
the sibling `../pebble` directory, uncommitted changes included. Stage 2
(`python:3.15.2-slim-trixie`) copies in the binary and the Python services.

To test a patched Pebble, edit the fork and run `docker compose up -d --build app`.
To build from a different tree: `PEBBLE_SRC=/path/to/pebble docker compose up -d --build app`.

## Prerequisites

- Docker Engine with Compose v2.17+ (for `additional_contexts`) and BuildKit.
- A Linux host with native Docker. cAdvisor's bind mounts (`/var/lib/docker`,
  `/sys`, ...) assume this. Under Docker Desktop, cAdvisor may not
  see the containers.
- `curl` on the host. Go is only needed if you want to build Pebble outside Docker.
- Free ports: 3000, 4000, 8080, 8081, 9090.

## Run it

```sh
docker compose up --build -d
scripts/metrics.sh          # Pebble's OpenMetrics output
docker compose logs -f app  # Pebble + service logs
docker compose down         # stop and remove everything (no volumes are used)
```

Then check http://localhost:9090/targets (the `pebble`, `cadvisor` and
`prometheus` jobs should be UP) and open the "Pebble metrics lab" dashboard at
http://localhost:3000.

Other useful commands (run as root over the unix socket inside the container):

```sh
docker exec metrics-lab-app pebble services
docker exec metrics-lab-app pebble checks
docker exec metrics-lab-app pebble restart web
```

## Helper scripts

- `scripts/metrics.sh [curl args]`: `curl -u metrics-lab:... localhost:4000/v1/metrics`.
- `scripts/uptime.sh`: reads `GET /v1/services` over Pebble's unix socket
  (via `docker exec`) and prints each service's `current`, `current-since` and
  seconds in that state.
- `scripts/proc-usage.sh [seconds]`: `ps --forest` inside the container, then
  samples `/proc` twice and prints per-process and per-service CPU (cores) and
  RSS/PSS. Next to that it prints cAdvisor's whole-container CPU rate and memory for the
  same window, queried from Prometheus. The sampling logic is in
  `scripts/procstat.py`.
- `scripts/simulate.sh [seconds] [window]`: the failed-vs-stopped simulation
  (experiment 4).

## Experiments

Run these in the Prometheus UI (http://localhost:9090/query) or Grafana Explore.

### 1. Restart-loop detection with `pebble_service_start_total`

Default config: the worker crashes every 30s and restarts after 1s, which is about 10 starts per 5m.

```promql
pebble_service_start_total
increase(pebble_service_start_total[5m])
rate(pebble_service_start_total[5m])
# Candidate alert: more than 3 starts in 5 minutes
increase(pebble_service_start_total[5m]) > 3
# Does the active gauge even catch the brief down periods at a 5s scrape interval?
changes(pebble_service_active[5m])
min_over_time(pebble_service_active[5m])
```

Variations to try:

```sh
# Tight crash loop: the backoff grows 1s,2s,4s,8s,16s,20s (capped) and never resets
WORKER_CRASH_AFTER_S=2 docker compose up -d app

# No crashes (baseline); start_total should stay at 1
WORKER_CRASH_AFTER_S=0 docker compose up -d app

# Health-check-driven restart of web (check goes down after ~15s)
docker exec metrics-lab-app touch /tmp/web-unhealthy
docker exec metrics-lab-app rm /tmp/web-unhealthy      # recover

# A deliberate restart increments the same counter. Can you tell it apart from a crash?
docker exec metrics-lab-app pebble restart worker
```

Things to note: the counter includes the initial start. It can't tell crash,
check-triggered and manual restarts apart. Exit codes aren't exposed. When
Pebble itself restarts the counter resets, which `rate`/`increase` handle.
Short backoff windows (1s) may fall between scrapes, so `pebble_service_active`
can look constantly 1.

### 2. Per-service uptime / start time without a new metric

What exists today:

```sh
scripts/uptime.sh                                   # REST: current-since per service
docker exec metrics-lab-app ps -eo pid,etimes,args  # per-process elapsed time
```

`/v1/services` needs `read` access over the unix socket or HTTPS. Pebble's
plain-HTTP listener only lets `metrics`-level identities through, and only to
`/v1/metrics` (see `internals/daemon/access.go`). So Prometheus can't scrape it
the way it scrapes metrics, even with a `read` identity. Also, `current-since` is the time
of the last status change, not the last process start. It is an uptime only while
`current` is `active`.

The best pure-PromQL approximation, derived from the start counter:

```promql
# seconds since the last observed start (resolution ~ scrape interval;
# empty if the last start was before the 1h subquery window)
time() - max_over_time((timestamp(changes(pebble_service_start_total[10s]) > 0))[1h:5s])

# container-level only (cAdvisor), not per service
time() - container_start_time_seconds{name="metrics-lab-app"}
```

With a `pebble_service_start_time_seconds{service}` gauge this would be
`time() - pebble_service_start_time_seconds`: exact, cheap, and with no lookback window.

### 3. Per-container (cAdvisor) vs per-process resource usage

```promql
sum(rate(container_cpu_usage_seconds_total{name="metrics-lab-app"}[1m]))
container_memory_working_set_bytes{name="metrics-lab-app"}
container_memory_rss{name="metrics-lab-app"}
```

```sh
# Crashes mid-window drop the dead process's CPU from the /proc sample, so
# disable them for a clean comparison
WORKER_CRASH_AFTER_S=0 docker compose up -d app
scripts/proc-usage.sh 30

# Put load on web while sampling (in another terminal)
for i in $(seq 300); do curl -s 'localhost:8080/burn?ms=100' >/dev/null; done

# Make the worker the obvious hog, then compare again
WORKER_CPU_PERCENT=60 WORKER_LEAK_MB_PER_S=5 WORKER_CRASH_AFTER_S=60 docker compose up -d app
```

Questions to answer: does cAdvisor's container total match the sum of the
per-service numbers? Can you tell from Prometheus alone which service is
leaking or burning CPU (the sawtooth in container memory comes from the worker
leak and crash cycle)? How much do shared gunicorn pages (RSS vs PSS) and page
cache (working set vs RSS) blur the picture?

### 4. Failed vs stopped (simulation)

```sh
scripts/simulate.sh 120 30   # duration and crash-loop window, in seconds
```

This adds a layer with five services that run side by side inside the app
container: `sim-healthy`, `sim-crashloop` (crashes 2s after each start),
`sim-failed` (crashes once, `on-failure: ignore`), `sim-stopped` (stopped by
`pebble stop` after 10s) and `sim-clean-exit` (exits 0, `on-success: ignore`).
[`scripts/simulate.py`](scripts/simulate.py) samples Pebble's status from
`/v1/services` and the scraped values from `/v1/metrics` once per second.
[`scripts/analyze_sim.py`](scripts/analyze_sim.py) then classifies each sample
using only today's metrics:

- running: `pebble_service_active == 1`
- crash-looping: `active == 0` and the start counter rose within the window
- down-unknown: `active == 0` and the counter is flat

It compares that classification with Pebble's real status. Raw CSV and the
report go to `results/`. To re-analyse a saved run with another window, use
`python3 scripts/analyze_sim.py results/sim-<stamp>.csv 60`.

The `sim-*` services stay in Pebble's service list (and metrics) until the
app container is recreated with `docker compose up -d --force-recreate app`.

## Findings

Measured with Pebble v1.33.2 built from `master`.

### 1. Restart-loop detection

- Worker crashing every 30s: `increase(pebble_service_start_total[5m])` ≈ 10,
  `avg_over_time(pebble_service_active[5m])` ≈ 0.97.
- Worker crashing after 2s (backoff capped at 20s): increase ≈ 13 per 5m,
  active average falling toward ≈ 0.1. `pebble services` showed `backoff`
  nearly the whole time.
- Crash loops can be detected by combining `pebble_service_active == 0` with a
  rising start counter, but the restart rate alone does not show severity, and
  every start (crash, health-check restart, manual `pebble start`) increments
  the same counter.

### 2. Per-service uptime / start time

- `pebble_service_start_total` gives no time of the last start.
- `current-since` is available from `/v1/services` (`scripts/uptime.sh`), but
  that endpoint is not served to Prometheus over plain HTTP.
- The PromQL approximation works to within the scrape interval, but it keeps
  counting while a service is down, so it is not an uptime.

### 3. Per-service resource telemetry

- Under load, per-process CPU summed to ≈ 0.70 cores (web 0.50, worker 0.20);
  cAdvisor reported ≈ 0.72 cores for the container, with no per-service split.
- Container memory rose from ≈ 72 MiB to ≈ 88 MiB while the five simulation
  services ran; cAdvisor cannot attribute it to a service.

### 4. Failed vs stopped

- A worker with `on-failure: ignore` that crashed showed `error` in
  `pebble services`; the same worker stopped with `pebble stop` showed
  `inactive`. Both times `/v1/metrics` reported
  `pebble_service_active{service="worker"} 0` and a flat start counter.
- Simulation ([`results/sim-20261106-182430.md`](results/sim-20261106-182430.md)),
  605 samples: healthy and crash-loop scenarios matched Pebble's status 100%
  of the time. All 345 samples from the failed, stopped and clean-exit
  scenarios had identical metric values.
- A single crash, stop or exit is classified as a crash loop for the length of
  the window:

  | Window | Crash-loop samples missed (of 98) | One-off failures and stops reported as a crash loop (of 345) |
  |---|---|---|
  | 10s | 18 | 12 |
  | 30s | 0 | 72 |
  | 60s | 0 | 162 |

### Conclusion: new metrics needed?

Crash loops, health checks and running/not-running are covered today. The gap
that is cheap to close is exposing Pebble's existing service status
(`active`, `backoff`, `error`, `inactive`) as a metric, which is what
[canonical/pebble#1150](https://github.com/canonical/pebble/issues/1150)
proposes. Start time is a smaller gap. Per-service CPU and memory is a real gap
but a poor fit for Pebble.
