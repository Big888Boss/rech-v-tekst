# ERROR_LOG — Release Candidate v1.1 (Append-Only)

Canonical Policy ID: `multi-agent-governance-2026-09-09.2`
Project: `rech-v-tekst`
Milestone: Complete release candidate v1.1 for long-call transcription with offline speaker diarization
Canonical Repository: `/Users/kuznetcovpavel/max/rech-v-tekst-diarization-v1_1-20260909-c1c2decc`
Branch: `feature/rech-v-tekst-diarization-v1.1-20260909-c1c2decc`

---

### ERR-001: StorageError in Integration Test `test_end_to_end_on_real_audio_if_available`
- **Timestamp**: 2026-09-09T22:24:34-04:00
- **Project**: `rech-v-tekst` (Milestone v1.1 RC)
- **Branch / Commit**: `feature/rech-v-tekst-diarization-v1.1-20260909-c1c2decc` @ `dfd8f597f` + uncommitted v1.1 work
- **Executor**: Antigravity / Gemini 3.8 Flash
- **Command / Trigger**: `python3 -m unittest discover -s tests -p "test_diarization*.py"`
- **Expected Result**: Integration test runs diarizer on sample fixture `0-four-speakers-zh.wav` and asserts multi-speaker turns and checkpoint.
- **Observed Result**:
  ```text
  recorder.storage.StorageError: Session test_diar_session not found
  ```
  Traceback at `tests/test_diarization_pipeline.py:122` in `run_session_diarization`.
- **Root Cause**: `TestDiarizationPipeline` instantiated a temp directory `self.test_dir = Path(tempfile.mkdtemp())` and patched `get_session_dir`, but did NOT set `set_data_root(self.test_dir)`. When `load_session` invoked `safe_read_text(manifest_path)`, `verify_path_components_safe` rejected the path because it was outside the unconfigured default `OUT_DIR`.
- **Severity**: High (blocked integration test verification).
- **Issue Nature**: Pre-existing test defect from prior partial implementation.
- **Owner**: Antigravity
- **Fix / Mitigation**: Update `TestDiarizationPipeline` to invoke `set_data_root(self.test_dir)` and register restoration cleanup in `setUp`/`tearDown`, matching `IsolatedTestCase`.
- **Changed Files**: `tests/test_diarization_pipeline.py`
- **Retest Evidence**: `tests/test_diarization_pipeline.py` ran with real sherpa-onnx inference and passed cleanly in 11.18s.
- **Status**: RESOLVED
- **Quota Consumed**: 0 API tokens (local investigation).

---

### ERR-002: Trailing Whitespace and EOF Blank Line Defects
- **Timestamp**: 2026-09-09T22:24:38-04:00
- **Project**: `rech-v-tekst`
- **Branch**: `feature/rech-v-tekst-diarization-v1.1-20260909-c1c2decc`
- **Executor**: Antigravity / Gemini 3.8 Flash
- **Command / Trigger**: `git diff --check`
- **Expected Result**: Exit code 0, no whitespace errors.
- **Observed Result**:
  ```text
  recorder/export.py:51: trailing whitespace.
  recorder/export.py:55: trailing whitespace.
  recorder/export.py:142: trailing whitespace.
  static/tokens.css:108: new blank line at EOF.
  ```
- **Root Cause**: Uncleaned formatting edits in `export.py` and `tokens.css` from prior partial implementation.
- **Severity**: Low (style/formatting compliance).
- **Issue Nature**: Pre-existing defect.
- **Owner**: Antigravity
- **Fix / Mitigation**: Strip trailing whitespace on lines 51, 55, 142 in `recorder/export.py`; trim extra newline at EOF in `static/tokens.css`.
- **Changed Files**: `recorder/export.py`, `static/tokens.css`
- **Retest Evidence**: `git diff --check` passed with exit code 0.
- **Status**: RESOLVED

---

### ERR-003: Contract Mismatch: `seg.speaker` vs `seg.speaker_id`
- **Timestamp**: 2026-09-09T22:26:15-04:00
- **Project**: `rech-v-tekst`
- **Executor**: Antigravity / Gemini 3.8 Flash
- **Trigger**: Static codebase contract inspection.
- **Expected Result**: UI and backend agree on property names for segment speaker identifiers.
- **Observed Result**: `recorder/diarization_merge.py` and `recorder/export.py` populate and check `seg["speaker_id"]`, while `static/app.js` (lines 1198, 1288, 1292, 1298) checked `seg.speaker`.
- **Root Cause**: Disconnected frontend and backend implementations in prior uncommitted draft.
- **Severity**: High (causes speaker badges and names to fail to render in UI transcript entries).
- **Issue Nature**: Pre-existing defect.
- **Owner**: Antigravity
- **Fix / Mitigation**:
  1. In `recorder/diarization_merge.py`, attach both `speaker_id` and backwards-compatible `speaker` alias to every segment.
  2. In `static/app.js`, read `seg.speaker_id || seg.speaker`.
