"""Grizzly SMS API client, rate limiter, and response parsing."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import requests
from requests.adapters import HTTPAdapter

from .config import API_URL

# Responses that mean there is no point in continuing to poll (fixed for the run).
FATAL_RESPONSES = frozenset({"BAD_KEY", "NO_BALANCE"})
# Same, but matched by prefix because they carry a suffix, e.g.
# "WRONG_MAX_PRICE:0.500000" (bid below the platform minimum).
FATAL_PREFIXES = ("WRONG_MAX_PRICE",)

#: Plain-text error bodies documented for getPricesV3
#: (https://grizzlysms.com/docs/activation#activation-api-getPrices-v3).
PRICE_API_ERRORS = frozenset({"BAD_KEY", "BAD_ACTION", "BAD_SERVICE"})


def parse_number(body: str) -> tuple[str, str] | None:
    """Parse an "ACCESS_NUMBER:<id>:<phone>" response into (id, phone)."""
    parts = body.split(":", 2)
    if len(parts) == 3 and parts[0] == "ACCESS_NUMBER" and all(parts[1:]):
        return parts[1], parts[2]
    return None


def is_fatal_response(body: str) -> bool:
    return body in FATAL_RESPONSES or body.startswith(FATAL_PREFIXES)


@dataclass(frozen=True)
class ProviderStock:
    """One provider's stock/price inside a getPricesV3 answer."""

    provider_id: str
    count: int
    prices: tuple[float, ...]


@dataclass(frozen=True)
class Stock:
    """Live stock and price for one service+country, from getPricesV3.

    Response shape (verified against the current docs, not the older
    getPrices/getPricesV2 actions, which return a flatter `{"cost": ...}`
    without a provider breakdown)::

        {"<country>": {"<service>": {
            "price": price, "count": count,
            "providers": {"<provider_id>": {"count": .., "price": [..]}}
        }}}
    """

    count: int
    price: float
    providers: tuple[ProviderStock, ...]


def parse_stock(data: object, country: str, service: str) -> Stock | None:
    """Pull the `{count, price, providers}` entry for one country+service out
    of a getPricesV3 response. `None` if the shape does not match -- this can
    legitimately happen if the pair does not exist for this account.
    """
    try:
        entry = data[country][service]  # type: ignore[index]
        providers = tuple(
            ProviderStock(
                provider_id=str(provider_data.get("provider_id", provider_id)),
                count=int(provider_data["count"]),
                prices=tuple(float(price) for price in provider_data.get("price") or ()),
            )
            for provider_id, provider_data in (entry.get("providers") or {}).items()
        )
        return Stock(
            count=int(entry["count"]),
            price=float(entry["price"]),
            providers=providers,
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def format_stock(service: str, country: str, stock: Stock | None) -> str:
    """One-line summary for logs and the startup notification."""
    if stock is None:
        return f"service={service} country={country}: no stock/price data"

    line = f"service={service} country={country} available={stock.count} price={stock.price:g}"
    if not stock.providers:
        return line

    per_provider = ", ".join(
        f"{p.provider_id}:{p.count}@{'/'.join(f'{price:g}' for price in p.prices) or '?'}"
        for p in sorted(stock.providers, key=lambda p: p.provider_id)
    )
    return f"{line} providers=[{per_provider}]"


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

    def get_prices_v3(self, service: str, country: str) -> requests.Response:
        """Live count/price for one service+country. Reserves nothing, costs no

        balance (https://grizzlysms.com/docs/activation#activation-api-getPrices-v3)
        -- safe to call on its own schedule, independent of `getNumber` polling.
        """
        return self._get(
            {"api_key": self.api_key, "action": "getPricesV3", "service": service, "country": country}
        )

    def get_services_list(self) -> requests.Response:
        return self._get({"api_key": self.api_key, "action": "getServicesList"})

    def get_countries(self) -> requests.Response:
        return self._get({"api_key": self.api_key, "action": "getCountries"})

    def close(self) -> None:
        self.session.close()
