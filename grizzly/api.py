"""Grizzly SMS API client, rate limiter, and response parsing."""

from __future__ import annotations

import threading
import time

import requests
from requests.adapters import HTTPAdapter

from .config import API_URL

# Responses that mean there is no point in continuing to poll (fixed for the run).
FATAL_RESPONSES = frozenset({"BAD_KEY", "NO_BALANCE"})
# Same, but matched by prefix because they carry a suffix, e.g.
# "WRONG_MAX_PRICE:0.500000" (bid below the platform minimum).
FATAL_PREFIXES = ("WRONG_MAX_PRICE",)


def parse_number(body: str) -> tuple[str, str] | None:
    """Parse an "ACCESS_NUMBER:<id>:<phone>" response into (id, phone)."""
    parts = body.split(":", 2)
    if len(parts) == 3 and parts[0] == "ACCESS_NUMBER" and all(parts[1:]):
        return parts[1], parts[2]
    return None


def is_fatal_response(body: str) -> bool:
    return body in FATAL_RESPONSES or body.startswith(FATAL_PREFIXES)


def new_session() -> requests.Session:
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=0, pool_maxsize=1))
    session.headers["User-Agent"] = "grizzlysms-bot/1.0"
    return session


class RateLimiter:
    """Global request spacer shared by all workers.

    Grants at most one start every ``1/rate`` seconds and never bursts above the
    target rate (no catch-up), keeping pressure on the API predictable.
    """

    def __init__(self, rate: float) -> None:
        self.interval = 1 / rate
        self.next_request = time.monotonic()
        self.lock = threading.Lock()

    def wait(self, stop: threading.Event) -> bool:
        while not stop.is_set():
            with self.lock:
                delay = self.next_request - time.monotonic()
                if delay <= 0:
                    self.next_request = time.monotonic() + self.interval
                    return True
            stop.wait(min(delay, 0.25))
        return False

    def pause(self, seconds: float) -> None:
        with self.lock:
            self.next_request = max(self.next_request, time.monotonic() + seconds)


class GrizzlyClient:
    """Thin wrapper over the handler_api endpoint. One instance per worker."""

    def __init__(self, api_key: str, timeout: float, api_url: str = API_URL) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.api_url = api_url
        self.session = new_session()

    def _get(self, params: dict) -> requests.Response:
        return self.session.get(self.api_url, params=params, timeout=self.timeout)

    def get_number(self, params: dict) -> requests.Response:
        """Return the raw response (caller inspects status code / headers / body)."""
        return self._get(params)

    def get_status(self, activation_id: str) -> str:
        response = self._get(
            {"api_key": self.api_key, "action": "getStatus", "id": activation_id}
        )
        response.raise_for_status()
        return response.text.strip()

    def set_status(self, activation_id: str, status: int) -> str:
        response = self._get(
            {
                "api_key": self.api_key,
                "action": "setStatus",
                "status": str(status),
                "id": activation_id,
            }
        )
        response.raise_for_status()
        return response.text.strip()

    def cancel(self, activation_id: str) -> str:
        """Cancel an activation (status 8), refunding a just-reserved number."""
        return self.set_status(activation_id, 8)

    def close(self) -> None:
        self.session.close()
