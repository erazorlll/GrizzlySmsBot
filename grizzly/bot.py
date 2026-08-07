"""Orchestration: acquire a number (hard cap), then watch it for the SMS code."""

from __future__ import annotations

import logging
import threading
import time

import requests

from .api import (
    PRICE_API_ERRORS,
    GrizzlyClient,
    RateLimiter,
    Stock,
    format_stock,
    is_fatal_response,
    parse_number,
    parse_stock,
)
from .config import Config
from .notify import Notifier

LOG = logging.getLogger("grizzly.bot")

HTTP_BACKOFF_SECONDS = 2.0
RATE_LIMIT_BACKOFF_SECONDS = 5.0


def _retry_after(response: requests.Response, default: float) -> float:
    raw = response.headers.get("Retry-After")
    if raw:
        try:
            return max(default, float(raw))
        except ValueError:
            return default
    return default


class Acquirer:
    """Worker pool that keeps up to ``max_acquisitions`` numbers, cancelling extras.

    ``getNumber`` is the purchase itself, so a burst of concurrent workers can win
    more than one number at once. Anything beyond the cap is cancelled (status 8)
    and refunded, which makes ``max_acquisitions`` a hard guarantee rather than a
    best-effort limit.
    """

    def __init__(self, config: Config, notifier: Notifier) -> None:
        self.config = config
        self.notifier = notifier
        self.limiter = RateLimiter(config.rate)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._seen: set[str] = set()
        self._kept: list[tuple[str, str]] = []
        self._count = 0
        self._total_requests = 0
        self._no_numbers = 0
        self._fatal = False

    def _record_request(self, no_number: bool = False) -> None:
        with self._lock:
            self._total_requests += 1
            if no_number:
                self._no_numbers += 1
            if self._total_requests % self.config.status_every == 0:
                LOG.info(
                    "still polling requests=%s no_numbers=%s kept=%s",
                    self._total_requests, self._no_numbers, len(self._kept),
                )

    def _claim_fatal(self) -> bool:
        with self._lock:
            if self._fatal:
                return False
            self._fatal = True
            return True

    @property
    def fatal(self) -> bool:
        """True if acquisition stopped on a fatal API response."""
        return self._fatal

    def _decide(self, activation_id: str, phone: str) -> str:
        """Classify an acquired number as 'keep', 'cancel', or 'dup' (under lock)."""
        with self._lock:
            if activation_id in self._seen:
                return "dup"
            self._seen.add(activation_id)
            self._count += 1
            cap = self.config.max_acquisitions
            if cap == 0:  # unlimited: keep everything, never auto-stop
                self._kept.append((activation_id, phone))
                return "keep"
            if self._count <= cap:
                self._kept.append((activation_id, phone))
                if self._count == cap:
                    self._stop.set()
                return "keep"
            return "cancel"

    def _poll_once(self, client: GrizzlyClient, worker_id: int) -> None:
        try:
            response = client.get_number(self.config.params)
        except requests.RequestException as error:
            LOG.warning("network error: %s", type(error).__name__)
            self.limiter.pause(HTTP_BACKOFF_SECONDS)
            return

        if response.status_code == 429:
            backoff = _retry_after(response, RATE_LIMIT_BACKOFF_SECONDS)
            self.limiter.pause(backoff)
            LOG.warning("HTTP 429: pause %.1fs", backoff)
            self._record_request()
            return
        if response.status_code != 200:
            self.limiter.pause(HTTP_BACKOFF_SECONDS)
            LOG.warning("HTTP %s: pause %.1fs", response.status_code, HTTP_BACKOFF_SECONDS)
            self._record_request()
            return

        body = response.text.strip()
        if body == "NO_NUMBERS":
            self._record_request(no_number=True)
            return

        self._record_request()

        number = parse_number(body)
        if number is None:
            if is_fatal_response(body):
                if self._claim_fatal():
                    LOG.error("fatal response: %s — stopping acquisition", body)
                    self.notifier.send(
                        "Grizzly bot stopped", f"Fatal API response: {body}", urgent=True
                    )
                    self._stop.set()
                return
            self.limiter.pause(HTTP_BACKOFF_SECONDS)
            LOG.warning("unexpected response: %s", body[:100])
            return

        activation_id, phone = number
        decision = self._decide(activation_id, phone)
        if decision == "dup":
            return
        if decision == "cancel":
            LOG.warning("over cap: cancelling extra activation=%s number=%s", activation_id, phone)
            try:
                client.cancel(activation_id)
            except requests.RequestException as error:
                LOG.warning("cancel failed activation=%s: %s", activation_id, type(error).__name__)
                self.notifier.send(
                    "Extra number cancel FAILED",
                    f"Manual cancellation needed: activation {activation_id} ({phone})",
                    urgent=True,
                )
                return
            self.notifier.send(
                "Extra number cancelled", f"Refunded activation {activation_id} ({phone})"
            )
            return
        LOG.info("number acquired worker=%s activation=%s number=%s", worker_id, activation_id, phone)
        self.notifier.send(
            "GRIZZLY NUMBER ACQUIRED", f"Number: {phone}\nActivation: {activation_id}", urgent=True
        )

    def _worker(self, worker_id: int) -> None:
        client = GrizzlyClient(self.config.api_key, self.config.timeout, self.config.api_url)
        try:
            while self.limiter.wait(self._stop):
                self._poll_once(client, worker_id)
        finally:
            client.close()

    def run(self, shutdown: threading.Event) -> list[tuple[str, str]]:
        threads = [
            threading.Thread(target=self._worker, args=(i,), name=f"poll-{i}")
            for i in range(1, self.config.workers + 1)
        ]
        for thread in threads:
            thread.start()
        # Supervise: mirror a global shutdown into the acquirer's own stop event.
        while any(thread.is_alive() for thread in threads):
            if shutdown.wait(0.2):
                self._stop.set()
                break
        for thread in threads:
            thread.join()
        return list(self._kept)


