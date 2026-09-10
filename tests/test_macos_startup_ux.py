import os
import sys
import time
import pytest
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

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
    assert launcher.retry() is None

def test_stale_startup_abandoned():
    window = MockWindow()
    launcher = ServerLauncher(window)
    launcher._timeout = 0.5

    servers_created = []

    class MockServer:
        def __init__(self, port):
            self.server_port = port
            self.closed = False
            self.serve_called = False
            servers_created.append(self)
        def serve_forever(self):
            self.serve_called = True
        def shutdown(self):
            pass
        def server_close(self):
            self.closed = True

    state = {'stage': 0}
    def delayed_startup():
        if state['stage'] == 0:
            state['stage'] = 1
            time.sleep(1.0) # Longer than 0.5s timeout -> becomes stale
            return MockServer(100) # Stale A
        else:
            return MockServer(200) # Fresh B

    launcher._startup_func = delayed_startup

    # Start Attempt A
    launcher.start_async()
    time.sleep(0.7) # Wait for A's watchdog timeout to fire
    assert not launcher.is_starting # A should have timed out

    # Start Attempt B (retry)
    launcher.retry()
    time.sleep(0.2) # B finishes immediately since state['stage'] == 1

    # Wait for A to finally return its stale server
    time.sleep(1.0)

    # Validate active server is B
    assert launcher.server_port == 200
    assert len(servers_created) == 2

    server_A = next(s for s in servers_created if s.server_port == 100)
    server_B = next(s for s in servers_created if s.server_port == 200)

    assert server_A.server_port == 100
    assert server_A.closed is True
    assert server_A.serve_called is False

    assert server_B.server_port == 200
    assert server_B.closed is False
    assert server_B.serve_called is True

def test_invalid_startup_return():
    window = MockWindow()
    launcher = ServerLauncher(window)

    def invalid_startup():
        return None # No server API

    launcher._startup_func = invalid_startup
    launcher.start_async()
    time.sleep(0.5)

    assert not launcher.is_starting
    error_calls = [c for c in window.js_calls if "showError" in c]
    assert len(error_calls) > 0
    assert "корректный объект" in error_calls[0]
