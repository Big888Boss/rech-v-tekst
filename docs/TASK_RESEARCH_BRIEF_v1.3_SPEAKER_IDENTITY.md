# Task Research Brief: v1.3 Speaker Identity

## Verified Current State
- The app (v1.2) provides basic speech-to-text without robust speaker identification between chunks or self-introduction recognition.
- Diarization may currently just label "Speaker 1", "Speaker 2", etc. per chunk, but loses track across long call boundaries.

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
- **Description:** Basic diarization per chunk, using regex or NLP to find "my name is X" and applying it to that chunk's speaker.
- **Correctness:** Low cross-chunk stability.
- **Risk:** High risk of losing speaker context across chunks.
- **Reversibility:** High.
- **Time / Ops Burden:** Low implementation time, but high ops burden to fix user issues.
- **Quota:** Low.

### Option B: Diarization + speaker embeddings for inter-chunk clustering + text analysis of self-introduction and manual correction
- **Description:** Diarization within chunk, extraction of speaker embeddings. Cross-chunk clustering based on embeddings. NLP text analysis to confidently identify self-introductions, mapping to the clustered speaker. Manual override support.
- **Correctness:** High, addresses cross-chunk stability and direct proof.
- **Risk:** Medium (requires embedding extraction and clustering).
- **Reversibility:** High (local processing, can be rolled back).
- **Time / Ops Burden:** Medium implementation time, low ops burden if accurate.
- **Quota:** Medium.

### Option C: Persistent registered voice profiles
- **Description:** Users register profiles ahead of time.
- **Status:** Evaluated as an optional future variant only. Violates privacy goal if made default.

## Selected Option: Option B
**Reasoning:** Option B provides the necessary stability for long calls across chunks and allows for dynamic name assignment based on clear evidence, without relying on privacy-invasive persistent profiles (Option C). Option A fails criterion 6 (stability across chunks). Diarization (who spoke when) is strictly separated from identification (what is their name).

## Dependencies and Unknowns
- **Dependencies:** Local embedding model, clustering algorithm (e.g., Agglomerative Clustering or DBSCAN), NLP for self-introduction.
- **Unknowns:** Performance of embedding extraction on macOS Native Shell in real-time or near real-time.

## Privacy and Security
- All processing (names, embeddings) is strictly local.
- Permanent biometric storage is disabled by default.

## Minimal Vertical Slice
- Process a synthetic 2-chunk mock with one self-introduction.
- Verify cross-chunk clustering and name propagation in memory.

## Stage Boundaries
- **Stage 1 (Current):** Research and Preparation (No product code changes).
- **Stage 2:** Core diarization & embedding clustering (backend).
- **Stage 3:** NLP self-introduction parsing & manual override.
- **Stage 4:** UI integration, persistence, export, and testing.

## Ownership Map
- `/core/diarization/` - Embedding & Clustering.
- `/core/nlp/` - Self-introduction analysis.
- `/ui/components/` - Manual override & Speaker display.
- `/export/` - Export formatting with proof.

## Verification, Rollback, and Evidence Plan
- **Verification:** Mock diarization fixtures, multi-chunk tests, negative examples.
- **Rollback:** Revert to v1.2 branch if embedding processing fails performance requirements.
- **Evidence:** Test logs and UI screenshots/recordings of the proof link.
