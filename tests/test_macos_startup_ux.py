import time
import pytest
import threading

from macos_app import ServerLauncher

class MockWindow:
    def __init__(self):
        self.js_calls = []
        self.loaded_url = None
        self.lock = threading.Lock()

    def evaluate_js(self, js_code):
        with self.lock:
            self.js_calls.append(js_code)

    def load_url(self, url):
        with self.lock:
            self.loaded_url = url

def test_delayed_success():
    window = MockWindow()
    launcher = ServerLauncher(window)
    launcher._timeout = 2.0

    # Mock startup that takes 0.5 seconds
    class MockServer:
        server_port = 12345
        def serve_forever(self):
            pass
        def shutdown(self):
            pass
        def server_close(self):
            pass

    def slow_startup():
        time.sleep(0.5)
        return MockServer()

    launcher._startup_func = slow_startup

    assert launcher.start_async() is True
    assert "showLoading()" in window.js_calls

    # Wait for completion
    time.sleep(1.0)
    assert not launcher.is_starting
    assert launcher.server_port == 12345
    assert window.loaded_url == "http://127.0.0.1:12345/"

def test_startup_exception_shows_error():
    window = MockWindow()
    launcher = ServerLauncher(window)

    def failing_startup():
        raise RuntimeError("Fake bind error")

    launcher._startup_func = failing_startup
    launcher.start_async()

    time.sleep(0.5)
    assert not launcher.is_starting
    assert launcher.server_port is None

    error_calls = [c for c in window.js_calls if "showError" in c]
    assert len(error_calls) > 0
    assert "Fake bind error" in error_calls[0]

def test_retry_after_fail():
    window = MockWindow()
    launcher = ServerLauncher(window)

    fail_first = [True]

    class MockServer:
        server_port = 54321
        def serve_forever(self):
            pass
        def shutdown(self):
            pass
        def server_close(self):
            pass

    def flaky_startup():
        if fail_first[0]:
            fail_first[0] = False
            raise ValueError("Init fail")
        return MockServer()

    launcher._startup_func = flaky_startup

    # 1. First attempt fails
    launcher.start_async()
    time.sleep(0.5)
    assert not launcher.is_starting
    assert launcher.server_port is None

    # 2. Retry succeeds
    launcher.retry()
    time.sleep(0.5)
    assert not launcher.is_starting
    assert launcher.server_port == 54321
    assert window.loaded_url == "http://127.0.0.1:54321/"
    assert launcher._startup_attempts == 2

def test_startup_timeout():
    window = MockWindow()
    launcher = ServerLauncher(window)
    launcher._timeout = 0.5 # Fast timeout

    def infinite_startup():
        time.sleep(5.0) # Longer than timeout
        return None

    launcher._startup_func = infinite_startup
    launcher.start_async()

    time.sleep(1.0) # Wait for watchdog
    assert not launcher.is_starting
    assert launcher.server_port is None

    timeout_calls = [c for c in window.js_calls if "Таймаут" in c]
    assert len(timeout_calls) > 0

def test_no_duplicate_servers():
    window = MockWindow()
    launcher = ServerLauncher(window)

    def hanging_startup():
        time.sleep(2.0)
        return None

    launcher._startup_func = hanging_startup

    assert launcher.start_async() is True
    # Immediately try to start again
    assert launcher.start_async() is False
    assert launcher.retry() is None # Equivalent to start_async returning False/None

    assert launcher._startup_attempts == 1 # Only one attempt made it through the lock