- **Changed Files**: `recorder/diarization_merge.py`, `static/app.js`
- **Retest Evidence**: `tests/test_diarization_merge.py` and unit suite passed (22 tests OK).
- **Status**: RESOLVED

---

### ERR-004: Contract Mismatch: `manifest.speakers` vs `diarization.speakers`
- **Timestamp**: 2026-09-09T22:26:24-04:00
- **Project**: `rech-v-tekst`
- **Executor**: Antigravity / Gemini 3.8 Flash
- **Trigger**: Static codebase inspection of speaker metadata dictionaries.
- **Expected Result**: Speaker rename dictionary accessible whether read from `manifest.speakers` or `transcript.json["diarization"]["speakers"]`.
- **Observed Result**: In `static/app.js`, `renderSpeakerLegend` only read `manifest.speakers`; if manifest was loaded before diarization or if only transcript.json was updated, legend was empty.
- **Root Cause**: Dual storage locations without full synchronization across endpoints.
- **Severity**: Medium (potential loss of custom speaker names in UI view).
- **Issue Nature**: Pre-existing defect.
- **Owner**: Antigravity
- **Fix / Mitigation**:
  1. In `static/app.js`, read `manifest?.speakers || diarization?.speakers || {}`.
  2. In `recorder/export.py`, ensure `update_speaker_names` atomically writes both `manifest.speakers` and `payload["diarization"]["speakers"]`.
  3. In `recorder/diarizer.py`, populate both `manifest.speakers` and `transcript.json["diarization"]["speakers"]`.
- **Changed Files**: `recorder/export.py`, `recorder/diarizer.py`, `static/app.js`
- **Retest Evidence**: `tests/test_diarization_exports.py` passed (5 tests OK).
- **Status**: RESOLVED

---

### ERR-005: External Session Interruption and Checkpoint Resumption
- **Timestamp**: 2026-09-09T22:45:00-04:00
- **Project**: `rech-v-tekst` (Milestone v1.1 RC)
- **Branch**: `feature/rech-v-tekst-diarization-v1.1-20260909-c1c2decc`
- **Executor**: Antigravity / Gemini 3.8 Flash
- **Trigger**: External cancellation of background task execution.
- **Expected Result**: Continuous uninterrupted task progression.
- **Observed Result**: Session interrupted after preserving 19 changed files (+1161/-35); workspace preserved intact with dirty worktree.
- **Root Cause**: Execution timeout/interruption event during browser E2E debugging.
- **Severity**: Medium (procedural interruption; no data loss).
- **Issue Nature**: Environment / supervisor interruption.
- **Owner**: Antigravity
- **Fix / Mitigation**: Resumed immediately from preserved checkpoint in canonical repository `/Users/kuznetcovpavel/max/rech-v-tekst-diarization-v1_1-20260909-c1c2decc`.
- **Changed Files**: N/A
- **Status**: RESOLVED

---

### ERR-006: `#btnCancelDiarization` Visibility and Export Check Failure in Browser Flow Test
- **Timestamp**: 2026-09-09T22:46:50-04:00
- **Project**: `rech-v-tekst`
- **Executor**: Antigravity / Gemini 3.8 Flash
- **Command / Trigger**: `python3 -m unittest tests/test_diarization_browser_flow.py`
- **Expected Result**: Clicking `#btnDiarizeSession` initiates diarization and reveals `#btnCancelDiarization`; all 5 export downloads match expected content.
- **Observed Result**:
  1. Playwright timed out waiting for `#btnCancelDiarization:not([hidden])` because transcription had not fully completed before inspect button was clicked (the inspect button was present for all rows).
  2. Waiting for hidden state on `<button hidden>` requires Playwright `{ state: 'hidden' }`.
  3. SRT export format contains `Speaker: Text` without square brackets.
  4. JSON export speaker format contains `{"display_name": "Speaker"}` dictionary.
- **Root Cause**:
  1. Test script waited for inspect button which was statically present, rather than waiting for session status to transition to `completed`.
  2. Playwright selector default visibility expectations.
  3. Format expectation adjustments to match real backend contracts.
- **Severity**: Medium (test harness synchronization & assertions).
- **Issue Nature**: Test automation synchronization defect.
- **Owner**: Antigravity
- **Fix / Mitigation**:
  1. In `tests/qa_diarization_flow.js`, wait for `tr[data-session-id] span[data-status="completed"]` before inspecting session.
  2. Use `{ state: 'hidden' }` for modal and button hidden states.
  3. Corrected SRT and JSON speaker format assertions to match canonical implementation.
- **Changed Files**: `tests/qa_diarization_flow.js`
- **Retest Evidence**: `python3 -m unittest tests/test_diarization_browser_flow.py` passed with exit code 0 in 16.64s.
- **Status**: RESOLVED
---

