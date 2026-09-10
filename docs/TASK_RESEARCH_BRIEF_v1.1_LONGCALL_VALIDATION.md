# TASK_RESEARCH_BRIEF — Acceptance & Endurance Validation: Long-Call Diarization (>= 2h)

Canonical Policy ID: `multi-agent-governance-2026-09-09.2`
Project: `rech-v-tekst`
Milestone: Rigorous Acceptance and Endurance Validation of Offline Speaker Diarization for Long Calls (>= 2:00:00)
Base Commit: `92a6c7493ca6e7776215a2ad39baeeb9a2621f30` (tag `v1.1` on `Big888Boss/rech-v-tekst`)
Isolated Worktree: `/Users/kuznetcovpavel/max/rech-v-tekst-longcall-validation-v1_1-20260910`
Branch: `validation/v1.1-long-call-20260910`
Timestamp: 2026-09-10T05:55:00-04:00
Executor: Antigravity / Gemini 3.8 Flash (Session `85d4dc81-6bc3-4537-9b78-9ac3f5b42b1e`)
Product Owner / Coordinator / Reviewer: Root Codex (strictly prohibited from product implementation)

---

## 1. Intended User-Visible Outcome and Measurable Acceptance Criteria

### User-Visible Outcome
Comprehensive, empirically proven stability, accuracy, and durability of the published `v1.1` release for continuous long-call recordings and uploads (duration >= 2:00:00 / 7200 seconds) with multi-speaker diarization (`sherpa-onnx`). The system must demonstrate:
- Bounded memory footprint (peak RSS strictly < 4.0 GB target; steady window-to-window memory profile with zero unbounded leaks).
- Uncompromised pipeline execution (Whisper transcription + Sherpa-ONNX diarization + ERes2Net centroid tracking).
- Clean interruption/cancellation and loss-free checkpoint resumption.
- Speaker count stability (auto-clustering detecting the exact speaker population).
- Reliable post-processing: speaker renaming, instant UI badge synchronization, and flawless export generation across all 5 formats (TXT, Markdown, SRT, VTT, JSON).
- Reversible queue management (removal to `'removed'` view and restoration).
- Complete visual evidence captured via automated browser/UI test runs.

### Acceptance Criteria
1. **Long-Call Audio Test Corpus (>= 2:00:00)**:
   - Reproducible synthetic audio call with >= 7200s total duration.
   - 3 distinct voices with known turn order, realistic pause intervals (0.5s–2.0s), and cross-talk/overlapping speech periods.
   - Formal `work/ground_truth_turns.json` recording ground truth turn boundaries and speaker labels.
   - Exact file duration, sample rate, format (16kHz 16-bit mono WAV), and SHA-256 logged.
2. **Real Unmocked Pipeline Execution**:
   - Run the production `v1.1` transcription + diarization pipeline end-to-end on the >= 2-hour recording using pinned `sherpa-onnx` and `ggml-large-v3-turbo.bin` / `whisper-cli`.
   - Process through sliding windowing (600s window, 60s overlap; ~14 windows for 7200s audio).
3. **Resource Profile & Endurance Monitoring**:
   - Continuous sampling of RSS, CPU, and per-window processing duration.
   - Peak RSS must remain safely below 4.0 GB (target < 2.0 GB).
   - Window-to-window memory delta must level off without continuous monotonic growth.
4. **Checkpoint Cancellation & Resumption Verification**:
   - At window >= 2, issue clean cancel (`POST /api/session/diarize/cancel`).
   - Verify active subprocess termination and preservation of already transcribed segments and completed window checkpoints.
   - Trigger resume (`POST /api/session/diarize`), verify processing picks up at the checkpoint without duplicate segments or lost turns.
5. **Diarization Clustering & DER / Speaker Metric**:
   - Evaluate auto-clustering speaker identification against `work/ground_truth_turns.json`.
   - Measure speaker purity, turn boundary alignment, and speaker error metrics.
6. **Speaker Renaming & Monotonic Export Integrity**:
   - Rename 3 detected speakers via API/UI.
   - Verify instantaneous update across manifest and UI elements.
   - Generate and download all 5 export formats (TXT, MD, SRT, VTT, JSON).
   - Validate timestamp monotonicity, non-empty text, valid speaker labels, and valid JSON schema.
7. **Queue Management Verification**:
   - Remove session from active queue (`POST /api/session/queue/remove`), verify `in_queue: false` and retention in removed filter.
   - Restore session (`POST /api/session/queue/restore`), verify `in_queue: true` and raw audio/manifest integrity.
