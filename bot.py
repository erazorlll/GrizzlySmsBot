from __future__ import annotations

import logging
import os
import signal
import threading
import time
from dataclasses import dataclass

import requests
from requests.adapters import HTTPAdapter


LOG = logging.getLogger("grizzlysms")
API_URL = "https://api.grizzlysms.com/stubs/handler_api.php"


def env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def env_int(name: str, minimum: int = 1) -> int:
    value = int(env_required(name))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def env_int_optional(name: str, default: int, minimum: int = 1) -> int:
    value = int(os.getenv(name, default))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def env_float(name: str, minimum: float = 0.1) -> float:
    value = float(env_required(name))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def env_float_optional(name: str, default: float, minimum: float = 0.1) -> float:
    value = float(os.getenv(name, default))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


@dataclass(frozen=True)
class Config:
    api_key: str
    service: str
    country: str
    max_price: str
    provider_ids: str | None
    workers: int
    rate: float
    timeout: float
    status_every: int
    ntfy_url: str
    price_check_interval: float
    api_url: str = API_URL

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            api_key=env_required("GRIZZLY_API_KEY"),
            service=env_required("SERVICE"),
            country=env_required("COUNTRY"),
            max_price=env_required("MAX_PRICE"),
            provider_ids=os.getenv("PROVIDER_IDS", "").strip() or None,
            workers=env_int("THREADS"),
            rate=env_float("MAX_REQUESTS_PER_SECOND"),
            timeout=env_float("REQUEST_TIMEOUT_SECONDS", 1),
            status_every=env_int_optional("STATUS_EVERY_REQUESTS", 100),
            ntfy_url=env_required("NTFY_URL"),
            price_check_interval=env_float_optional(
                "PRICE_CHECK_INTERVAL_SECONDS", 300.0, minimum=10.0
            ),
            api_url=os.getenv("GRIZZLY_API_URL", API_URL),
        )

    @property
    def params(self) -> dict[str, str]:
        params = {
            "api_key": self.api_key,
            "action": "getNumber",
            "service": self.service,
            "country": self.country,
            "maxPrice": self.max_price,
        }
        if self.provider_ids:
            params["providerIds"] = self.provider_ids
        return params


class RateLimiter:
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


def parse_number(body: str) -> tuple[str, str] | None:
    parts = body.split(":", 2)
    if len(parts) == 3 and parts[0] == "ACCESS_NUMBER" and all(parts[1:]):
        return parts[1], parts[2]
    return None


#: Plain-text error bodies documented for getPricesV3
#: (https://grizzlysms.com/docs/activation#activation-api-getPrices-v3).
PRICE_API_ERRORS = frozenset({"BAD_KEY", "BAD_ACTION", "BAD_SERVICE"})


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
    session.headers["User-Agent"] = "grizzlysms-stock-watcher/1.0"
    return session


