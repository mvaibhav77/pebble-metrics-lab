# Expose service status (active, backoff, error, inactive) in `/v1/metrics`

## Summary

`/v1/metrics` reports a service as either active (`1`) or not active (`0`). Pebble itself tracks four statuses (`active`, `backoff`, `error`, `inactive`), but the last three all become `0`. As a result, Prometheus cannot tell a service that failed and that Pebble gave up on from a service that was stopped on purpose or finished successfully.

I'd like to propose exposing the status Pebble already computes, as one gauge per status, alongside the existing metrics. I'm happy to implement it if the design below is acceptable.

## Reproduction

Pebble v1.33.2 (current `master`), run as a normal user with no identities configured.

`$PEBBLE/layers/001-repro.yaml`:

```yaml
services:
  failed:
    override: replace
    command: /bin/sh -c "sleep 2; exit 1"
    startup: enabled
    on-failure: ignore
  stopped:
    override: replace
    command: sleep 1000
    startup: enabled
```

```console
$ pebble run &
$ sleep 4
$ pebble stop stopped
$ pebble services
Service  Startup  Current   Since               Notes
failed   enabled  error     today at 18:47 IST  -
stopped  enabled  inactive  today at 18:47 IST  -
$ curl --unix-socket "$PEBBLE/.pebble.socket" http://localhost/v1/metrics
# HELP pebble_service_active Whether the service is currently active (1) or not (0)
# TYPE pebble_service_active gauge
pebble_service_active{service="failed"} 0
pebble_service_active{service="stopped"} 0

# HELP pebble_service_start_total Number of times the service has started
# TYPE pebble_service_start_total counter
pebble_service_start_total{service="failed"} 1
pebble_service_start_total{service="stopped"} 1
```

`pebble services` distinguishes the two, but the metrics are identical, and there is no query that can separate them.

## Impact

I built a small test setup (Pebble from `master` running a web service and a worker in a container, scraped by Prometheus, with cAdvisor and Grafana) and sampled `/v1/services` and `/v1/metrics` once per second for five scripted scenarios: healthy, crash loop, crashed with `on-failure: ignore`, stopped by an operator, and exited successfully with `on-success: ignore`.

- **Crash loops are detectable today** by combining `pebble_service_active == 0` with `increase(pebble_service_start_total[...]) > 0`. The existing metrics matched Pebble's status 100% of the time for the healthy and crash-loop scenarios.
- **Failed, stopped and successfully finished services were indistinguishable.** All 345 samples from those three scenarios had the same metric values (`active` 0, start counter flat), while Pebble reported `error` or `inactive`.
- **A single crash, stop or exit looks like a crash loop for the length of the alert window**, because the counter rose recently. With the same data:

  | Window | Crash-loop samples missed (of 98) | One-off failures and stops reported as a crash loop (of 345) |
  |---|---|---|
  | 10s | 18 | 12 |
  | 30s | 0 | 72 |
  | 60s | 0 | 162 |

  No window gives both: short windows miss crash loops, long ones report false ones.

Where this matters:

- Services with `on-failure: ignore`, such as one-off jobs or migrations, where "did it fail or finish?" is the question to alert on.
- Charms and rocks scraped by the Canonical Observability Stack, where an alert should fire for `error` but not for a service an operator stopped.
- Setups like the one in #642, where an external system should notice that a service has stayed down. If a retry limit along the lines discussed there (`backoff-attempts`) is ever added, "Pebble gave up on this service" would need to be visible in metrics too.

## Proposal

Add a gauge with one series per status for each service. The current status is `1` and the others are `0`:

```text
# HELP pebble_service_status Current status of the service (1 for the current status, 0 otherwise)
# TYPE pebble_service_status gauge
pebble_service_status{service="failed",status="active"} 0
pebble_service_status{service="failed",status="backoff"} 0
pebble_service_status{service="failed",status="error"} 1
pebble_service_status{service="failed",status="inactive"} 0
pebble_service_status{service="stopped",status="active"} 0
pebble_service_status{service="stopped",status="backoff"} 0
pebble_service_status{service="stopped",status="error"} 0
pebble_service_status{service="stopped",status="inactive"} 1
```

Queries then become direct:

```promql
pebble_service_status{status="error"} == 1     # failed, Pebble is not restarting it
pebble_service_status{status="backoff"} == 1   # crash-looping, no time window needed
sum by (status) (pebble_service_status)        # how many services are in each status
```

Notes on the design:

- **Conventions:** this is the usual Prometheus pattern for enumerated states, as used by `systemd_unit_state{state=...}` in systemd_exporter and `kube_pod_status_phase{phase=...}` in kube-state-metrics. All four series are always emitted, so series don't appear and disappear when the status changes, and no numeric encoding is needed.
- **Status values** are the existing `ServiceStatus` strings used by `pebble services` and the services API.
- **Not a breaking change:** `pebble_service_active` and `pebble_service_start_total` are unchanged.
- **Cost:** four series per service. No new state or collection: `serviceData.writeMetric` already calls `stateToStatus`. The change would be a few lines there, plus tests, the example in `docs/how-to/run-services-reliably.md` and `docs/specs/openapi.yaml`.
- **Export format:** it goes through the existing `metrics.Writer` interface, so it would be available to any other export path added later (for example the OpenTelemetry work in #919 and #1103).

Open questions:

- Is `pebble_service_status` with a `status` label the preferred naming?
- Should the equivalent be considered for checks? I've kept this proposal to services.

## Out of scope

- Service start time or uptime (discussed in #104). The services API already has `current-since`. It could be a follow-up if wanted.
- Per-service CPU and memory. That would need process accounting Pebble doesn't do today, and container-level tools already cover it.

## Related

- #519: the metrics endpoint this builds on
- #104: introspecting failing and restarting services
- #642: `max-start-retries` / `backoff-attempts`
- #1124: recent metric naming changes