class Watcher:
    """Polls getStatus for activations until code / cancellation / timeout."""

    def __init__(self, config: Config, notifier: Notifier) -> None:
        self.config = config
        self.notifier = notifier
        self.client = GrizzlyClient(config.api_key, config.timeout, config.api_url)
        self.limiter = RateLimiter(config.rate)

    def watch(self, activation_ids: list[str], shutdown: threading.Event) -> None:
        pending = set(activation_ids)
        reported: dict[str, str] = {}
        deadline = time.monotonic() + self.config.watch_timeout_seconds
        LOG.info("watching %s activation(s): %s", len(pending), ", ".join(sorted(pending)))
        while pending and not shutdown.is_set() and time.monotonic() < deadline:
            for activation_id in sorted(pending):
                if not self.limiter.wait(shutdown):
                    break
                try:
                    body = self.client.get_status(activation_id)
                except requests.RequestException as error:
                    LOG.warning("id=%s network error: %s", activation_id, type(error).__name__)
                    continue
                if body.startswith("STATUS_OK"):
                    code = body.split(":", 1)[1] if ":" in body else ""
                    LOG.info("id=%s SMS received: %s", activation_id, code)
                    self.notifier.send(
                        "SMS code received", f"Activation {activation_id}\nCode: {code}", urgent=True
                    )
                    pending.discard(activation_id)
                elif body.startswith("STATUS_WAIT_RETRY"):
                    code = body.split(":", 1)[1] if ":" in body else ""
                    if reported.get(activation_id) != code:
                        reported[activation_id] = code
                        LOG.info("id=%s code (retry): %s", activation_id, code)
                        self.notifier.send(
                            "SMS code received (retry)",
                            f"Activation {activation_id}\nCode: {code}",
                            urgent=True,
                        )
                elif body == "STATUS_CANCEL":
                    LOG.info("id=%s cancelled/expired", activation_id)
                    self.notifier.send(
                        "Number expired", f"Activation {activation_id} cancelled/expired"
                    )
                    pending.discard(activation_id)
                elif body == "STATUS_WAIT_CODE":
                    pass  # keep waiting for the SMS
                else:
                    LOG.warning("id=%s unexpected: %s", activation_id, body[:100])
            if pending:
                shutdown.wait(self.config.status_poll_seconds)
        if pending:
            remaining = ", ".join(sorted(pending))
            if shutdown.is_set():
                LOG.info("shutdown requested — still waiting on: %s", remaining)
                self.notifier.send("Watch interrupted", f"Shutdown requested; still waiting: {remaining}")
            else:
                LOG.info("watch timeout — still waiting on: %s", remaining)
                self.notifier.send("Watch timeout", f"Still waiting: {remaining}")
        else:
            LOG.info("all activations resolved")

    def close(self) -> None:
        self.client.close()


def fetch_stock(client: GrizzlyClient, service: str, country: str) -> Stock | None:
    """Live stock/price for `service`+`country` via getPricesV3, or `None` on any

    error (network, HTTP, malformed body) -- best-effort, never raises, since this
    is informational and must not block acquisition.
    """
    try:
        response = client.get_prices_v3(service, country)
    except requests.RequestException as error:
        LOG.warning("getPricesV3 network error: %s", type(error).__name__)
        return None

    if response.status_code != 200:
        LOG.warning("getPricesV3 HTTP %s", response.status_code)
        return None

    body = response.text.strip()
    if body in PRICE_API_ERRORS:
        LOG.warning("getPricesV3 error: %s", body)
        return None

    try:
        data = response.json()
    except ValueError:
        LOG.warning("getPricesV3: non-JSON response: %s", body[:200])
        return None

    stock = parse_stock(data, country, service)
    if stock is None:
        LOG.warning(
            "getPricesV3: no entry for service=%s country=%s in response: %s",
            service, country, body[:200],
        )
    return stock


