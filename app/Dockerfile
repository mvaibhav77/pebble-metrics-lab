# syntax=docker/dockerfile:1

# Stage 1: build Pebble from the local fork. The "pebble" build context is
# supplied by docker-compose.yml (additional_contexts), defaulting to ../pebble.
FROM golang:1.27.3-trixie AS pebble-build
WORKDIR /src
COPY --from=pebble . .
RUN --mount=type=cache,target=/root/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 go build -buildvcs=false -trimpath -o /out/pebble ./cmd/pebble

# Stage 2: runtime with Python for the two lab services.
FROM python:3.15.2-slim-trixie
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl procps \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY --from=pebble-build /out/pebble /usr/local/bin/pebble
COPY web.py worker.py /app/
COPY layers/ /var/lib/pebble/default/layers/
COPY identities.yaml /etc/pebble/identities.yaml

ENV PEBBLE=/var/lib/pebble/default \
    PYTHONUNBUFFERED=1
EXPOSE 4000 8080

# --verbose also writes service output to Pebble's stdout (docker logs).
ENTRYPOINT ["pebble", "run", "--verbose", "--http=:4000", "--identities=/etc/pebble/identities.yaml"]
