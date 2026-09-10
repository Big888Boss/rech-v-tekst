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
