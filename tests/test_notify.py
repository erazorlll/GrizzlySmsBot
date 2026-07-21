import unittest

from grizzly.config import Config
from grizzly.notify import Notifier


class SelectionTests(unittest.TestCase):
    def _cfg(self, ntfy=None, discord=None):
        return Config(api_key="k", ntfy_url=ntfy, discord_webhook_url=discord)

    def test_ntfy_only(self):
        notifier = Notifier.from_config(self._cfg(ntfy="https://ntfy.sh/x"))
        self.assertEqual(notifier.backend_names, ["ntfy"])

    def test_discord_only(self):
        notifier = Notifier.from_config(self._cfg(discord="https://discord.com/api/webhooks/x/y"))
        self.assertEqual(notifier.backend_names, ["discord"])

    def test_both(self):
        notifier = Notifier.from_config(self._cfg(ntfy="a", discord="b"))
        self.assertEqual(set(notifier.backend_names), {"ntfy", "discord"})

    def test_none_raises(self):
        with self.assertRaises(ValueError):
            Notifier.from_config(self._cfg())


class FakeBackend:
    def __init__(self, name, ok):
        self.name = name
        self._ok = ok
        self.calls = []

    def send(self, title, message, urgent):
        self.calls.append((title, message, urgent))
        return self._ok

    def close(self):
        pass


class FanOutTests(unittest.TestCase):
    def test_fan_out_returns_true_if_any_succeeds(self):
        first, second = FakeBackend("a", False), FakeBackend("b", True)
        notifier = Notifier([first, second])
        self.assertTrue(notifier.send("t", "m", urgent=True))
        self.assertEqual(len(first.calls), 1)
        self.assertEqual(len(second.calls), 1)

    def test_all_fail_returns_false(self):
        only = FakeBackend("a", False)
        self.assertFalse(Notifier([only]).send("t", "m"))


if __name__ == "__main__":
    unittest.main()
