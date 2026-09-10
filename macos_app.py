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

instance_lock_fd = None

LOADING_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body { font-family: -apple-system, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background: #1e1e1e; color: white; margin: 0; }
        .container { text-align: center; }
        .error { color: #ff5555; display: none; margin-bottom: 20px; max-width: 600px; word-wrap: break-word; }
        .spinner { border: 4px solid rgba(255,255,255,0.1); width: 36px; height: 36px; border-radius: 50%; border-left-color: #09f; animation: spin 1s linear infinite; margin: 20px auto; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        button { padding: 10px 20px; font-size: 16px; cursor: pointer; display: none; background: #09f; color: white; border: none; border-radius: 4px; }
        button:hover { background: #007acc; }
    </style>
</head>
<body>
    <div class="container">
        <div id="loading">Запуск локального сервера...<div class="spinner"></div></div>
        <div id="error" class="error">Ошибка запуска сервера.</div>
        <button id="retryBtn" onclick="pywebview.api.retry()">Повторить попытку</button>
    </div>
    <script>
        function showError(msg) {
            document.getElementById('loading').style.display = 'none';
            document.getElementById('error').style.display = 'block';
            document.getElementById('error').innerText = msg;
            document.getElementById('retryBtn').style.display = 'inline-block';
        }
        function showLoading() {
            document.getElementById('loading').style.display = 'block';
            document.getElementById('error').style.display = 'none';
            document.getElementById('retryBtn').style.display = 'none';
        }
    </script>
</body>
</html>
"""

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

class ServerLauncher:
    def __init__(self, window=None):
        self.window = window
        self.is_starting = False
        self.http_server = None
        self.server_port = None
        self.lock = threading.Lock()
        self._timeout = 10.0
        self._startup_attempts = 0

    def _startup_func(self):
        # Allow tests to mock this safely
        ensure_private_out_dir()
        CAPTURE_MANAGER.reconcile_on_startup()
        server = ThreadingHTTPServer((DEFAULT_HOST, 0), HardenedHTTPHandler)
        return server

    def retry(self):
        self.start_async()

    def start_async(self):
        with self.lock:
            if self.is_starting or self.http_server is not None:
                return False
            self.is_starting = True
            self._startup_attempts += 1

        if self.window:
            self.window.evaluate_js("showLoading()")

        def attempt():
            error_msg = None
            server = None
            try:
                server = self._startup_func()
            except Exception as e:
                error_msg = str(e)

            with self.lock:
                if not self.is_starting:
                    # Watchdog timed out before we finished
                    if server:
                        server.server_close()
                    return

                if error_msg:
                    self.is_starting = False
                    if self.window:
                        safe_msg = error_msg.replace("'", "\\'").replace("\\", "\\\\")
                        self.window.evaluate_js(f"showError('{safe_msg}')")
                else:
                    self.http_server = server
                    self.server_port = server.server_port
                    os.environ['UI_PORT'] = str(self.server_port)
                    self.is_starting = False
                    if self.window:
                        self.window.load_url(f"http://{DEFAULT_HOST}:{self.server_port}/")
                    threading.Thread(target=self.http_server.serve_forever, daemon=True).start()

        startup_thread = threading.Thread(target=attempt, daemon=True)
        startup_thread.start()

        def watchdog():
            startup_thread.join(timeout=self._timeout)
            with self.lock:
                if self.is_starting and startup_thread.is_alive():
                    self.is_starting = False
                    if self.window:
                        self.window.evaluate_js("showError('Таймаут запуска сервера.')")

        threading.Thread(target=watchdog, daemon=True).start()
        return True

    def shutdown(self):
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

        with self.lock:
            if self.http_server:
                self.http_server.shutdown()
                self.http_server.server_close()
                self.http_server = None
                self.server_port = None

        global instance_lock_fd
        if instance_lock_fd is not None:
            try:
                fcntl.flock(instance_lock_fd, fcntl.LOCK_UN)
                os.close(instance_lock_fd)
                instance_lock_fd = None
            except Exception:
                pass


def main():
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

    # We instantiate Api and inject it, but the window needs it.
    class Api:
        def __init__(self):
            self.launcher = None
        def retry(self):
            if self.launcher:
                self.launcher.retry()

    api = Api()

    window = webview.create_window(
        'Речь в текст',
        html=LOADING_HTML,
        js_api=api,
        width=1000,
        height=800,
        min_size=(800, 600)
    )

    launcher = ServerLauncher(window)
    api.launcher = launcher

    def on_closing():
        print("Window closing (Red button) -> hiding instead.")
        window.hide()
        return False

    window.events.closing += on_closing

    def setup_app_delegate():
        try:
            from AppKit import NSApp, NSObject, NSAlert, NSTerminateNow, NSTerminateCancel
            from recorder.http_server import STATE

            class CustomAppDelegate(NSObject):
                def applicationShouldHandleReopen_hasVisibleWindows_(self, app, flag):
                    if hasattr(self, 'webview_window') and self.webview_window:
                        self.webview_window.show()
                    return True

                def applicationShouldTerminate_(self, sender):
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
                        if response == 1001:
                            return NSTerminateCancel

                    if hasattr(self, 'launcher') and self.launcher:
                        self.launcher.shutdown()
                    return NSTerminateNow

            app_delegate = CustomAppDelegate.alloc().init()
            app_delegate.webview_window = window
            app_delegate.launcher = launcher
            NSApp().setDelegate_(app_delegate)
            # Prevent GC
            global _app_delegate_ref
            _app_delegate_ref = app_delegate
        except ImportError:
            pass

        # Start the server immediately after window creation
        launcher.start_async()

    webview.start(setup_app_delegate, debug=False)
    launcher.shutdown()

if __name__ == '__main__':
    if os.environ.get('TEST_MODE') == '1':
        if not acquire_single_instance_lock():
            print("Application is already running.")
            sys.exit(1)
        launcher = ServerLauncher(None)
        launcher.start_async()
        # Wait for port
        start_time = time.time()
        while launcher.server_port is None and time.time() - start_time < 10:
            time.sleep(0.01)
        if launcher.server_port:
            print(f"TEST_PORT={launcher.server_port}", flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            launcher.shutdown()
    else:
        main()
