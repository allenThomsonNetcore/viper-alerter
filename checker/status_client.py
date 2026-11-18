"""Status endpoint client with retry and per-host concurrency limits."""
from __future__ import annotations

import time
from collections import defaultdict
from threading import BoundedSemaphore, Lock
from typing import Any

import requests


class StatusClient:
    """Fetch status history with bounded host concurrency and retries."""
    def __init__(self, timeout: float = 15, retries: int = 3) -> None:
        """Initialize timeout, retry count and host semaphores."""
        self.timeout, self.retries = timeout, retries
        self._semaphores: dict[str, BoundedSemaphore] = defaultdict(lambda: BoundedSemaphore(3))
        self._lock = Lock()

    def fetch(self, host: str, site_key: str) -> list[dict[str, Any]]:
        """Fetch a site's JSON history, raising after bounded retries."""
        url = f"https://{host}/api/{site_key}/catalog/status"
        with self._lock:
            semaphore = self._semaphores[host]
        error: Exception | None = None
        for attempt in range(self.retries):
            try:
                with semaphore:
                    response = requests.get(url, timeout=self.timeout)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list) or any(not isinstance(x, dict) for x in payload):
                    raise ValueError("Status response must be a JSON array of objects")
                return payload
            except (requests.RequestException, ValueError) as exc:
                error = exc
                if attempt + 1 < self.retries:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"status endpoint failed after {self.retries} attempts: {error}")