class Bot:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.stop = threading.Event()
        self.limiter = RateLimiter(config.rate)
        self.ntfy = new_session()
        self.ntfy_lock = threading.Lock()
        self.seen_lock = threading.Lock()
        self.seen_activations: set[str] = set()
        self.status_lock = threading.Lock()
        self.total_requests = 0
        self.no_numbers = 0
        self.latest_stock: Stock | None = None

    def send_notification(self, title: str, message: str, urgent: bool = False) -> bool:
        try:
            with self.ntfy_lock:
                response = self.ntfy.post(
                    self.config.ntfy_url,
                    data=message.encode(),
                    headers={
                        "Title": title,
                        "Priority": "urgent" if urgent else "default",
                        "Tags": "telephone_receiver" if urgent else "white_check_mark",
                    },
                    timeout=self.config.timeout,
                )
            return response.ok
        except requests.RequestException as error:
            LOG.warning("ntfy error: %s", type(error).__name__)
            return False

    def mark_seen(self, activation_id: str) -> bool:
        with self.seen_lock:
            if activation_id in self.seen_activations:
                return False
            self.seen_activations.add(activation_id)
            return True

    def record_request(self, no_number: bool = False) -> None:
        with self.status_lock:
            self.total_requests += 1
            if no_number:
                self.no_numbers += 1
            if self.total_requests % self.config.status_every != 0:
                return
            with self.seen_lock:
                acquired = len(self.seen_activations)
            LOG.info(
                "still polling requests=%s no_numbers=%s acquired=%s",
                self.total_requests,
                self.no_numbers,
                acquired,
            )

    def notify_purchase(self, activation_id: str, phone_number: str) -> None:
        message = f"Number: {phone_number}\nActivation: {activation_id}"
        if self.send_notification("GRIZZLY NUMBER ACQUIRED", message, urgent=True):
            LOG.info("notification sent activation=%s", activation_id)
        else:
            LOG.warning("notification failed activation=%s", activation_id)

    def fetch_stock(self, session: requests.Session) -> Stock | None:
        """Live count/price for the configured service+country, via getPricesV3.

        Unlike getNumber this reserves nothing and costs no balance, so it is
        safe to call on its own schedule, independent of the polling rate
        limiter (https://grizzlysms.com/docs/activation#activation-api-getPrices-v3).
        """
        params = {
            "api_key": self.config.api_key,
            "action": "getPricesV3",
            "service": self.config.service,
            "country": self.config.country,
        }
        try:
            response = session.get(self.config.api_url, params=params, timeout=self.config.timeout)
        except requests.RequestException as error:
            LOG.warning("Grizzly getPricesV3 network error: %s", type(error).__name__)
            return None

        if response.status_code != 200:
            LOG.warning("Grizzly getPricesV3 HTTP %s", response.status_code)
            return None

        body = response.text.strip()
        if body in PRICE_API_ERRORS:
            LOG.warning("Grizzly getPricesV3 error: %s", body)
            return None

        try:
            data = response.json()
        except ValueError:
            LOG.warning("Grizzly getPricesV3: non-JSON response: %s", body[:200])
            return None

        stock = parse_stock(data, self.config.country, self.config.service)
        if stock is None:
            LOG.warning(
                "Grizzly getPricesV3: no entry for service=%s country=%s in response: %s",
                self.config.service,
                self.config.country,
                body[:200],
            )
        return stock

    def fetch_service_name(self, session: requests.Session) -> str | None:
        """Human-readable service name via getServicesList. Best-effort only:
        display sugar, never a reason to fail the stock check itself.
        """
        try:
            response = session.get(
                self.config.api_url,
                params={"api_key": self.config.api_key, "action": "getServicesList"},
                timeout=self.config.timeout,
            )
            response.raise_for_status()
            for entry in response.json().get("services", []):
                if entry.get("code") == self.config.service:
                    name = entry.get("name")
                    return str(name) if name else None
        except (requests.RequestException, ValueError, AttributeError) as error:
            LOG.debug("could not resolve service name: %s", type(error).__name__)
        return None

    def fetch_country_name(self, session: requests.Session) -> str | None:
        """Human-readable country name via getCountries. Best-effort only, and
        the response shape is less certain than getPricesV3's: the docs example
        for this endpoint renders oddly (double-nested braces), so this accepts
        either a `{id: {...}}` mapping or a plain list of entries.
        """
        try:
            response = session.get(
                self.config.api_url,
                params={"api_key": self.config.api_key, "action": "getCountries"},
                timeout=self.config.timeout,
            )
            response.raise_for_status()
            data = response.json()
            entries = data.values() if isinstance(data, dict) else data
            for entry in entries:
                if str(entry.get("id")) == self.config.country:
                    name = entry.get("eng")
                    return str(name) if name else None
        except (requests.RequestException, ValueError, AttributeError) as error:
            LOG.debug("could not resolve country name: %s", type(error).__name__)
        return None

    def stock_watcher(self) -> None:
        """Re-checks stock/price on its own schedule for the life of the bot."""
        session = new_session()
        try:
            while not self.stop.is_set():
                stock = self.fetch_stock(session)
                self.latest_stock = stock
                LOG.info("%s", format_stock(self.config.service, self.config.country, stock))
                if self.stop.wait(self.config.price_check_interval):
                    return
        finally:
            session.close()

    def poll_worker(self, worker_id: int) -> None:
        session = new_session()
        try:
            while self.limiter.wait(self.stop):
                self.poll_once(session, worker_id)
        finally:
            session.close()

    def poll_once(self, session: requests.Session, worker_id: int) -> None:
        try:
            response = session.get(
                self.config.api_url,
                params=self.config.params,
                timeout=self.config.timeout,
            )
        except requests.RequestException as error:
            LOG.warning("Grizzly network error: %s", type(error).__name__)
            self.stop.wait(1)
            return

        if response.status_code != 200:
            self.record_request()
            delay = 2.0
            self.limiter.pause(delay)
            LOG.warning("Grizzly HTTP %s: pause %.1fs", response.status_code, delay)
            return

        body = response.text.strip()
        if body == "NO_NUMBERS":
            self.record_request(no_number=True)
            return

        self.record_request()

        number = parse_number(body)
        if not number:
            self.limiter.pause(2)
            LOG.warning("Grizzly response: %s", body[:100])
            return

        activation_id, phone_number = number
        if not self.mark_seen(activation_id):
            return

        LOG.info(
            "number acquired worker=%s activation=%s number=%s",
            worker_id,
            activation_id,
            phone_number,
        )
        self.notify_purchase(activation_id, phone_number)

    def run(self) -> None:
        cfg = self.config
        LOG.info(
            "startup service=%s country=%s maxPrice=%s providerIds=%s "
            "workers=%s limit=%.1f/s",
            cfg.service,
            cfg.country,
            cfg.max_price,
            cfg.provider_ids or "none",
            cfg.workers,
            cfg.rate,
        )

        startup_session = new_session()
        try:
            service_name = self.fetch_service_name(startup_session)
            country_name = self.fetch_country_name(startup_session)
            self.latest_stock = self.fetch_stock(startup_session)
        finally:
            startup_session.close()

        stock_line = format_stock(cfg.service, cfg.country, self.latest_stock)
        LOG.info("%s", stock_line)
        if service_name or country_name:
            LOG.info(
                "resolved names: service=%s country=%s",
                service_name or cfg.service,
                country_name or cfg.country,
            )

        result = self.send_notification(
            "Grizzly SMS startup test",
            f"Bot active: {cfg.workers} workers, limit {cfg.rate:g} req/s.\n"
            f"{service_name or cfg.service} / {country_name or cfg.country}\n"
            f"{stock_line}",
        )
        LOG.info("ntfy test: %s", "OK" if result else "FAILED")

        threads = [
            threading.Thread(target=self.poll_worker, args=(worker,), name=f"poll-{worker}")
            for worker in range(1, cfg.workers + 1)
        ]
        threads.append(threading.Thread(target=self.stock_watcher, name="stock-watcher"))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def close(self) -> None:
        self.stop.set()
        self.ntfy.close()


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s | %(levelname)s | %(threadName)s | %(message)s",
    )
    try:
        bot = Bot(Config.from_env())
    except (ValueError, OSError) as error:
        LOG.error("startup failed: %s", error)
        return 2

    def shutdown(_signal: int, _frame: object) -> None:
        LOG.info("shutdown requested")
        bot.stop.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        bot.run()
    finally:
        bot.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
