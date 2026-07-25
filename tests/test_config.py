import os
import tempfile
import unittest

from grizzly.config import load_dotenv


class LoadDotenvTests(unittest.TestCase):
    KEYS = ["GRIZZLY_TEST_A", "GRIZZLY_TEST_B", "GRIZZLY_TEST_EXISTING", "GRIZZLY_TEST_EXPORT"]

    def tearDown(self):
        for key in self.KEYS:
            os.environ.pop(key, None)

    def _write(self, content):
        fd, path = tempfile.mkstemp(suffix=".env")
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
        self.addCleanup(os.remove, path)
        return path

    def test_loads_values_and_skips_comments(self):
        path = self._write(
            "# a comment\n"
            "\n"
            "GRIZZLY_TEST_A=hello\n"
            "export GRIZZLY_TEST_EXPORT=exported\n"
            'GRIZZLY_TEST_B="quoted value"\n'
        )
        load_dotenv(path)
        self.assertEqual(os.environ["GRIZZLY_TEST_A"], "hello")
        self.assertEqual(os.environ["GRIZZLY_TEST_EXPORT"], "exported")
        self.assertEqual(os.environ["GRIZZLY_TEST_B"], "quoted value")

    def test_does_not_override_existing_env(self):
        os.environ["GRIZZLY_TEST_EXISTING"] = "from_env"
        path = self._write("GRIZZLY_TEST_EXISTING=from_file\n")
        load_dotenv(path)
        self.assertEqual(os.environ["GRIZZLY_TEST_EXISTING"], "from_env")

    def test_missing_file_is_noop(self):
        load_dotenv("/nonexistent/path/to/.env")  # must not raise


if __name__ == "__main__":
    unittest.main()
