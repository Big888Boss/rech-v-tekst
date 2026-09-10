# TASK_RESEARCH_BRIEF — Release Candidate v1.1: Long-Call Diarization

Canonical Policy ID: `multi-agent-governance-2026-09-09.2`
Project: `rech-v-tekst`
Milestone: Complete release candidate v1.1 for long-call transcription with offline speaker diarization
Canonical Repository: `/Users/kuznetcovpavel/max/rech-v-tekst-diarization-v1_1-20260909-c1c2decc`
Branch: `feature/rech-v-tekst-diarization-v1.1-20260909-c1c2decc`
Timestamp: 2026-09-09T22:30:00-04:00
Executor: Antigravity / Gemini 3.8 Flash
Product Owner / Coordinator / Reviewer: Root Codex (strictly prohibited from product implementation)

---

## 1. Intended User-Visible Outcome and Measurable Acceptance Criteria

### User-Visible Outcome
A reliable, completely offline, zero-telemetry macOS desktop solution for recording or uploading multi-hour audio/video files (tested up to 8+ hours), producing high-accuracy Whisper transcription combined with automated speaker diarization (`sherpa-onnx`). The user can see distinct speakers with unique color badges, rename speakers seamlessly, navigate transcripts with instant in-memory search, inspect progress, safely cancel and retry operations, and export diarized transcripts in all 5 formats (TXT, Markdown, SRT, VTT, JSON).

### Acceptance Criteria
1. **Upload Visibility & Handling**: Prominent audio/video upload controls on the initial screen; supported media files can be selected, queued, and processed, with clear Russian error messages if prerequisites or files fail.
2. **Safe Queue Deletion**: Any queued recording can be deleted safely from the queue table without deleting preserved session audio files or corrupting state.
3. **8+ Hour Long-Call Processing**: Incremental processing in bounded sliding windows (600s window, 60s overlap) without loading the entire 8+ hour file into memory; durable checkpoints allow resuming after interruption.
4. **Offline Speaker Diarization**: Uses pinned `sherpa-onnx` (Pyannote segmentation 3.0 + ERes2Net embedding) on Apple Silicon; supports Auto speaker detection and manual 2–20 speakers; stable IDs, distinct color badges, and rename propagation.
5. **Robust Lifecycle**: Cancel cleanly terminates active subprocesses; retry clears failed diarization without corrupting Whisper transcript; resume continues from the last valid checkpoint.
6. **Data Contract Reconciliation**: Backend, manifest, API, UI, and exports strictly agree on `speaker_id`, `speaker` alias, and speaker metadata dictionaries (`manifest.speakers` and `transcript.json["diarization"]["speakers"]`).
7. **Complete Diarized Exports**: TXT, MD, SRT, VTT, and JSON all incorporate resolved speaker names, timestamps, and metadata.
8. **Component Validation & Installer Security**: Status check verifies all local binaries and models against pinned SHA-256 hashes; Apple Silicon arm64 supported; Intel x86_64 cleanly fail-closed with actionable Russian explanations.
9. **Zero Leakage & Local Offline Processing**: No external network requests; all operations confined to local sandboxed directories with symlink and traversal protections.

---

## 2. Verified Current State of Repository and Runtime

- **Repository Root**: `/Users/kuznetcovpavel/max/rech-v-tekst-diarization-v1_1-20260909-c1c2decc`
- **Branch**: `feature/rech-v-tekst-diarization-v1.1-20260909-c1c2decc` (matches required branch)
- **Local Components**:
  - `work/bin/sherpa-onnx-offline-speaker-diarization` (SHA-256 verified)
  - `work/lib/libsherpa-onnx-c-api.dylib` (SHA-256 verified)
  - `models/diarization/sherpa-onnx-pyannote-segmentation-3-0/model.int8.onnx` (SHA-256 verified)
  - `models/diarization/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx` (SHA-256 verified)
  - `verify_diarizer_components()` reports `ready: True`.
- **Baseline Test Suite**:
  - Ran 140+ unit/integration tests: All passed except 1 known defect:
    `tests/test_diarization_pipeline.py::test_end_to_end_on_real_audio_if_available` failing with `StorageError: Session test_diar_session not found`.
  - Cause diagnosed: Test did not isolate data root via `set_data_root(test_dir)`, causing storage safety check to reject path as outside root.
- **Diff Check**:
  - 4 whitespace defects detected by `git diff --check` in `recorder/export.py` and `static/tokens.css`.

---

## 3. Implementation Options and Tradeoff Analysis

### Option A (Selected): Native Incremental Windowing with In-Process Centroid Registry and Unified Data Contracts
- **Description**: Fix test isolation with `set_data_root`; clean whitespace defects; unify `speaker_id` and `speaker` alias across UI and merge engine; sync `manifest.speakers` and `diarization.speakers`; enhance initial screen UI to make file upload prominently visible alongside mic/system sources; expand test suite with simulated 8h virtual timeline, recovery, and API tests; execute browser-based verification.
- **Correctness**: 100% compliant with ADR and v1.1.0 specifications.
- **Risk**: Very low; builds directly upon existing tested foundation.
- **Reversibility**: High; all edits on dedicated feature branch.
- **Quota Impact**: Minimal local compute; zero API LLM calls needed.

### Option B: Pyannote.audio Community-1 via PyTorch
- **Description**: Replace sherpa-onnx with PyTorch pyannote pipeline.
- **Rejection Reason**: Requires Hugging Face authentication token, downloads hundreds of megabytes of PyTorch dependencies, violates offline zero-token acceptance criteria.

---

## 4. Dependencies, Security, and Production Risks

- **Dependencies**: Python 3.9+ standard library, `sherpa-onnx` arm64 binary + C-API dylib, `onnxruntime`, ffmpeg/ffprobe.
- **Security Protections**: Enforce `is_safe_regular_file`, `verify_path_components_safe`, path traversal prevention, zero symlink following.
- **Hardware Architecture**: Apple Silicon arm64 verified; Intel x86_64 fail-closed with user-friendly error.

---

## 5. File Ownership Map

- `tests/test_diarization_pipeline.py`: Pipeline test fixes and real audio fixture validation
- `recorder/export.py`: Trailing whitespace cleanup, speaker name resolution and contract synchronization
- `static/tokens.css`: Blank EOF cleanup
- `recorder/diarization_merge.py`: Dual `speaker_id` and `speaker` assignment
- `recorder/session.py` & `recorder/diarizer.py`: Checkpoint persistence, `manifest.speakers` synchronization
- `recorder/http_server.py`: API route contracts, CSRF validation, queue management
- `static/index.html` & `static/app.js`: Prominent upload controls on initial screen, speaker legend and badge rendering, queue deletion
- `tests/test_diarization_recovery.py`: Recovery, checkpoint, and cancel/retry tests
- `tests/test_diarization_8hour_timeline.py`: Simulated 8+ hour timeline test
- `tests/test_diarization_api.py`: Comprehensive HTTP API coverage

---

## 6. Verification and Rollback Plan

- **Automated Tests**:
  - `python3 -m unittest discover -s tests -p "test_diarization*.py"`
  - Full regression test suite: `python3 -m unittest discover -s tests -p "test_*.py"`
- **Browser Flow**: Run Playwright / Chrome verification on ephemeral port covering upload, diarization toggle, speakers legend, rename, cancel/retry, and exports.
- **Lint & Whitespace**: `git diff --check` must return exit code 0.
- **Rollback**: Clean worktree on dedicated branch; old path remains intact as backup.
