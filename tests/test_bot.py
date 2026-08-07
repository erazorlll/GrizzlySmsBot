import json
import threading
import unittest

import requests

from grizzly.bot import Acquirer, StockWatcher, run
from grizzly.config import Config


class FakeNotifier:
    def __init__(self):
        self.sent = []
        self.backend_names = ["fake"]

    def send(self, title, message, urgent=False):
        self.sent.append((title, message, urgent))
        return True


def make_acquirer(max_acquisitions):
    config = Config(api_key="k", max_acquisitions=max_acquisitions, rate=100.0)
    return Acquirer(config, FakeNotifier())


class DecideTests(unittest.TestCase):
    def test_cap_one_keeps_first_cancels_rest(self):
        acq = make_acquirer(1)
        self.assertEqual(acq._decide("a", "1"), "keep")
        self.assertTrue(acq._stop.is_set())
        self.assertEqual(acq._decide("b", "2"), "cancel")
        self.assertEqual(acq._decide("a", "1"), "dup")
        self.assertEqual([aid for aid, _ in acq._kept], ["a"])

    def test_cap_two(self):
        acq = make_acquirer(2)
        self.assertEqual(acq._decide("a", "1"), "keep")
        self.assertFalse(acq._stop.is_set())
        self.assertEqual(acq._decide("b", "2"), "keep")
        self.assertTrue(acq._stop.is_set())
        self.assertEqual(acq._decide("c", "3"), "cancel")

    def test_unlimited_keeps_all(self):
        acq = make_acquirer(0)
        for i in range(5):
            self.assertEqual(acq._decide(str(i), "x"), "keep")
        self.assertFalse(acq._stop.is_set())
        self.assertEqual(len(acq._kept), 5)


class FakeResponse:
    def __init__(self, text, status_code=200, headers=None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeClient:
    def __init__(self, number_body, cancel_error=None):
        self._number_body = number_body
        self._cancel_error = cancel_error
        self.cancelled = []

    def get_number(self, params):
        return FakeResponse(self._number_body)

    def cancel(self, activation_id):
        self.cancelled.append(activation_id)
        if self._cancel_error is not None:
            raise self._cancel_error
        return "ACCESS_CANCEL"


class CancelPathTests(unittest.TestCase):
    def _acquirer_at_cap(self):
        acq = make_acquirer(1)
        acq._decide("kept", "111")  # count == cap -> the next acquisition must cancel
        return acq

    def test_cancel_failure_does_not_report_refund(self):
        acq = self._acquirer_at_cap()
        client = FakeClient("ACCESS_NUMBER:extra:222", cancel_error=requests.RequestException("boom"))
        acq._poll_once(client, worker_id=1)
        titles = [title for title, _, _ in acq.notifier.sent]
        self.assertIn("extra", client.cancelled)
        self.assertIn("Extra number cancel FAILED", titles)
        self.assertNotIn("Extra number cancelled", titles)

    def test_cancel_success_reports_refund(self):
        acq = self._acquirer_at_cap()
        client = FakeClient("ACCESS_NUMBER:extra:222")
        acq._poll_once(client, worker_id=1)
        titles = [title for title, _, _ in acq.notifier.sent]
        self.assertIn("extra", client.cancelled)
        self.assertIn("Extra number cancelled", titles)
        self.assertNotIn("Extra number cancel FAILED", titles)


class FakeStartupClient:
    """Stands in for `GrizzlyClient` in tests that exercise `bot.run`'s startup

    path (name/stock resolution via `client_factory`) -- keeps that path off the
    real network, matching the "no network" guarantee the other tests already have.
    """

    def __init__(self, *_args, **_kwargs):
        self.closed = False

    def get_prices_v3(self, service, country):
        return FakeResponse(
            json.dumps({country: {service: {"price": 2, "count": 5, "providers": {}}}})
        )

    def get_services_list(self):
        return FakeResponse(json.dumps({"services": [{"code": "wx", "name": "Apple"}]}))

    def get_countries(self):
        return FakeResponse(json.dumps({"62": {"id": "62", "eng": "Turkey"}}))

    def close(self):
        self.closed = True


class RunStartupTests(unittest.TestCase):
    def test_run_logs_startup_without_crashing(self):
        config = Config(
            api_key="k", service="wx", country="62", max_price="1",
            provider_ids="385", except_provider_ids="12,25,311",
            workers=0, rate=1.0, ntfy_url="x", price_check_interval=10.0,
        )
        notifier = FakeNotifier()
        rc = run(config, notifier, threading.Event(), client_factory=FakeStartupClient)
        self.assertEqual(rc, 0)
        titles = [title for title, _, _ in notifier.sent]
        self.assertIn("Grizzly SMS bot started", titles)

    def test_startup_notification_includes_resolved_names_and_stock(self):
        config = Config(
            api_key="k", service="wx", country="62", max_price="1",
            workers=0, rate=1.0, ntfy_url="x",
        )
        notifier = FakeNotifier()
        run(config, notifier, threading.Event(), client_factory=FakeStartupClient)
        _, message, _ = next(s for s in notifier.sent if s[0] == "Grizzly SMS bot started")
        self.assertIn("Apple", message)
        self.assertIn("Turkey", message)
        self.assertIn("available=5", message)

    def test_stock_watcher_is_stopped_and_joined_before_returning(self):
        config = Config(
            api_key="k", service="wx", country="62", max_price="1",
            workers=0, rate=1.0, ntfy_url="x", price_check_interval=1000.0,
        )
        notifier = FakeNotifier()
        # A long price_check_interval would hang the thread for the whole test run
        # if `run()` failed to stop+join the watcher before returning.
        run(config, notifier, threading.Event(), client_factory=FakeStartupClient)
        self.assertEqual(
            sum(1 for t in threading.enumerate() if t.name == "stock-watcher"), 0
        )


class StockWatcherTests(unittest.TestCase):
    def test_stop_ends_the_run_loop_promptly(self):
        config = Config(api_key="k", service="wx", country="62", price_check_interval=1000.0)
        watcher = StockWatcher(config, FakeNotifier(), client_factory=FakeStartupClient)
        thread = threading.Thread(target=watcher.run, args=(threading.Event(),))
        thread.start()
        watcher.stop()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())

    def test_shutdown_event_ends_the_run_loop_promptly(self):
        config = Config(api_key="k", service="wx", country="62", price_check_interval=1000.0)
        watcher = StockWatcher(config, FakeNotifier(), client_factory=FakeStartupClient)
        shutdown = threading.Event()
        thread = threading.Thread(target=watcher.run, args=(shutdown,))
        thread.start()
        shutdown.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
