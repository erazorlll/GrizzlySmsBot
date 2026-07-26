"""Environment-backed configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

API_URL = "https://api.grizzlysms.com/stubs/handler_api.php"


def load_dotenv(path: str | None = None) -> None:
    """Load ``KEY=VALUE`` lines from a .env file into ``os.environ``.

    Best-effort: a missing file is fine. Variables already present in the
    environment are NOT overridden, so an explicit env var (or Docker's
    ``env_file``) always wins over the .env file. Set ``GRIZZLY_ENV_FILE`` to
    point at a different path.
    """
    path = path or os.getenv("GRIZZLY_ENV_FILE", ".env")
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if key:
            os.environ.setdefault(key, value.strip().strip('"').strip("'"))


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
    raw = os.getenv(name, "").strip()
    value = int(raw) if raw else default
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def env_float(name: str, minimum: float = 0.1) -> float:
    value = float(env_required(name))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def env_float_optional(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.getenv(name, "").strip()
    value = float(raw) if raw else default
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


@dataclass(frozen=True)
class Config:
    api_key: str
    ntfy_url: str | None = None
    discord_webhook_url: str | None = None
    timeout: float = 10.0
    status_poll_seconds: float = 5.0
    watch_timeout_seconds: float = 1200.0
    # Acquisition-only fields (unused by the `watch` subcommand):
    service: str | None = None
    country: str | None = None
    max_price: str | None = None
    provider_ids: str | None = None
    except_provider_ids: str | None = None
    workers: int = 1
    rate: float = 1.0
    max_acquisitions: int = 1
    status_every: int = 100
    api_url: str = API_URL

    @classmethod
    def from_env(cls) -> "Config":
        """Full configuration for the acquire-then-watch flow."""
        return cls(
            api_key=env_required("GRIZZLY_API_KEY"),
            service=env_required("SERVICE"),
            country=env_required("COUNTRY"),
            max_price=env_required("MAX_PRICE"),
            provider_ids=os.getenv("PROVIDER_IDS", "").strip() or None,
            except_provider_ids=os.getenv("EXCEPT_PROVIDER_IDS", "").strip() or None,
            workers=env_int("THREADS"),
            rate=env_float("MAX_REQUESTS_PER_SECOND"),
            timeout=env_float("REQUEST_TIMEOUT_SECONDS", 1),
            max_acquisitions=env_int_optional("MAX_ACQUISITIONS", 1, minimum=0),
            status_every=env_int_optional("STATUS_EVERY_REQUESTS", 100),
            status_poll_seconds=env_float_optional("STATUS_POLL_SECONDS", 5.0, minimum=1.0),
            watch_timeout_seconds=env_float_optional("WATCH_TIMEOUT_SECONDS", 1200.0, minimum=1.0),
            ntfy_url=os.getenv("NTFY_URL", "").strip() or None,
            discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", "").strip() or None,
            api_url=os.getenv("GRIZZLY_API_URL", API_URL),
        )

    @classmethod
    def for_watch(cls) -> "Config":
        """Lighter configuration for the `watch` subcommand (no purchase params)."""
        return cls(
            api_key=env_required("GRIZZLY_API_KEY"),
            timeout=env_float_optional("REQUEST_TIMEOUT_SECONDS", 10.0, minimum=1.0),
            status_poll_seconds=env_float_optional("STATUS_POLL_SECONDS", 5.0, minimum=1.0),
            watch_timeout_seconds=env_float_optional("WATCH_TIMEOUT_SECONDS", 1200.0, minimum=1.0),
            ntfy_url=os.getenv("NTFY_URL", "").strip() or None,
            discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", "").strip() or None,
            api_url=os.getenv("GRIZZLY_API_URL", API_URL),
        )

    @property
    def params(self) -> dict[str, str]:
        """Query parameters for a `getNumber` request."""
        params = {
            "api_key": self.api_key,
            "action": "getNumber",
            "service": self.service,
            "country": self.country,
            "maxPrice": self.max_price,
        }
        if self.provider_ids:
            params["providerIds"] = self.provider_ids
        if self.except_provider_ids:
            params["exceptProviderIds"] = self.except_provider_ids
        return params