def fetch_service_name(client: GrizzlyClient, service: str) -> str | None:
    """Human-readable service name via getServicesList. Best-effort display sugar

    only -- never a reason to fail the stock check itself.
    """
    try:
        response = client.get_services_list()
        response.raise_for_status()
        for entry in response.json().get("services", []):
            if entry.get("code") == service:
                name = entry.get("name")
                return str(name) if name else None
    except (requests.RequestException, ValueError, AttributeError) as error:
        LOG.debug("could not resolve service name: %s", type(error).__name__)
    return None


def fetch_country_name(client: GrizzlyClient, country: str) -> str | None:
    """Human-readable country name via getCountries. Best-effort only, and the

    response shape is less certain than getPricesV3's: the docs example for this
    endpoint renders oddly (double-nested braces), so this accepts either a
    `{id: {...}}` mapping or a plain list of entries.
    """
    try:
        response = client.get_countries()
        response.raise_for_status()
        data = response.json()
        entries = data.values() if isinstance(data, dict) else data
        for entry in entries:
            if str(entry.get("id")) == country:
                name = entry.get("eng")
                return str(name) if name else None
    except (requests.RequestException, ValueError, AttributeError) as error:
        LOG.debug("could not resolve country name: %s", type(error).__name__)
    return None


class StockWatcher:
    """Re-checks live stock/price for service+country on its own schedule, fully

    independent of `getNumber` polling (no balance spent, no number reserved).
    Runs only during the acquire phase (`bot.run`) -- once a number is kept, stock
    information is no longer relevant, so this stops when the caller calls
    `stop()`, in addition to reacting to the shared `shutdown` event.
    """

    def __init__(self, config: Config, notifier: Notifier, client_factory=GrizzlyClient) -> None:
        self.config = config
        self.notifier = notifier
        self._client_factory = client_factory
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self, shutdown: threading.Event) -> None:
        client = self._client_factory(self.config.api_key, self.config.timeout, self.config.api_url)
        try:
            while not shutdown.is_set() and not self._stop.is_set():
                stock = fetch_stock(client, self.config.service, self.config.country)
                LOG.info("%s", format_stock(self.config.service, self.config.country, stock))
                remaining = self.config.price_check_interval
                while remaining > 0 and not shutdown.is_set() and not self._stop.is_set():
                    step = min(remaining, 1.0)
                    if self._stop.wait(step):
                        return
                    if shutdown.is_set():
                        return
                    remaining -= step
        finally:
            client.close()


def run(
    config: Config,
    notifier: Notifier,
    shutdown: threading.Event,
    *,
    client_factory=GrizzlyClient,
) -> int:
    """Full flow: startup notice (with live stock/price) -> acquire (hard cap),

    stock re-checked in the background -> watch the kept number(s).

    `client_factory` builds a `GrizzlyClient`-like object from `(api_key, timeout,
    api_url)` -- overridable in tests so startup name/stock resolution never
    touches the real network (mirrors `Acquirer`/`Watcher`, which take a
    pre-built client per call rather than constructing one themselves).
    """
    cfg = config
    stop_after = cfg.max_acquisitions or "unlimited"

    startup_client = client_factory(cfg.api_key, cfg.timeout, cfg.api_url)
    try:
        service_name = fetch_service_name(startup_client, cfg.service)
        country_name = fetch_country_name(startup_client, cfg.country)
        stock = fetch_stock(startup_client, cfg.service, cfg.country)
    finally:
        startup_client.close()
    stock_line = format_stock(cfg.service, cfg.country, stock)

    LOG.info(
        "startup service=%s country=%s maxPrice=%s providerIds=%s exceptProviderIds=%s "
        "workers=%s limit=%.1f/s stop_after=%s notify=%s",
        cfg.service, cfg.country, cfg.max_price, cfg.provider_ids or "none",
        cfg.except_provider_ids or "none",
        cfg.workers, cfg.rate, stop_after, ",".join(notifier.backend_names),
    )
    LOG.info("%s", stock_line)
    if service_name or country_name:
        LOG.info(
            "resolved names: service=%s country=%s",
            service_name or cfg.service, country_name or cfg.country,
        )

    notifier.send(
        "Grizzly SMS bot started",
        f"Target service={service_name or cfg.service} country={country_name or cfg.country} "
        f"maxPrice={cfg.max_price}\n"
        f"{cfg.workers} workers, limit {cfg.rate:g} req/s, stop after {stop_after} acquisition(s).\n"
        f"{stock_line}",
    )

    stock_watcher = StockWatcher(cfg, notifier, client_factory=client_factory)
    stock_thread = threading.Thread(target=stock_watcher.run, args=(shutdown,), name="stock-watcher")
    stock_thread.start()
    try:
        acquirer = Acquirer(cfg, notifier)
        kept = acquirer.run(shutdown)
    finally:
        stock_watcher.stop()
        stock_thread.join()

    if not kept:
        if acquirer.fatal:
            LOG.info("stopped on a fatal response — exiting non-zero")
            return 1
        LOG.info("no number acquired — exiting")
        return 0
    watcher = Watcher(cfg, notifier)
    try:
        watcher.watch([activation_id for activation_id, _ in kept], shutdown)
    finally:
        watcher.close()
    return 0
