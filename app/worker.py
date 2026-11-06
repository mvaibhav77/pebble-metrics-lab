"""Background worker managed by Pebble as the "worker" service.

Misbehaves on purpose, controlled by environment variables (set them in
docker-compose.yml or a .env file; Pebble passes its environment through):

  WORKER_CRASH_AFTER_S  exit(1) after this many seconds of uptime (0 = never)
  WORKER_LEAK_MB_PER_S  allocate and keep this many MB every second (0 = off)
  WORKER_CPU_PERCENT    busy-loop this share of each second (0-100)
"""

import os
import sys
import time

CRASH_AFTER_S = float(os.environ.get("WORKER_CRASH_AFTER_S", "30"))
LEAK_MB_PER_S = float(os.environ.get("WORKER_LEAK_MB_PER_S", "1"))
CPU_PERCENT = min(max(float(os.environ.get("WORKER_CPU_PERCENT", "20")), 0), 100)


def main():
    started = time.monotonic()
    leaked = []
    print(
        f"worker started pid={os.getpid()} crash_after={CRASH_AFTER_S}s "
        f"leak={LEAK_MB_PER_S}MB/s cpu={CPU_PERCENT}%"
    )
    while True:
        tick = time.monotonic()
        while time.monotonic() < tick + CPU_PERCENT / 100:
            pass

        if LEAK_MB_PER_S > 0:
            # Filled (not zeroed) bytes, so the pages are really resident.
            leaked.append(b"\xff" * int(LEAK_MB_PER_S * 1024 * 1024))

        uptime = time.monotonic() - started
        if CRASH_AFTER_S > 0 and uptime >= CRASH_AFTER_S:
            print(f"worker crashing after {uptime:.0f}s, leaked {len(leaked) * LEAK_MB_PER_S:.0f}MB")
            sys.exit(1)

        time.sleep(max(0.0, tick + 1 - time.monotonic()))


if __name__ == "__main__":
    main()
