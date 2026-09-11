# Task Research Brief: v1.3 Speaker Identity

## Verified Current State
- The app (v1.2) uses `EmbeddingExtractor` and `CentroidRegistry` in `recorder/diarizer.py`.
- `CentroidRegistry.match_cluster()` already performs voice matching between chunks using cosine similarity. Thus, speaker stability across long calls is already functioning.
- Current identifiers are `speaker_01`, `speaker_02`, etc. No self-introduction detection exists in the pipeline.

## User-Visible Outcome
The transcribed output will feature consistent speaker names across the entire long call. If a speaker introduces themselves (e.g., "My name is John"), all past and future utterances by that physical voice cluster in the current session will be labeled "John" instead of "speaker_01". The export will contain a reference/link to the exact timestamp of the self-introduction.

## Measurable Acceptance Criteria
1. When cluster A says "Меня зовут Анна" (My name is Anna), subsequent utterances by A receive the name Anna and a link to the proof moment.
2. If another voice says "Анна сказала" (Anna said), it does not rename itself.
3. Voices without self-introduction remain as `speaker_N` (or localized "Говорящий N").
4. Identical names from two distinct voice clusters do not merge the clusters.
5. Manual correction propagates within the session.
6. Long recordings are processed in chunks with checkpoint, cancel/resume, without loss or duplication, and voices are stable across chunks (already supported by `CentroidRegistry`).
7. Names and embeddings are strictly local; permanent biometric storage is disabled by default.
8. Exports contain speaker labels/names and the proof of name.
9. Tests include mock diarization/transcript fixtures, synthetic multi-chunk streams, and negative examples in RU/EN.
10. UI and runtime evidence are mandatory during implementation.

## Architecture Variants

### Option A: Diarization + local rules for self-introduction search (Selected)
- **Description:** Utilize the *existing* embedding/clustering architecture (`CentroidRegistry`). Add a deterministic local RU/EN parser to analyze text for first-person patterns ("меня зовут...", "my name is...", "я...") with confidence thresholds and negative checks (third-person, quotes). Attach detected `display_name` and proof metadata to the existing speaker cluster.
- **Correctness:** High, leveraging the already stable cross-chunk clustering.
- **Risk:** Low. No cloud API required. Deterministic parser is reliable.
- **Reversibility:** High. Name metadata layer can be safely bypassed.
- **Time / Ops Burden:** Low implementation time.
- **Quota:** Low (No LLM required for the core parser).

### Option B: Local LLM for Introduction Parsing
- **Description:** Same as Option A, but uses a local LLM to extract names instead of a deterministic parser.
- **Correctness:** High, but prone to hallucination.
- **Risk:** High latency and resource consumption on macOS.
- **Status:** Rejected for this milestone. Any local LLM can only be an optional, off-by-default future improvement.

### Option C: Persistent registered voice profiles
- **Description:** Users register profiles ahead of time and system matches current voice to the database.
- **Correctness:** High (if profile exists).
- **Risk:** High privacy and security risk (biometric storage).
- **Reversibility:** Low (data deletion workflows required).
- **Time / Ops Burden:** High implementation time, high ops burden for user management.
- **Quota:** Low.

## Selected Option: Option A
**Reasoning:** Option A extends the already working `CentroidRegistry` with a fast, deterministic local parser. It avoids heavy LLMs and respects the privacy boundary by avoiding persistent profiles (Option C).

## Minimal Vertical Slice
1. Take an existing diarized segment.
2. Run the deterministic local parser on the text.
3. If an introduction is detected, bind the `display_name` only to the current speaker cluster (e.g., `speaker_01`).
4. Ensure identical names do not merge clusters, and mentions by others do not rename the cluster.
5. Export/UI shows the name and proof. Manual edits propagate only in the current session.

## Concrete Local Stack & Spike Plan
- **Stack Options:** Deterministic Regex/Rule-based parser in Python for RU/EN.
- **Spike Plan:** In Stage 2, measure the *additional latency* of the local self-intro parser running on top of the already working embedding chain to ensure it doesn't block the real-time transcription flow.

## Privacy and Security Risks
- **Risks:** Memory leaks or unauthorized persistence of speaker names/metadata.
- **Mitigation:** All metadata is kept in ephemeral session memory. Writing to disk requires user opt-in (disabled by default).

## Unresolved Blockers
- **Blocker 1:** Latency of the new deterministic parser text analysis step. (To be resolved by the Spike).

## Milestones and Boundaries
- **Stage 1 (Current):** Research and Preparation (No product code changes).
- **Stage 2:** Spike on parser latency; extend `CentroidRegistry` with name metadata.
- **Stage 3:** Local deterministic RU/EN self-introduction parser & manual override logic.
- **Stage 4:** UI integration, export formatting, and testing.

## Ownership Map
- `recorder/diarizer.py` (existing) - Extend existing `CentroidRegistry` to store `display_name` and proof metadata. Do not create new duplicate embedding modules.
- `recorder/diarization_merge.py` (existing) - Inter-chunk logic.
- `recorder/nlp_intro.py` (planned new path) - Deterministic self-introduction parser layer.
- `ui.py` (existing) - Manual override & Speaker display.
- `recorder/export.py` (existing) - Export formatting with proof.

## Verification, Rollback, and Evidence Plan
- **Verification:** Unit tests with mock diarization fixtures, negative RU/EN examples (quotes, 3rd person).
- **Rollback:** Disable the `nlp_intro` parsing layer via config flag and fall back to v1.2 `speaker_N` behavior.
- **Evidence:** Test suite logs and macOS UI screenshots/recordings proving the self-introduction link.
