import os
import socket
import sys
import threading
import webview
import time

try:
    from AppKit import NSApp, NSObject, NSApplication
    import objc

    class CustomAppDelegate(NSObject):
        def applicationShouldHandleReopen_hasVisibleWindows_(self, app, flag):
            # When dock icon is clicked
            print("Dock clicked! Restoring window.")
            # Unhide the window
            if hasattr(self, 'webview_window') and self.webview_window:
                self.webview_window.show()
            return True
            
        def applicationWillTerminate_(self, notification):
            print("Cmd-Q received. Terminating.")
            os._exit(0)

except ImportError:
    pass

def test_pywebview():
    port = 8787
    
    window = webview.create_window(
        'Test',
        html='<h1>Test</h1>',
        width=800,
        height=600,
    )
    
    def on_closing():
        print("Closing window... Hiding instead.")
        window.hide()
        return False # Cancel close

    window.events.closing += on_closing

    def setup_app_delegate():
        if 'NSApp' in globals():
            delegate = CustomAppDelegate.alloc().init()
            delegate.webview_window = window
            NSApp().setDelegate_(delegate)
            print("Set custom app delegate")

    webview.start(setup_app_delegate, debug=False)

if __name__ == '__main__':
    test_pywebview()
