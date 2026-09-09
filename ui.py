#!/usr/bin/env python3
"""Webinar Recorder Web UI server entrypoint."""
from __future__ import annotations

import os
import signal
import sys
from http.server import ThreadingHTTPServer

from recorder.capture import CAPTURE_MANAGER
from recorder.constants import DEFAULT_HOST, DEFAULT_PORT
from recorder.http_server import HardenedHTTPHandler
from recorder.lock import GLOBAL_LOCK
from recorder.storage import ensure_private_out_dir
from recorder.transcribe import TRANSCRIBE_MANAGER


def main() -> None:
    # Ensure private directory exists and reconcile any crash tails
    ensure_private_out_dir()
    reconciled = CAPTURE_MANAGER.reconcile_on_startup()
    if reconciled:
        print(f"Reconciled interrupted sessions from prior run: {', '.join(reconciled)}")

    port = int(os.environ.get("UI_PORT", str(DEFAULT_PORT)))
    host = DEFAULT_HOST

    server = ThreadingHTTPServer((host, port), HardenedHTTPHandler)
    server.server_port = port
    print(f"=== Webinar Recorder UI ===")
    print(f"URL: http://{host}:{port}")
    print("Press Ctrl+C to stop.")

    def handle_signal(sig, frame):
        print("\nShutting down server...")
        try:
            from recorder.http_server import STATE
            STATE.stop_all_workers(timeout=6.0)
            # Verify workers have actually finished before releasing lock
            workers_stopped = (
                not CAPTURE_MANAGER.is_active()
                and not TRANSCRIBE_MANAGER.progress.get("is_running", False)
                and len(STATE.active_workers) == 0
            )
            if workers_stopped:
                try:
                    GLOBAL_LOCK.release()
                except Exception:
                    pass
            else:
                print("Warning: some workers did not terminate cleanly; lock retained for safety.")
        except Exception as e:
            print(f"Teardown note: {e}")
        finally:
            try:
                server.server_close()
            except Exception:
                pass
            sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        handle_signal(signal.SIGINT, None)


if __name__ == "__main__":
    main()
