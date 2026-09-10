import os
import signal
import socket
import sys
import threading
import time
from http.server import ThreadingHTTPServer
import webview

# Add app directory to path
if getattr(sys, 'frozen', False):
    app_dir = sys._MEIPASS
else:
    app_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, app_dir)

from recorder.capture import CAPTURE_MANAGER
from recorder.constants import DEFAULT_HOST
from recorder.http_server import HardenedHTTPHandler
from recorder.lock import GLOBAL_LOCK
from recorder.storage import ensure_private_out_dir
from recorder.transcribe import TRANSCRIBE_MANAGER

def find_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port

def run_server(port):
    ensure_private_out_dir()
    CAPTURE_MANAGER.reconcile_on_startup()
    
    server = ThreadingHTTPServer((DEFAULT_HOST, port), HardenedHTTPHandler)
    server.server_port = port
    
    global http_server
    http_server = server
    server.serve_forever()

def shutdown_server():
    print("Shutting down server...")
    try:
        from recorder.http_server import STATE
        STATE.stop_all_workers(timeout=6.0)
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
    except Exception as e:
        print(f"Teardown error: {e}")
        
    if 'http_server' in globals():
        http_server.shutdown()
        http_server.server_close()

def main():
    port = find_free_port()
    os.environ['UI_PORT'] = str(port)
    
    server_thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    server_thread.start()

    loading_html_path = os.path.join(app_dir, 'app_resources', 'loading.html')
    loading_url = f"file://{loading_html_path}?port={port}"
    
    window = webview.create_window(
        'Речь в текст',
        loading_url,
        width=1000,
        height=800,
        min_size=(800, 600)
    )

    def on_closing():
        print("Window closing (Red button) -> hiding instead.")
        window.hide()
        return False # Prevent window destruction

    window.events.closing += on_closing

    def setup_app_delegate():
        try:
            from AppKit import NSApp, NSObject
            import objc

            class CustomAppDelegate(NSObject):
                def applicationShouldHandleReopen_hasVisibleWindows_(self, app, flag):
                    print("Dock clicked! Restoring window.")
                    if hasattr(self, 'webview_window') and self.webview_window:
                        self.webview_window.show()
                    return True
                    
                def applicationWillTerminate_(self, notification):
                    print("Cmd-Q received. Terminating gracefully.")
                    shutdown_server()
                    os._exit(0)

            delegate = CustomAppDelegate.alloc().init()
            delegate.webview_window = window
            NSApp().setDelegate_(delegate)
            print("macOS App Delegate overridden successfully.")
        except ImportError:
            print("PyObjC not found. Native Dock/Cmd-Q integration skipped.")

    webview.start(setup_app_delegate, debug=False)
    
    # If we somehow exit webview.start normally
    shutdown_server()

if __name__ == '__main__':
    main()
