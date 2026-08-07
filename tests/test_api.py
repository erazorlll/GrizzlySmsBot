import unittest

from grizzly.api import format_stock, is_fatal_response, parse_number, parse_stock


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


class ParseStockTests(unittest.TestCase):
    def test_valid_entry_with_providers(self):
        data = {
            "62": {
                "wx": {
                    "price": 2,
                    "count": 5,
                    "providers": {
                        "311": {"count": 3, "price": [2, 2.5]},
                        "312": {"count": 2, "price": [3]},
                    },
                }
            }
        }
        stock = parse_stock(data, "62", "wx")
        self.assertEqual(stock.count, 5)
        self.assertEqual(stock.price, 2.0)
        self.assertEqual({p.provider_id for p in stock.providers}, {"311", "312"})

    def test_valid_entry_without_providers(self):
        data = {"62": {"wx": {"price": 2, "count": 5}}}
        stock = parse_stock(data, "62", "wx")
        self.assertEqual(stock.count, 5)
        self.assertEqual(stock.providers, ())

    def test_missing_country_or_service(self):
        self.assertIsNone(parse_stock({}, "62", "wx"))
        self.assertIsNone(parse_stock({"62": {}}, "62", "wx"))

    def test_malformed_shape_returns_none_not_raise(self):
        self.assertIsNone(parse_stock({"62": {"wx": {"price": "n/a"}}}, "62", "wx"))
        self.assertIsNone(parse_stock("not a dict", "62", "wx"))
        self.assertIsNone(parse_stock(None, "62", "wx"))


class FormatStockTests(unittest.TestCase):
    def test_none_stock(self):
        text = format_stock("wx", "62", None)
        self.assertIn("no stock/price data", text)
        self.assertIn("service=wx", text)
        self.assertIn("country=62", text)

    def test_stock_without_providers(self):
        data = {"62": {"wx": {"price": 2, "count": 5}}}
        text = format_stock("wx", "62", parse_stock(data, "62", "wx"))
        self.assertEqual(text, "service=wx country=62 available=5 price=2")

    def test_stock_with_providers_sorted_and_formatted(self):
        data = {
            "62": {
                "wx": {
                    "price": 2,
                    "count": 5,
                    "providers": {
                        "312": {"count": 2, "price": [3]},
                        "311": {"count": 3, "price": [2, 2.5]},
                    },
                }
            }
        }
        text = format_stock("wx", "62", parse_stock(data, "62", "wx"))
        self.assertIn("providers=[311:3@2/2.5, 312:2@3]", text)


if __name__ == "__main__":
    unittest.main()
