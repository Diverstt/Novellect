from __future__ import annotations

import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_STARTUP_TIMEOUT_SEC = 60.0
HTTP_TIMEOUT_SEC = 3.0
RETRY_INTERVAL_SEC = 1.0


def qdrant_base_url() -> str:
    return os.getenv('NOVELLECT_QDRANT_URL', '').strip().rstrip('/')


def startup_timeout_seconds() -> float:
    raw_value = os.getenv('NOVELLECT_QDRANT_STARTUP_TIMEOUT_SEC', str(DEFAULT_STARTUP_TIMEOUT_SEC))
    try:
        return max(1.0, float(raw_value or DEFAULT_STARTUP_TIMEOUT_SEC))
    except ValueError:
        return DEFAULT_STARTUP_TIMEOUT_SEC


def wait_for_endpoint(target: str, *, timeout_sec: float) -> bool:
    deadline = time.monotonic() + timeout_sec

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(target, timeout=HTTP_TIMEOUT_SEC) as response:
                if 200 <= getattr(response, 'status', 0) < 500:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(RETRY_INTERVAL_SEC)

    return False


def main() -> int:
    base_url = qdrant_base_url()
    if not base_url:
        return 0

    target = f'{base_url}/collections'
    if wait_for_endpoint(target, timeout_sec=startup_timeout_seconds()):
        return 0

    print(f'Qdrant is not reachable: {target}', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