8. **Regression & Diff Integrity**:
   - Full test suite passing (`python3 -m unittest discover -s tests`).
   - Browser E2E flow verified with Playwright / Chrome.
   - `git diff --check` passes cleanly with zero trailing whitespace or merge conflict markers.
   - Worktree remains clean on `validation/v1.1-long-call-20260910`, without pushing or modifying remote repository.

---

## 2. Verified Current State of Worktree and Runtime

- **Worktree Path**: `/Users/kuznetcovpavel/max/rech-v-tekst-longcall-validation-v1_1-20260910`
- **Current Branch**: `validation/v1.1-long-call-20260910` (branched from tag `v1.1`, commit `92a6c74`)
- **Git Status**: Clean. `work/` and `models/` ignored by `.gitignore`.
- **Runtime Environment**:
  - Python 3.9.6 (Apple Silicon arm64).
  - Audio tools: `/usr/bin/afconvert`, macOS `say` TTS engine.
  - Browser testing: Google Chrome `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` with Playwright Node runner via `tests/test_diarization_browser_flow.py`.
- **Cached Binaries and Models**:
  - `work/bin/sherpa-onnx-offline-speaker-diarization` (SHA-256 verified)
  - `work/lib/libsherpa-onnx-c-api.dylib` (SHA-256 verified)
  - `work/bin/whisper-cli`
  - `models/ggml-large-v3-turbo.bin`
  - `models/diarization/sherpa-onnx-pyannote-segmentation-3-0/model.int8.onnx`
  - `models/diarization/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx`
  - `recorder.installer.get_diarization_install_status()`: `ready: True`.

---

## 3. Implementation Options and Technical Decisions

### Generation of >= 2-Hour Audio Corpus
- **Option A (Selected)**: Programmatic synthesis of interleaved dialogue using distinct acoustic/TTS voice identities via macOS `say` and Python `wave`.
  - Voices: Daniel (Voice 1, British male), Fred (Voice 2, US male robotic/distinct pitch), Milena (Voice 3, Russian female).
  - Dialogue structure: Continuous turns (3–15 seconds each) representing a structured team meeting, with interspersed realistic silence gaps (0.5s–2.0s) and controlled cross-talk / overlapping speech periods (simultaneous speech for 5–10s).
  - Length: Exactly 7200.0 seconds (2 hours, 0 minutes, 0 seconds) consisting of ~700 turns.
  - Accurate ground truth: Saved in real-time during generation to `work/ground_truth_turns.json`.
  - Format: Standard 16kHz 16-bit mono WAV, directly ingestible by Whisper and Sherpa-ONNX without transcoding artifacts.
- **Option B (Rejected)**: Looping a 3-minute clip 40 times.
  - Flaw: Unrealistic acoustic continuity, introduces repetitive artifact loops, doesn't test real conversational turn dynamics across hours.

### Long-Call Endurance Execution Strategy
- Run the full pipeline via dedicated test script `work/run_longcall_validation.py`.
- Collect high-frequency OS metrics (RSS memory in bytes, CPU percentage) after each window completion.
- Verify checkpoint persistence in `sessions/<session_id>/diarization_checkpoint.json`.
- Execute cancel after window 2, verify status is `diarization_status="cancelled"`, then resume from window 2.
- Verify final combined manifest and generate all export artifacts.

---

## 4. File and Artifact Ownership Map

- `docs/TASK_RESEARCH_BRIEF_v1.1_LONGCALL_VALIDATION.md`
- `docs/MODEL_ROUTING_PLAN_v1.1_LONGCALL_VALIDATION.md`
- `docs/QUALITY_REPORT_v1.1_LONGCALL_VALIDATION.md`
- `docs/COMPLETION_REPORT_v1.1_LONGCALL_VALIDATION.md`
- `docs/ERROR_LOG_v1.1_LONGCALL_VALIDATION.md`
- `work/generate_longcall_corpus.py` (ignored)
- `work/longcall_2h.wav` (ignored, >= 7200s audio)
- `work/ground_truth_turns.json` (ignored)
- `work/run_longcall_validation.py` (ignored)
- `work/longcall_summary.json` (ignored)
- `work/longcall_metrics.json` (ignored)

---

## 5. Verification, Rollback, and Evidence Plan

1. **Safety & Zero Disruption**: All validation work is performed strictly on `validation/v1.1-long-call-20260910` in worktree `/Users/kuznetcovpavel/max/rech-v-tekst-longcall-validation-v1_1-20260910`. The published `v1.1` release tag and `main` branch remain untouched.
2. **Metrics & Artifacts**: Peak RSS, CPU, wall-clock duration, window metrics, export checksums, and Playwright browser screenshots recorded in `work/longcall_summary.json` and referenced in reports.
3. **Rollback**: If any failure occurs, the worktree can be reset cleanly (`git checkout -- .`) without remote impact.
