# ERROR_LOG_v1.2_MACOS_SHELL

| Timestamp | Project/Branch | Executor | Check/Action | Expected | Observed | Root Cause | Status/Mitigation |
|---|---|---|---|---|---|---|---|
| 2026-09-10T08:08 | rech-v-tekst | Antigravity | PyWebView Window testing | Window shows | PyObjC not installed | Missing dependency | Installed `pyobjc` via venv. Solved. |
| 2026-09-10T08:09 | rech-v-tekst | Antigravity | Red button click | App running | Window destroyed | PyWebView default | Implemented `window.events.closing` to `hide()`. |
| 2026-09-10T08:09 | rech-v-tekst | Antigravity | Dock click | Window restores | Nothing happens natively | NSApplication delegate | Overrode NSApp delegate with PyObjC. |
| 2026-09-10T08:21 | rech-v-tekst | Root Codex | Check commit size | Only code/docs | `build` and `.venv` committed | Forgotten `.gitignore` | `git rm -r --cached`, added to `.gitignore`. |
| 2026-09-10T08:21 | rech-v-tekst | Root Codex | Check port logic | Safe binding | Port race condition | `find_free_port` usage | Switched to `ThreadingHTTPServer((..., 0))`. |
| 2026-09-10T08:21 | rech-v-tekst | Root Codex | Check Single Instance | Exclusive running app | Tested OSError port | Naive implementation | Added `fcntl` exclusive file lock `~/.rech-v-tekst-app.lock`. |
| 2026-09-10T08:23 | rech-v-tekst | Antigravity | Capture Screenshots | PNG files generated | `could not create image from display` | Headless CI | Flagged as `NOT VERIFIED` in reports. |
| 2026-09-10T08:30 | rech-v-tekst | Root Codex | test_server_startup | TEST_PORT captured | Test hung/timeout | Buffer block | Added `flush=True`, `PYTHONUNBUFFERED=1`, async read |
| 2026-09-10T08:30 | rech-v-tekst | Root Codex | test_single_instance | Pass in isolated HOME | PermissionError | Hardcoded `~/.rech-v-tekst` | Made lock path configurable via ENV, default to App Support |
| 2026-09-10T08:30 | rech-v-tekst | Root Codex | Cmd-Q active check | Prevent close if working | Only checked capture | Missed transcribe/workers | Added `TRANSCRIBE_MANAGER` and `STATE.active_workers` check |
| 2026-09-10T08:30 | rech-v-tekst | Root Codex | Trailing whitespace | Clean `git diff --check` | Trailing spaces | Bad IDE format | Removed via `sed -i` |
| 2026-09-10T08:34 | rech-v-tekst | Root Codex | Data Loss Recovery | Files are >0 bytes | P0 DATA LOSS | Bad python sed script | Files restored from parent `fa37c0e`, logic reapplied carefully |
| 2026-09-10T08:39 | rech-v-tekst | Root Codex | Startup UX Freeze | Window shows loading | Window didn't appear on error | Sequential startup blocked UI | Migrated to parallel startup with inline Russian loading HTML and robust Retry API |
| 2026-09-10T08:48 | rech-v-tekst | Root Codex | Startup Generation Race | Retry clears stale server | Second duplicate server opened | is_starting boolean | Replaced with monotonic attempt_id/cancel token logic |
| 2026-09-10T08:48 | rech-v-tekst | Root Codex | Startup Exception UI | JS Error in UI | `Unicode escaped` string | json.dumps default ascii | Added `ensure_ascii=False` to json.dumps in UI bridge |
