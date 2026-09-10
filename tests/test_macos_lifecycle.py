import os
import sys
import signal
import socket
import subprocess
import time
import pytest
import urllib.request
import re
import fcntl
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

@pytest.fixture
def isolated_lock_path():
    fd, path = tempfile.mkstemp(suffix=".lock", prefix="rech-v-tekst-test-")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)

def test_readiness_and_retry_html_exists():
    html_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'loading.html')
    assert os.path.exists(html_path)
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()
        assert 'Повторить попытку' in content
        assert 'checkServer()' in content

def test_server_startup_dynamic_port_and_cleanup(isolated_lock_path):
    # Spin up macos_app in TEST_MODE
    env = {**os.environ, 'TEST_MODE': '1', 'PYTHONUNBUFFERED': '1', 'RECH_V_TEKST_LOCK_PATH': isolated_lock_path}
    proc = subprocess.Popen(
        [sys.executable, 'macos_app.py'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True
    )

    port = None
    start_time = time.time()

    try:
        # Non-blocking or strict timeout loop to read output
        os.set_blocking(proc.stdout.fileno(), False)
        while time.time() - start_time < 5:
            line = proc.stdout.readline()
            if line:
                match = re.search(r'TEST_PORT=(\d+)', line)
                if match:
                    port = int(match.group(1))
                    break
            else:
                time.sleep(0.1)

        assert port is not None, "Server did not output TEST_PORT within timeout"
        assert port > 0

        # Test HTTP server is responsive and serves loading.html
        url = f"http://127.0.0.1:{port}/static/loading.html"
        req = urllib.request.urlopen(url)
        assert req.getcode() == 200

        # Send SIGINT (simulates KeyboardInterrupt / graceful shutdown trigger in test mode)
        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=5)

        assert proc.returncode == 0 # Clean exit

        # Verify lock is released by acquiring it ourselves
        fd = os.open(isolated_lock_path, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

        # Verify port is released
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('127.0.0.1', port))
        s.close()

    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)

