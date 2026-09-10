import os
import sys
import signal
import socket
import subprocess
import time
import pytest
import urllib.request
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

def test_readiness_and_retry_html_exists():
    html_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'loading.html')
    assert os.path.exists(html_path)
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()
        assert 'Повторить попытку' in content
        assert 'checkServer()' in content

def test_server_startup_dynamic_port_and_cleanup():
    # Spin up macos_app in TEST_MODE
    proc = subprocess.Popen(
        ['python3', 'macos_app.py'], 
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE,
        env={**os.environ, 'TEST_MODE': '1'},
        text=True
    )
    
    port = None
    start_time = time.time()
    while time.time() - start_time < 5:
        line = proc.stdout.readline()
        match = re.search(r'TEST_PORT=(\d+)', line)
        if match:
            port = int(match.group(1))
            break
            
    assert port is not None
    assert port > 0
    
    # Test HTTP server is responsive and serves loading.html
    url = f"http://127.0.0.1:{port}/static/loading.html"
    req = urllib.request.urlopen(url)
    assert req.getcode() == 200
    
    # Try sending SIGTERM
    proc.send_signal(signal.SIGINT)
    proc.wait(timeout=5)
    
    assert proc.poll() is not None # Terminated cleanly

def test_single_instance_lock():
    # Acquire lock in parent
    from macos_app import acquire_single_instance_lock
    success = acquire_single_instance_lock()
    assert success is True
    
    # Try running another instance
    proc = subprocess.Popen(
        ['python3', 'macos_app.py'], 
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE,
    )
    proc.wait(timeout=5)
    # The app should exit with code 1 due to single instance lock
    assert proc.returncode == 1
    
    out, err = proc.communicate()
    assert b"already running" in out or b"already running" in err
