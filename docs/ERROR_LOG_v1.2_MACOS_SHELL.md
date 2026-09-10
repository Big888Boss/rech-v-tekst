# ERROR_LOG_v1.2_MACOS_SHELL

| Timestamp | Project/Branch | Executor | Check/Action | Expected | Observed | Root Cause | Status/Mitigation |
|---|---|---|---|---|---|---|---|
| 2026-09-10T08:08 | rech-v-tekst | Antigravity (Gemini 3.1 Pro) | PyWebView Window testing | Window shows | PyObjC not installed | Missing dependency | Installed `pyobjc` via venv. Solved. |
| 2026-09-10T08:09 | rech-v-tekst | Antigravity (Gemini 3.1 Pro) | Red button click | App running | Window destroyed | PyWebView default | Implemented `window.events.closing` to `hide()`. |
| 2026-09-10T08:09 | rech-v-tekst | Antigravity (Gemini 3.1 Pro) | Dock click | Window restores | Nothing happens natively | NSApplication delegate | Overrode NSApp delegate with PyObjC. |
| 2026-09-10T08:21 | rech-v-tekst | Root Codex Feedback | Check commit size | Only code/docs | `build` and `.venv` committed | Forgotten `.gitignore` | `git rm -r --cached`, added to `.gitignore`. |
| 2026-09-10T08:21 | rech-v-tekst | Root Codex Feedback | Check port logic | Safe binding | Port race condition | `find_free_port` usage | Switched to `ThreadingHTTPServer((..., 0))`. |
| 2026-09-10T08:21 | rech-v-tekst | Root Codex Feedback | Check Single Instance | Exclusive running app | Tested OSError port | Naive implementation | Added `fcntl` exclusive file lock `~/.rech-v-tekst-app.lock`. |
| 2026-09-10T08:23 | rech-v-tekst | Antigravity (Gemini 3.1 Pro) | Capture Screenshots | PNG files generated | `could not create image from display` | Headless CI | Flagged as `NOT VERIFIED` in reports. |
