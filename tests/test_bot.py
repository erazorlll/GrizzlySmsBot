import unittest

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


if __name__ == "__main__":
    unittest.main()