def test_single_instance_lock(isolated_lock_path):
    # Acquire lock in parent
    from macos_app import acquire_single_instance_lock
    import macos_app

    os.environ['RECH_V_TEKST_LOCK_PATH'] = isolated_lock_path
    success = acquire_single_instance_lock()
    assert success is True

    try:
        # Try running another instance
        env = {**os.environ, 'RECH_V_TEKST_LOCK_PATH': isolated_lock_path, 'TEST_MODE': '1'}
        proc = subprocess.Popen(
            [sys.executable, 'macos_app.py'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env
        )
        proc.wait(timeout=5)
        # The app should exit with code 1 due to single instance lock
        assert proc.returncode == 1

        out, err = proc.communicate()
        assert b"already running" in out or b"already running" in err
    finally:
        # Release parent lock
        if macos_app.instance_lock_fd is not None:
            fcntl.flock(macos_app.instance_lock_fd, fcntl.LOCK_UN)
            os.close(macos_app.instance_lock_fd)
            macos_app.instance_lock_fd = None


class DummyWindow:
    def __init__(self):
        self.hidden = False
        self.shown = False

    def hide(self):
        self.hidden = True

    def show(self):
        self.shown = True


class DummyLauncher:
    def __init__(self):
        self.shutdown_called = False

    def shutdown(self):
        self.shutdown_called = True


def test_lifecycle_red_button_hides_window():
    from macos_app import LifecyclePolicy
    window = DummyWindow()
    launcher = DummyLauncher()
    policy = LifecyclePolicy(window=window, launcher=launcher, is_busy_func=lambda: False)

    result = policy.handle_window_closing()
    assert result is False
    assert window.hidden is True
    assert launcher.shutdown_called is False
    assert policy.is_terminating is False


def test_lifecycle_dock_reopen_shows_window():
    from macos_app import LifecyclePolicy
    window = DummyWindow()
    launcher = DummyLauncher()
    policy = LifecyclePolicy(window=window, launcher=launcher)

    result = policy.handle_reopen()
    assert result is True
    assert window.shown is True


def test_lifecycle_idle_quit_terminates_cleanly():
    from macos_app import LifecyclePolicy
    window = DummyWindow()
    launcher = DummyLauncher()
    policy = LifecyclePolicy(window=window, launcher=launcher, is_busy_func=lambda: False)

    should_terminate = policy.request_termination()
    assert should_terminate is True
    assert policy.is_terminating is True
    assert launcher.shutdown_called is True

    # Closing event during termination must now allow close (return True)
    assert policy.handle_window_closing() is True


def test_lifecycle_busy_quit_cancel_keeps_running():
    from macos_app import LifecyclePolicy
    window = DummyWindow()
    launcher = DummyLauncher()
    policy = LifecyclePolicy(
        window=window,
        launcher=launcher,
        is_busy_func=lambda: True,
        confirm_dialog_func=lambda: False,
    )

    should_terminate = policy.request_termination()
    assert should_terminate is False
    assert policy.is_terminating is False
    assert launcher.shutdown_called is False

    # Window close should still be prevented (hidden instead)
    assert policy.handle_window_closing() is False
    assert window.hidden is True


def test_lifecycle_busy_quit_confirm_terminates_cleanly():
    from macos_app import LifecyclePolicy
    window = DummyWindow()
    launcher = DummyLauncher()
    policy = LifecyclePolicy(
        window=window,
        launcher=launcher,
        is_busy_func=lambda: True,
        confirm_dialog_func=lambda: True,
    )

    should_terminate = policy.request_termination()
    assert should_terminate is True
    assert policy.is_terminating is True
    assert launcher.shutdown_called is True

    # Window close should now be allowed
    assert policy.handle_window_closing() is True


def test_lifecycle_shutdown_releases_single_instance_lock(isolated_lock_path):
    from macos_app import ServerLauncher, LifecyclePolicy, acquire_single_instance_lock
    import macos_app

    os.environ['RECH_V_TEKST_LOCK_PATH'] = isolated_lock_path
    assert acquire_single_instance_lock() is True
    assert macos_app.instance_lock_fd is not None

    launcher = ServerLauncher(None)
    policy = LifecyclePolicy(launcher=launcher, is_busy_func=lambda: False)

    # Request termination
    assert policy.request_termination() is True

    # Lock must be released so we can acquire it again
    assert acquire_single_instance_lock() is True
    # Clean up
    if macos_app.instance_lock_fd is not None:
        fcntl.flock(macos_app.instance_lock_fd, fcntl.LOCK_UN)
        os.close(macos_app.instance_lock_fd)
        macos_app.instance_lock_fd = None


def test_custom_app_delegate_with_appkit():
    from macos_app import CustomAppDelegate, LifecyclePolicy
    try:
        from AppKit import NSTerminateNow, NSTerminateCancel
    except ImportError:
        pytest.skip("AppKit not available")

    assert CustomAppDelegate is not None

    # Test idle quit
    window = DummyWindow()
    launcher = DummyLauncher()
    idle_policy = LifecyclePolicy(window=window, launcher=launcher, is_busy_func=lambda: False)
    delegate = CustomAppDelegate.alloc().init()
    delegate.policy = idle_policy

    assert delegate.applicationShouldTerminate_(None) == NSTerminateNow
    assert idle_policy.is_terminating is True
    assert launcher.shutdown_called is True

    # Test busy cancelled quit
    busy_launcher = DummyLauncher()
    busy_policy = LifecyclePolicy(
        window=window,
        launcher=busy_launcher,
        is_busy_func=lambda: True,
        confirm_dialog_func=lambda: False,
    )
    delegate.policy = busy_policy
    assert delegate.applicationShouldTerminate_(None) == NSTerminateCancel
    assert busy_policy.is_terminating is False
    assert busy_launcher.shutdown_called is False

    # Test reopen
    reopen_called = delegate.applicationShouldHandleReopen_hasVisibleWindows_(None, False)
    assert reopen_called is True
    assert window.shown is True

def test_default_confirm_dialog_fail_closed(monkeypatch):
    from macos_app import default_confirm_dialog

    # Mock AppKit.NSAlert to raise an exception
    class MockNSAlert:
        @classmethod
        def alloc(cls):
            raise Exception("Mocked UI exception")

    import sys
    # Patch AppKit in sys.modules
    class MockAppKit:
        NSAlert = MockNSAlert

    monkeypatch.setitem(sys.modules, 'AppKit', MockAppKit())

    result = default_confirm_dialog()
    assert result is False

def test_idempotent_shutdown():
    from macos_app import ServerLauncher
    launcher = ServerLauncher(None)

    calls = 0
    def mock_server_close(*args, **kwargs):
        nonlocal calls
        calls += 1

    class MockServer:
        def shutdown(self): pass
        def server_close(self): mock_server_close()

    launcher.http_server = MockServer()

    # First shutdown
    launcher.shutdown()
    assert getattr(launcher, '_is_shutdown', False) is True

    # Second shutdown should not crash or double-release
    try:
        launcher.shutdown()
    except Exception as e:
        pytest.fail(f"Second shutdown failed: {e}")

    assert calls == 1
