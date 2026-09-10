# ERROR_LOG_v1.2_MACOS_SHELL

| Timestamp | Project/Branch | Executor | Check/Action | Expected | Observed | Root Cause | Status/Mitigation |
|---|---|---|---|---|---|---|---|
| 2026-09-10T08:08 | rech-v-tekst (feature/macos-native-shell-v1.2-20260910) | Antigravity (Gemini 3.1 Pro) | Initial PyWebView Window testing | Window shows | PyObjC not installed initially | Missing dependency | Installed `pyobjc` via venv. Solved. |
| 2026-09-10T08:09 | rech-v-tekst | Antigravity (Gemini 3.1 Pro) | Red button click | App remains running | Window destroyed natively | PyWebView default behavior | Implemented `window.events.closing` to `hide()` instead. |
| 2026-09-10T08:09 | rech-v-tekst | Antigravity (Gemini 3.1 Pro) | Dock click handling | Window restores | Nothing happens natively | NSApplication delegate needed | Overrode NSApp delegate with PyObjC `applicationShouldHandleReopen:hasVisibleWindows:`. |
