import os
import sys
import signal
import socket
import subprocess
import time
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

def test_find_free_port():
    from macos_app import find_free_port
    port = find_free_port()
    assert port > 1024
    assert port < 65535

def test_readiness_and_retry_html_exists():
    html_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'app_resources', 'loading.html')
    assert os.path.exists(html_path)
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()
        assert 'Повторить попытку' in content
        assert 'checkServer()' in content

def test_server_startup_and_cleanup():
    # Spin up macos_app in a subprocess to test lifecycle
    proc = subprocess.Popen(
        ['python3', 'macos_app.py'], 
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE,
        env={**os.environ, 'TEST_MODE': '1'}
    )
    time.sleep(3) # Wait for startup
    
    assert proc.poll() is None # Should still be running
    
    # Try sending SIGTERM (Cmd-Q equivalent simulation)
    proc.terminate()
    proc.wait(timeout=5)
    
    assert proc.poll() is not None # Terminated

def test_single_instance_port_binding():
    # Test that if a port is already bound, it won't crash but handle gracefully
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    
    from macos_app import run_server
    import threading
    
    # run_server on an already bound port should raise OSError
    with pytest.raises(OSError):
        run_server(port)
        
    s.close()