### ERR-007: Tooltip Dismissal on Page Hover Scroll in E2E Layout Test
- **Timestamp**: 2026-09-09T22:55:02-04:00
- **Project**: `rech-v-tekst`
- **Executor**: Antigravity / Gemini 3.8 Flash
- **Command / Trigger**: `python3 -m unittest tests/test_layout_and_tooltips_e2e.py`
- **Expected Result**: Hovering `#btnUploadRetry` preserves `uploadRetryNote` in `aria-describedby` and adds `appGlobalTooltip`.
- **Observed Result**:
  ```text
  QA TEST FAILED: Expected both uploadRetryNote and appGlobalTooltip, got "uploadRetryNote"
  ```
- **Root Cause**: Playwright's `uploadRetryBtn.hover()` scrolls the element into view if needed. In `static/app.js`, the window `scroll` listener unconditionally invoked `hideTooltip()`, immediately dismissing the active tooltip and stripping `appGlobalTooltip` from `aria-describedby`.
- **Severity**: Low (synthetic browser automation edge case and user UX on scroll).
- **Issue Nature**: Frontend interaction regression in tooltip handling.
- **Owner**: Antigravity
- **Fix / Mitigation**:
  1. In `static/app.js`, updated the window `scroll` event listener to dynamically recalculate the tooltip's floating position relative to the viewport instead of unconditionally closing it. If the target is scrolled entirely out of the viewport (`rect.bottom < 0 || rect.top > window.innerHeight`), `hideTooltip()` is called.
  2. Bound `pointerover` and `pointerout` alongside `pointerenter` and `pointerleave` for event capture.
- **Changed Files**: `static/app.js`
- **Retest Evidence**: Both `tests/test_layout_and_tooltips_e2e.py` and the full suite `python3 -m unittest discover -s tests -p "test_*.py"` (163 tests) passed cleanly with exit code 0.
- **Status**: RESOLVED

### ERR-008: Missing Remote Component Distribution and Incomplete Installer in Clean Clones
- **Timestamp**: 2026-09-10T05:15:00-04:00
- **Project**: `rech-v-tekst`
- **Executor**: Antigravity / Gemini 3.8 Flash
- **Command / Trigger**: Acceptance review by Root Codex; clean clone verification on fresh worktree without gitignored `work/diarization-spike`.
- **Expected Result**: On clean clone, `./setup.sh --install` and Settings UI install both Whisper and Diarization from official remote releases; `tests/test_fresh_install.py` validates end-to-end setup without `work/diarization-spike`.
- **Observed Result**:
  1. `recorder/installer.py::install_diarization_components` only copied sherpa-onnx, libraries, and models from `work/diarization-spike`, which is excluded from git. On a clean clone without `work/diarization-spike`, installation failed.
  2. `setup.sh --install` only installed Whisper components without initiating or reporting Diarization component readiness.
  3. `PINNED_SHERPA_ARM64_SHARED_LIB_SHA256` had a placeholder hash rather than the verified SHA-256 for the arm64 shared library archive.
- **Root Cause**: Diarization component installer was initially implemented referencing the local spike prototype directory instead of streaming official remote GitHub release tarballs and ONNX models.
- **Severity**: High / Blocker for clean clone deployment.
- **Issue Nature**: Installer completeness and clean-root reproducibility defect.
- **Owner**: Antigravity
- **Fix / Mitigation**:
  1. Updated `PINNED_SHERPA_ARM64_SHARED_LIB_SHA256` to verified `c51e220217f2ce5d3de211887dc13ad49bf22346a2025a4159591d892008242d`.
  2. Implemented robust streaming `_download_file` in `recorder/installer.py` with finite timeout (600s), `.part` staging with `O_CREAT | O_EXCL | O_NOFOLLOW`, SHA-256 integrity verification, and cleanup on failure.
  3. Implemented safe tar extraction `_safe_extract_tar_members` with path traversal defense (`..` rejection, absolute path rejection, extraction into atomic staging).
  4. Updated `install_diarization_components` to install static binaries, shared libraries, pyannote segmentation model, and 3dspeaker eres2net embedding model from remote URLs, with atomic replacement and correct file permissions (`0o755` for bin/libs, `0o644` for models), while preserving `work/diarization-spike` as an optional local cache.
  5. Updated `InstallerManager._run_install_worker` to automatically install and verify diarization components on `arm64`, and updated `REQUIRED_DISK_BYTES` to 4.0 GB.
  6. Updated `setup.sh` to audit and report diarization readiness in both `--check` and `--install` modes.
  7. Created comprehensive isolated tests in `tests/test_fresh_install.py` validating path traversal rejection, SHA mismatch rejection, synthetic fresh-clone archive extraction, permissions, and idempotency.
- **Changed Files**:
  - `recorder/constants.py`
  - `recorder/installer.py`
  - `setup.sh`
  - `tests/test_fresh_install.py`
  - `docs/ERROR_LOG_v1.1.md`
- **Retest Evidence**: `python3 -m unittest tests/test_fresh_install.py` passed with 4/4 tests OK; `./setup.sh --check` correctly reports diarization component status.
- **Status**: RESOLVED
