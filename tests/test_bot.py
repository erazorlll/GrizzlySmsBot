import unittest

import requests

from grizzly.bot import Acquirer
from grizzly.config import Config


class FakeNotifier:
    def __init__(self):
        self.sent = []

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


if __name__ == "__main__":
    unittest.main()
