import unittest

from grizzly.api import is_fatal_response, parse_number


class ParseNumberTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(parse_number("ACCESS_NUMBER:123:33612"), ("123", "33612"))

    def test_extra_colons_kept_in_number(self):
        self.assertEqual(parse_number("ACCESS_NUMBER:1:2:3"), ("1", "2:3"))

    def test_no_numbers(self):
        self.assertIsNone(parse_number("NO_NUMBERS"))

    def test_empty_parts(self):
        self.assertIsNone(parse_number("ACCESS_NUMBER:123:"))
        self.assertIsNone(parse_number("ACCESS_NUMBER::9"))

    def test_other(self):
        self.assertIsNone(parse_number("BAD_KEY"))


class FatalResponseTests(unittest.TestCase):
    def test_exact(self):
        self.assertTrue(is_fatal_response("BAD_KEY"))
        self.assertTrue(is_fatal_response("NO_BALANCE"))

    def test_prefix(self):
        self.assertTrue(is_fatal_response("WRONG_MAX_PRICE:0.500000"))

    def test_non_fatal(self):
        self.assertFalse(is_fatal_response("NO_NUMBERS"))
        self.assertFalse(is_fatal_response("ACCESS_NUMBER:1:2"))
        self.assertFalse(is_fatal_response(""))


if __name__ == "__main__":
    unittest.main()
