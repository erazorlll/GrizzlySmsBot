"""Notification backends (ntfy and/or Discord) and a fan-out notifier."""

from __future__ import annotations

import logging
import threading

import requests

from .config import Config

LOG = logging.getLogger("grizzly.notify")

_GREEN = 0x2ECC71
_RED = 0xE74C3C


def _session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = "grizzlysms-bot/1.0"
    return session


class NtfyBackend:
    name = "ntfy"

    def __init__(self, url: str, timeout: float) -> None:
        self.url = url
        self.timeout = timeout
        self.session = _session()

    def send(self, title: str, message: str, urgent: bool) -> bool:
        try:
            response = self.session.post(
                self.url,
                data=message.encode(),
                headers={
                    "Title": title,
                    "Priority": "urgent" if urgent else "default",
                    "Tags": "telephone_receiver" if urgent else "white_check_mark",
                },
                timeout=self.timeout,
            )
            return response.ok
        except requests.RequestException as error:
            LOG.warning("ntfy error: %s", type(error).__name__)
            return False

    def close(self) -> None:
        self.session.close()


class DiscordBackend:
    name = "discord"

    def __init__(self, url: str, timeout: float) -> None:
        self.url = url
        self.timeout = timeout
        self.session = _session()

    def send(self, title: str, message: str, urgent: bool) -> bool:
        payload = {
            "embeds": [
                {
                    "title": title,
                    "description": message,
                    "color": _RED if urgent else _GREEN,
                }
            ]
        }
        try:
            response = self.session.post(self.url, json=payload, timeout=self.timeout)
            return response.ok
        except requests.RequestException as error:
            LOG.warning("discord error: %s", type(error).__name__)
            return False

    def close(self) -> None:
        self.session.close()


class Notifier:
    """Fans a notification out to every configured backend (thread-safe)."""

    def __init__(self, backends: list) -> None:
        if not backends:
            raise ValueError(
                "no notifier configured: set NTFY_URL and/or DISCORD_WEBHOOK_URL"
            )
        self._backends = backends
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, config: Config) -> "Notifier":
        backends: list = []
        if config.ntfy_url:
            backends.append(NtfyBackend(config.ntfy_url, config.timeout))
        if config.discord_webhook_url:
            backends.append(DiscordBackend(config.discord_webhook_url, config.timeout))
        return cls(backends)

    @property
    def backend_names(self) -> list:
        return [backend.name for backend in self._backends]

    def send(self, title: str, message: str, urgent: bool = False) -> bool:
        """Send to all backends; returns True if at least one succeeded."""
        with self._lock:
            results = [backend.send(title, message, urgent) for backend in self._backends]
        return any(results)

    def close(self) -> None:
        for backend in self._backends:
            backend.close()
