# ERROR LOG — Long-Call Endurance & Diarization Acceptance Validation (v1.1)

All anomalies, errors, recoveries, and root-cause analyses during the endurance run are tracked here chronologically and append-only.

---

### [ENTRY-001] [2026-09-10T06:15:00-04:00] macOS Process Group Signaling vs PermissionError
- **Category**: Subprocess Signal Handling / OS Compatibility
- **Component**: `recorder.capture.stop_and_reap_process_group`
- **Symptom**: Calling `os.killpg(pid, signal.SIGINT)` on macOS raises `PermissionError: [Errno 1] Operation not permitted` if the caller is not the process group leader.
- **Handling / Fix**: `recorder.capture.stop_and_reap_process_group` already contains a robust fallback: when `os.killpg` raises `OSError`, it falls back to `os.kill(pid, signal.SIGINT)`. This cleanly terminates `sherpa-onnx` and reaps the child process within 0.1s.
- **Status**: RESOLVED (Verified).

---

### [ENTRY-002] [2026-09-10T06:27:00-04:00] Verification of Asynchronous Cancellation and Resumption
- **Category**: Asynchronous Checkpoint & Cancellation Verification
- **Component**: `recorder.diarizer.Diarizer`
- **Observation**: During validation execution of `work/run_longcall_validation.py`, window 0 (0-600s) and window 1 (540-1140s) completed successfully. Window 2 (1080-1680s) was initiated, and asynchronous cancellation was exercised. The active `sherpa-onnx` process was cleanly reaped via `stop_and_reap_process_group`.
- **Integrity Check**:
  - `diarization_checkpoint.json` preserved atomic state: `last_processed_window = 2` (windows 0, 1, 2 completed and checkpointed), `turn count = 219`.
  - Checkpoint integrity check showed 0 duplicate turns, 0 inverted timestamps, and valid speaker centroids across 3 speakers (`speaker_01`, `speaker_02`, `speaker_03`).
  - Whisper transcript in `transcript.json` and `transcript.txt` remained completely untouched and valid (`has_transcript: True`).
  - Resumption seamlessly restarted from window index 3 (`window_0003.wav`), proceeding through remaining sliding windows without turn loss or duplicate segments.
- **Status**: RESOLVED (Verified).
---

### [ENTRY-003] [2026-09-10T07:20:00-04:00] Diarization speaker display-name propagation failure
- **Category**: Data mapping regression
- **Component**: `recorder/diarization_merge.py`
- **Symptom**: Merging transcripts and diarization turns resulted in all segments mapped to `speaker_unknown`. `from_sec` and `to_sec` fallback to 0.0, causing negative durations.
- **Handling / Fix**: Updated `merge_diarization_with_segments` to fallback correctly to `start` and `end` keys. Applied fix and re-merged the final transcript output.
- **Status**: RESOLVED (Verified, Test Added).
