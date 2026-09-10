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
- **Observation**: During validation execution of `work/run_longcall_validation.py`, asynchronous cancellation was exercised.
- **Integrity Check**:
  - Immediately after real asynchronous cancellation, the new-session checkpoint correctly recorded `last_processed_window=1` with 110 turns (mtime 06:25:45).
  - Resumed processing correctly began at window 2.
  - The subsequent checkpoint was `last_processed_window=2` with 219 turns at 06:29:14.
  - Chronology verification script confirmed exactly 0 duplicate turn keys and 0 timestamp inversions.
  - Whisper transcript in `transcript.json` and `transcript.txt` remained completely untouched and valid (`has_transcript: True`).
- **Status**: RESOLVED (Verified).
---

### [ENTRY-003] [2026-09-10T07:20:00-04:00] Diarization speaker display-name propagation failure
- **Category**: Data mapping regression
- **Component**: `recorder/diarization_merge.py`
- **Symptom**: Merging transcripts and diarization turns resulted in all segments mapped to `speaker_unknown`. `from_sec` and `to_sec` fallback to 0.0, causing negative durations.
- **Handling / Fix**: Updated `merge_diarization_with_segments` to fallback correctly to `start` and `end` keys. Applied fix and re-merged the final transcript output.
- **Status**: RESOLVED (Verified, Test Added).
