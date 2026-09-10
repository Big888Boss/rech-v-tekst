import os
import subprocess
import tempfile
import time
import urllib.request
import signal
import re
import sys
import json

def run_packaged_smoke_test():
    print("Building application...")
    subprocess.run(["./build.sh"], check=True)

    app_exe = "dist/Речь в текст.app/Contents/MacOS/macos_app"
    if not os.path.exists(app_exe):
        print(f"Executable not found at {app_exe}")
        sys.exit(1)

    with tempfile.TemporaryDirectory() as temp_out_dir:
        print(f"Using isolated OUT_DIR: {temp_out_dir}")
        env = os.environ.copy()
        env["TEST_MODE"] = "1"
        env["RECH_V_TEKST_OUT_DIR"] = temp_out_dir
        env["RECH_V_TEKST_LOCK_PATH"] = os.path.join(temp_out_dir, "app.lock")

        proc = subprocess.Popen(
            [app_exe],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True
        )

        os.set_blocking(proc.stdout.fileno(), False)

        port = None
        start_time = time.time()

        def reap_process(is_error=False):
            print("Killing process...")
            proc.kill()
            out, err = proc.communicate(timeout=5)
            if is_error:
                print("STDERR: " + err)
            sys.exit(1)

        try:
            while time.time() - start_time < 10:
                line = proc.stdout.readline()
                if line:
                    match = re.search(r'TEST_PORT=(\d+)', line)
                    if match:
                        port = int(match.group(1))
                        break
                else:
                    time.sleep(0.1)

            if not port:
                print("Failed to get TEST_PORT")
                reap_process(is_error=True)

            print(f"Captured TEST_PORT={port}")

            def check_url(path):
                url = f"http://127.0.0.1:{port}{path}"
                req = urllib.request.urlopen(url)
                assert req.getcode() == 200
                print(f"OK: {url}")
                return req.read()

            # Query /
            html_root = check_url("/")
            assert b"html" in html_root.lower()

            # Query exact /static/index.html
            html_static = check_url("/static/index.html")
            assert b"html" in html_static.lower()

            # Query /api/preflight
            preflight_raw = check_url("/api/preflight")
            preflight_data = json.loads(preflight_raw)
            assert "ok" in preflight_data and preflight_data["ok"] is True
            assert "preflight" in preflight_data
            assert isinstance(preflight_data["preflight"], dict)

            # Verify OUT_DIR is outside bundle
            out_dir_path = os.path.abspath(temp_out_dir)
            bundle_path = os.path.abspath("dist/Речь в текст.app")
            assert not out_dir_path.startswith(bundle_path)
            print(f"OUT_DIR {out_dir_path} is correctly outside the bundle.")

        except Exception as e:
            print(f"Test failed with exception: {e}")
            reap_process(is_error=True)

        print("Sending SIGINT to app...")
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=10)
            print(f"Process exited cleanly with code {proc.returncode}")
        except subprocess.TimeoutExpired:
            print("Process did not exit cleanly (Timeout)!")
            reap_process(is_error=True)

        if proc.returncode != 0:
            print(f"Error: Non-zero exit code: {proc.returncode}")
            sys.exit(1)

if __name__ == "__main__":
    run_packaged_smoke_test()
