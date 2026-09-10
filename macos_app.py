import os
import signal
import socket
import sys
import threading
import time
import fcntl
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

# Global references
http_server = None
server_port = None
app_delegate = None
instance_lock_fd = None

def acquire_single_instance_lock():
    global instance_lock_fd
    lock_dir = os.path.expanduser("~/Library/Application Support/rech-v-tekst")
    os.makedirs(lock_dir, exist_ok=True)
    default_lock = os.path.join(lock_dir, "app.lock")
    lock_file = os.environ.get("RECH_V_TEKST_LOCK_PATH", default_lock)

    try:
        instance_lock_fd = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(instance_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except IOError:
        return False

def run_server():
    global http_server, server_port
    ensure_private_out_dir()
    CAPTURE_MANAGER.reconcile_on_startup()

    # Bind to port 0 to avoid race conditions
    server = ThreadingHTTPServer((DEFAULT_HOST, 0), HardenedHTTPHandler)
    server_port = server.server_port
    os.environ['UI_PORT'] = str(server_port)
    http_server = server
    server.serve_forever()

def shutdown_server():
    print("Shutting down server gracefully...")
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

    if http_server:
        http_server.shutdown()
        http_server.server_close()

    global instance_lock_fd
    if instance_lock_fd is not None:
        try:
            fcntl.flock(instance_lock_fd, fcntl.LOCK_UN)
            os.close(instance_lock_fd)
            instance_lock_fd = None
        except Exception:
            pass

def main():
    global app_delegate, server_port

    if not acquire_single_instance_lock():
        print("Application is already running.")
        try:
            from AppKit import NSAlert, NSApplication
            alert = NSAlert.alloc().init()
            alert.setMessageText_("Приложение уже запущено")
            alert.setInformativeText_("Один экземпляр «Речь в текст» уже работает.")
            alert.runModal()
        except ImportError:
            pass
        sys.exit(1)

    # Start the local server
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # Wait for the OS to assign the port
    while server_port is None:
        time.sleep(0.01)

    loading_url = f"http://{DEFAULT_HOST}:{server_port}/static/loading.html"

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
        global app_delegate
        try:
            from AppKit import NSApp, NSObject, NSAlert, NSTerminateNow, NSTerminateCancel
            import objc
            from recorder.http_server import STATE

            class CustomAppDelegate(NSObject):
                def applicationShouldHandleReopen_hasVisibleWindows_(self, app, flag):
                    print("Dock clicked! Restoring window.")
                    if hasattr(self, 'webview_window') and self.webview_window:
                        self.webview_window.show()
                    return True

                def applicationShouldTerminate_(self, sender):
                    print("Cmd-Q received.")
                    is_busy = (
                        CAPTURE_MANAGER.is_active() or
                        TRANSCRIBE_MANAGER.progress.get("is_running", False) or
                        len(STATE.active_workers) > 0
                    )

                    if is_busy:
                        alert = NSAlert.alloc().init()
                        alert.setMessageText_("Прервать активную работу?")
                        alert.setInformativeText_("В данный момент выполняется запись, обработка или импорт аудио. Если вы закроете приложение, процесс будет завершен.")
                        alert.addButtonWithTitle_("Закрыть и сохранить")
                        alert.addButtonWithTitle_("Отмена")
                        response = alert.runModal()
                        if response == 1001: # Cancel button
                            return NSTerminateCancel

                    shutdown_server()
                    return NSTerminateNow

            app_delegate = CustomAppDelegate.alloc().init()
            app_delegate.webview_window = window
            NSApp().setDelegate_(app_delegate)
            print("macOS App Delegate overridden successfully.")
        except ImportError:
            print("PyObjC not found. Native Dock/Cmd-Q integration skipped.")

    webview.start(setup_app_delegate, debug=False)

    # If we somehow exit webview.start normally
    shutdown_server()

if __name__ == '__main__':
    # When running in test mode, do not block
    if os.environ.get('TEST_MODE') == '1':
        if not acquire_single_instance_lock():
            print("Application is already running.")
            sys.exit(1)
        # Start server but don't open GUI
        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()
        while server_port is None:
            time.sleep(0.01)
        # Inform the test of the assigned port
        print(f"TEST_PORT={server_port}", flush=True)
        # Wait for termination
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            shutdown_server()
    else:
        main()
