import unittest
from unittest.mock import patch

from packaged_smoke_test import wait_for_test_port


class _FakeStdout:
    def __init__(self, lines):
        self._lines = iter(lines)

    def readline(self):
        return next(self._lines, "")


class _FakeProcess:
    def __init__(self, lines, returncodes=None):
        self.stdout = _FakeStdout(lines)
        self._returncodes = iter(returncodes or [])

    def poll(self):
        return next(self._returncodes, None)


class TestPackagedSmokeStartup(unittest.TestCase):
    @patch("packaged_smoke_test.time.sleep")
    @patch("packaged_smoke_test.time.monotonic", side_effect=[0, 0, 1, 2, 3])
    def test_waits_through_delayed_output(self, _monotonic, _sleep):
        proc = _FakeProcess(["", "", "TEST_PORT=49152\n"])

        self.assertEqual(wait_for_test_port(proc, timeout=10), 49152)

    @patch("packaged_smoke_test.time.sleep")
    @patch("packaged_smoke_test.time.monotonic", side_effect=[0, 0, 1])
    def test_stops_when_process_exits_before_port(self, _monotonic, _sleep):
        proc = _FakeProcess([""], returncodes=[1])

        self.assertIsNone(wait_for_test_port(proc, timeout=10))


if __name__ == "__main__":
    unittest.main()
