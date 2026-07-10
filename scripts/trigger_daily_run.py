from __future__ import annotations

import os
import sys
import time
from datetime import datetime

import httpx


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main() -> int:
    base_url = os.getenv("HOT_COLLECT_SERVER_URL", "http://127.0.0.1:8000").rstrip("/")
    url = f"{base_url}/run/daily"

    max_wait_seconds = int(os.getenv("HOT_COLLECT_TRIGGER_WAIT_SECONDS", "90"))
    request_timeout_seconds = float(os.getenv("HOT_COLLECT_TRIGGER_TIMEOUT_SECONDS", "600"))

    deadline = time.time() + max_wait_seconds
    last_error: str | None = None

    while time.time() < deadline:
        try:
            with httpx.Client(timeout=request_timeout_seconds) as client:
                resp = client.post(url, json={})
                resp.raise_for_status()
                data = resp.json()
            print(f"[{_now()}] triggered ok: thread_id={data.get('thread_id')} status={data.get('status')}")
            return 0
        except Exception as e:  # noqa: BLE001
            last_error = repr(e)
            time.sleep(2)

    print(f"[{_now()}] trigger failed after {max_wait_seconds}s; last_error={last_error}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

