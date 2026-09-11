# Task Research Brief: v1.3 Speaker Identity

## Verified Current State
- The app (v1.2) uses `recorder/diarizer.py` and `recorder/diarization_merge.py` for chunk-based diarization.
- The current implementation assigns generic labels like "SPEAKER_00", "SPEAKER_01", but loses context across long calls (chunk boundaries). No self-introduction detection exists in `recorder/transcribe.py`.

## User-Visible Outcome
The transcribed output will feature consistent speaker names across the entire long call. If a speaker introduces themselves (e.g., "My name is John"), all past and future utterances by that physical voice in the current session will be labeled "John". The export will contain a reference/link to the exact timestamp of the self-introduction.

## Measurable Acceptance Criteria
1. When cluster A says "Меня зовут Анна" (My name is Anna), subsequent utterances by A receive the name Anna and a link to the proof moment.
2. If another voice says "Анна сказала" (Anna said), it does not rename itself.
3. Voices without self-introduction remain as "Спикер N" (Speaker N).
4. Identical names from two distinct voices do not merge the clusters.
5. Manual correction propagates within the session.
6. Long recordings are processed in chunks with checkpoint, cancel/resume, without loss or duplication, and voices are stable across chunks.
7. Names and embeddings are strictly local; permanent biometric storage is disabled by default.
8. Exports contain speaker labels/names and the proof of name.
9. Tests include mock diarization/transcript fixtures, synthetic multi-chunk streams, and negative examples in RU/EN.
10. UI and runtime evidence are mandatory during implementation.

## Architecture Variants

### Option A: Diarization + local rules for self-introduction search
- **Description:** Basic diarization per chunk, using NLP to find "my name is X" and applying it to that chunk's speaker.
- **Correctness:** Low cross-chunk stability.
- **Risk:** High risk of losing speaker context across chunks and misattributing names.
- **Reversibility:** High.
- **Time / Ops Burden:** Low implementation time, but high operations burden to fix user complaints.
- **Quota:** Low.

### Option B: Diarization + speaker embeddings for inter-chunk clustering + text analysis of self-introduction and manual correction
- **Description:** Diarization within chunk, extraction of speaker embeddings (e.g., via local ONNX model like Wespeaker or Pyannote embeddings). Cross-chunk clustering. NLP text analysis (regex + LLM validation) to confidently identify self-introductions, mapping to the clustered speaker. Manual override support.
- **Correctness:** High, addresses cross-chunk stability and direct proof.
- **Risk:** Medium (requires embedding extraction performance check).
- **Reversibility:** High (local processing, can be safely disabled).
- **Time / Ops Burden:** Medium implementation time, low ops burden if accurate.
- **Quota:** Medium.

### Option C: Persistent registered voice profiles
- **Description:** Users register profiles ahead of time and system matches current voice to the database.
- **Correctness:** High (if profile exists).
- **Risk:** High privacy and security risk (biometric storage).
- **Reversibility:** Low (data deletion workflows required).
- **Time / Ops Burden:** High implementation time, high ops burden for user management.
- **Quota:** Low.

## Selected Option: Option B
**Reasoning:** Option B provides the necessary stability for long calls across chunks and allows for dynamic name assignment based on clear evidence, without relying on privacy-invasive persistent profiles (Option C). Option A fails criterion 6 (stability across chunks).

## Concrete Local Stack & Spike Plan
- **Stack Options:** 
  1. `pyannote/embedding` locally exported to ONNX.
  2. `wespeaker-voxceleb-resnet34` via ONNXRuntime.
- **Spike Plan:** In Stage 2, before integrating, run a performance spike using `wespeaker` on a mock audio chunk to measure latency on macOS (Apple Silicon). Product code remains untouched during the spike.

## Privacy and Security Risks
- **Risks:** Memory leaks or unauthorized persistence of speaker embeddings.
- **Mitigation:** All embeddings are kept in ephemeral session memory. Writing to disk requires user opt-in (disabled by default).

## Unresolved Blockers
- **Blocker 1:** Latency of extracting embeddings concurrently with Whisper transcription. (To be resolved by the Spike).

## Milestones and Boundaries
- **Stage 1 (Current):** Research and Preparation (No product code changes).
- **Stage 2:** Spike on embedding extraction latency; core diarization modification.
- **Stage 3:** NLP self-introduction parsing & manual override logic.
- **Stage 4:** UI integration, export formatting, and testing.

## Ownership Map
- `recorder/diarizer.py` (existing) - Core chunk diarization.
- `recorder/diarization_merge.py` (existing) - Inter-chunk logic.
- `recorder/speaker_identity.py` (planned new path) - Embedding extraction & clustering.
- `recorder/nlp_intro.py` (planned new path) - Self-introduction analysis.
- `ui.py` (existing) - Manual override & Speaker display.
- `recorder/export.py` (existing) - Export formatting with proof.

## Verification, Rollback, and Evidence Plan
- **Verification:** Unit tests with mock diarization fixtures, multi-chunk synthetic tests, and negative RU/EN examples.
- **Rollback:** Disable the `speaker_identity` clustering module via config flag and fall back to v1.2 behavior if performance issues occur.
- **Evidence:** Test suite logs and macOS UI screenshots/recordings proving the self-introduction link.
