# Independent Review: v1.3 Speaker Identity (Stage 1)

**Reviewer:** Antigravity Claude Opus 4.6 Thinking
**Reviewer Model Family:** Claude (Anthropic) — independent from writer (Gemini)
**Reviewer Quota Pool:** Antigravity Claude/GPT pool — independent from writer (Antigravity Gemini pool)
**Review Date:** 2026-09-11T08:38 America/New_York
**Branch:** `feature/speaker-identity-v1.3-20260911`
**Base Commit:** `a361053e3ef5f8ca00f11da58744cc04d244c330`
**Writer Commits:** `b314ea6`, `c664493`
**HEAD at review:** `c664493`
**Scope:** Read-only review of the diff `a361053..c664493` and all five Stage 1 documents
**Product Code Changes:** NONE (confirmed: 0 product `.py` files in diff)

---

## 1. Smoke Test Results

| Question | Expected | Observed | Status |
|----------|----------|----------|--------|
| Canonical policy ID | `multi-agent-governance-2026-09-09.2` | `multi-agent-governance-2026-09-09.2` in AGENTS.md L3, shared-governance.md L4, GEMINI.md L4 | ✅ PASS |
| Root Codex role | Product Owner / coordinator / reviewer only | AGENTS.md §1: "Product Owner / coordinator / reviewer role"; shared-governance.md: "Only Product Owner/coordinator/reviewer" | ✅ PASS |
| Reserve floors | 20% weekly/monthly, 15% 5h/daily, 25% Root Codex, 20%/15% Spark | shared-governance.md L14: "20% weekly/monthly, 15% 5h/daily; Root 25%; Spark 20% weekly/15% 5h" | ✅ PASS |
| Required reports | ERROR_LOG and QUALITY_REPORT | shared-governance.md L16: "`ERROR_LOG` and `QUALITY_REPORT` required for every large milestone" | ✅ PASS |

**Smoke test verdict: INSTRUCTION_CONTEXT_VERIFIED**

---

## 2. Live Resource Data Audit

User-supplied live data at preflight time:
- Antigravity Gemini: weekly 100%, 5h 99%, reset ≈6d14h/1h39m
- Antigravity Claude/GPT: weekly 36%, 5h 100%, reset ≈4h29m
- Claude Desktop Opus 5 High: weekly 85%, resets Tue 7:00 AM

**MODEL_ROUTING_PLAN findings:**

| Field | Expected | Observed | Verdict |
|-------|----------|----------|---------|
| Gemini weekly | 100% | 100% | ✅ |
| Gemini 5h | 99% | 99% | ✅ |
| Gemini reset | ≈6d14h/1h39m (relative OK with note) | "Reset time UNKNOWN" | ⚠️ **DEF-01** |
| Claude/GPT weekly | 36% | 36% | ✅ |
| Claude/GPT 5h | 100% | 100% | ✅ |
| Claude/GPT reset | ≈4h29m (relative OK with note) | "Reset time UNKNOWN" | ⚠️ **DEF-02** |
| Claude Desktop weekly | 85% | 85% | ✅ |
| Claude Desktop reset | Tue 7:00 AM | "Reset time UNKNOWN" | ⚠️ **DEF-03** |

---

## 3. Document-by-Document Review

### 3.1 TASK_RESEARCH_BRIEF_v1.3_SPEAKER_IDENTITY.md

#### 3.1.1 Verified Current State — Factual Accuracy

| Claim in Brief | Actual Codebase State | Verdict |
|----------------|----------------------|---------|
| "uses `recorder/diarizer.py` and `recorder/diarization_merge.py`" | Both files exist and are imported | ✅ |
| "assigns generic labels like 'SPEAKER_00', 'SPEAKER_01'" | Actual labels: `speaker_01`, `speaker_02` (lowercase, 1-indexed via `CentroidRegistry._next_speaker_id()` at [diarizer.py:300-304](file:///Users/kuznetcovpavel/max/rech-v-tekst-speaker-identity-v1_3-20260911/recorder/diarizer.py#L300-L304)) | ⚠️ **DEF-04** |
| "loses context across long calls (chunk boundaries)" | `CentroidRegistry` already performs embedding-based cross-chunk matching with cosine similarity at [diarizer.py:324-366](file:///Users/kuznetcovpavel/max/rech-v-tekst-speaker-identity-v1_3-20260911/recorder/diarizer.py#L324-L366). Display names are `"Говорящий {num}"` | ⚠️ **DEF-05** |
| "No self-introduction detection exists in `recorder/transcribe.py`" | Confirmed: no self-introduction detection in `recorder/transcribe.py` | ✅ |

**DEF-04 (Medium):** The research brief claims speaker labels are `SPEAKER_00`, `SPEAKER_01` (uppercase, 0-indexed). The actual code uses `speaker_01`, `speaker_02` (lowercase, 1-indexed) and display names `Говорящий 1`, `Говорящий 2`. This is factually incorrect and will mislead the implementer.

**DEF-05 (High):** The brief claims the app "loses context across long calls (chunk boundaries)." In reality, `CentroidRegistry.match_cluster()` already performs embedding-based cross-chunk speaker matching using cosine similarity against stored centroids. The research brief describes v1.2 as if it has no cross-chunk continuity, but the code shows otherwise. This fundamentally misrepresents the current state and will lead to duplicated architecture in the "planned new" `speaker_identity.py`.

#### 3.1.2 Acceptance Criteria vs. User Requirements

| User Requirement | AC Coverage | Verdict |
|-----------------|-------------|---------|
| Distinguish real voices between chunks | AC-6: "voices are stable across chunks" | ✅ |
| Name only if self-introduced | AC-1: self-introduction detection | ✅ |
| Others' mention doesn't rename | AC-2: explicit negative case | ✅ |
| Identical names don't merge voices | AC-4: explicit | ✅ |
| Manual correction in current session | AC-5: "propagates within the session" | ✅ |
| Persistent profiles not the basis | AC-7: "permanent biometric storage is disabled by default" | ✅ |
| AC-3 display name | "Спикер N" | Actual code uses "Говорящий N" | ⚠️ **DEF-06** |

**DEF-06 (Low):** AC-3 specifies unnamed speakers remain as "Спикер N" (Speaker N), but the current codebase uses "Говорящий N" (lit. "Speaking N"). The research brief does not acknowledge this discrepancy, which will either require a rename or an explicit acceptance of the existing convention.

#### 3.1.3 Architecture — "regex + LLM validation"

The phrase appears in Option B description (line 33):

> "NLP text analysis (regex + LLM validation) to confidently identify self-introductions"

**DEF-07 (High — Potential Blocker):** The phrase "LLM validation" does not specify that this LLM is local and optional. Per user requirements: "architecture fully local: do not permit a mandatory cloud LLM/API in the identification path." The phrase "regex + LLM validation" is permitted **only if explicitly local and optional**. The brief does not contain the words "local", "optional", or "offline" in association with the LLM validation. This must be clarified before implementation begins.

The Concrete Local Stack section (lines 51-55) mentions only embedding models (pyannote, wespeaker), not an NLP/LLM model for introduction parsing. There is no mention of which local LLM would be used for "LLM validation" or that it can be disabled.

#### 3.1.4 Ownership Map Accuracy

| Planned Path | Issue |
|-------------|-------|
| `recorder/speaker_identity.py` (planned new) | `CentroidRegistry` and `EmbeddingExtractor` already exist in `recorder/diarizer.py`. The brief does not acknowledge this overlap. **DEF-08** |
| `recorder/nlp_intro.py` (planned new) | OK — no existing intro detection |
| `ui.py` (existing) | Correct |
| `recorder/export.py` (existing) | Correct |

**DEF-08 (Medium):** The ownership map plans a new `recorder/speaker_identity.py` for "Embedding extraction & clustering" without acknowledging that `CentroidRegistry` (L284) and `EmbeddingExtractor` (L179) already exist in `recorder/diarizer.py`. The implementer may create duplicate functionality. The brief should specify whether v1.3 refactors these out of `diarizer.py` or builds on them in-place.

#### 3.1.5 Unresolved Blockers

**DEF-09 (Medium):** Blocker 1 states "Latency of extracting embeddings concurrently with Whisper transcription." However, `EmbeddingExtractor` already exists in `diarizer.py` and is used in production v1.2. The blocker should be reframed: the real spike question is whether the *additional* NLP/intro-parsing pipeline adds latency on top of the existing embedding pipeline, not whether embedding extraction itself is feasible.

#### 3.1.6 Policy Compliance Checklist

| Required Field | Present | Notes |
|---------------|---------|-------|
| User-visible outcome | ✅ | |
| Measurable acceptance criteria | ✅ | 10 criteria |
| Verified current state | ⚠️ | Partially inaccurate (DEF-04, DEF-05) |
| ≥2 options + comparison | ✅ | 3 options with 5-axis comparison |
| Selected option + rejection reasons | ✅ | |
| Dependencies/unknowns/risks | ✅ | |
| Minimal vertical slice | ⚠️ | Not explicitly defined as a single deliverable |
| File/subsystem ownership map | ✅ | But inaccurate (DEF-08) |
| Verification/rollback/evidence plan | ✅ | |

---

### 3.2 MODEL_ROUTING_PLAN_v1.3_SPEAKER_IDENTITY.md

| Required Field per §5 | Present | Notes |
|----------------------|---------|-------|
| Primary executor with all fields | ✅ | Gemini 3.1 Pro Low |
| ≥2 independent backups | ✅ | Claude Opus 4.6 (AGY) + Claude Desktop Opus 5 |
| Task fit per candidate | ⚠️ | Not explicitly stated for each |
| Live quota and reset | ⚠️ | Reset times marked UNKNOWN despite available data (DEF-01/02/03) |
| Protected reserve | ✅ | |
| Current load | ✅ | |
| Expected quota cost | ✅ | |
| Promotion trigger | ✅ | |

**DEF-10 (Low):** Both backups are Claude-family models. While they draw from independent quota pools (Antigravity Claude/GPT vs. Claude Desktop), they are the same model family. Policy §5 says "Use different model families for implementation and final review when possible." If Gemini (primary/implementer) fails over to backup, the reviewer should ideally be from yet another family. However, this is a documentation stage with no implementation, so the risk is low.

---

### 3.3 ALLOCATION_REPORT_v1.3_SPEAKER_IDENTITY.md

| Required Field per §11 | Present | Verdict |
|------------------------|---------|---------|
| Research brief path + selected option | ✅ | |
| Preflight timestamp | ⚠️ | Date only, no time | 
| Agent/model/quota table | ✅ | |
| Selected executor + reviewer + owned paths | ✅ | |
| Parallel workstreams | ✅ (None) | |
| Quota minimization rationale | ✅ | |
| Codex implementation status | ✅ | PROHIBITED |
| Spark status separate line | ✅ | |

No blocking defects.

---

### 3.4 ERROR_LOG_v1.3_SPEAKER_IDENTITY.md

| Required Field per §10 | Present | Verdict |
|------------------------|---------|---------|
| Timestamp | ✅ | Date only |
| Project, milestone, branch/commit | ✅ | |
| Executor app + model | ✅ | |
| Exposing check | ✅ | |
| Expected / observed | ✅ | |
| Root cause + impact/severity | ✅ | |
| Classification (new/pre-existing/env) | ✅ | |
| Owner + fix/mitigation | ✅ | |
| Changed files | ✅ | |
| Retest evidence | ⚠️ | "Follow-up commit diff inspection" — no link to c664493 |
| Final status | ✅ | RESOLVED |
| Regression risk | ✅ | |
| Quota/failover | ✅ | |

The log is append-only compliant. One entry for the Root Codex review defect from b314ea6, resolved in c664493.

---

### 3.5 QUALITY_REPORT_v1.3_SPEAKER_IDENTITY.md

| Required Field per §10 | Present | Correct | Notes |
|------------------------|---------|---------|-------|
| Acceptance-criteria coverage | ✅ | ⚠️ | Marked PASS, but criteria are not yet testable (Stage 1 = docs only). Should be DOCUMENTED or N/A for Stage 1. |
| Implementation completeness | ✅ | ✅ | NOT VERIFIED |
| Automated tests | ✅ | ✅ | NOT VERIFIED |
| Runtime/UI evidence | ✅ | ✅ | NOT VERIFIED |
| Regressions | ✅ | ✅ | NOT VERIFIED |
| Security/data-safety | ✅ | ✅ | PASS (local-only confirmed in design) |
| Documentation/operability | ✅ | ⚠️ | Marked PASS despite factual errors (DEF-04, DEF-05) |
| Maintainability | ✅ | ⚠️ | Marked PASS without implementation |
| Reviewer independence | ✅ | ✅ | PENDING |
| Unresolved defects | ✅ | ❌ | Marked NONE, but this review found defects |
| Rollback readiness | ✅ | ✅ | |
| Executor self-report | ✅ | ✅ | |
| Reviewer verdict | ✅ | ✅ | PENDING |
| Evidence links | ⚠️ | | Generic "Git diff" — no commit SHAs |
| Severity counts | ✅ | ⚠️ | "High: 1 (Resolved)" — only counts ERR_LOG entry, not research accuracy |
| Overall quality score | ✅ | ✅ | PENDING |
| Final status | ✅ | ⚠️ | "REWORK REQUIRED (Pending final verification)" — status is correct but reason text is misleading |

---

## 4. Diff Integrity

| Check | Result |
|-------|--------|
| Files changed | 8 files, 192 insertions, 4 deletions |
| Product `.py` files modified | 0 — ✅ |
| Only docs + governance adapters | ✅ |
| No dependency installs | ✅ |
| No release/publish actions | ✅ |
| Working tree clean | ✅ (`git status --short` empty) |
| Correct branch | ✅ `feature/speaker-identity-v1.3-20260911` |

---

## 5. Defect Register

| ID | Severity | Document | Section | Description | Blocks Implementation? |
|----|----------|----------|---------|-------------|----------------------|
| DEF-01 | Low | MODEL_ROUTING_PLAN | Primary Executor | Gemini reset time marked UNKNOWN despite available relative data (≈6d14h weekly, ≈1h39m 5h). Policy §4 allows relative time with snapshot note. | No |
| DEF-02 | Low | MODEL_ROUTING_PLAN | Backup 1 | Claude/GPT reset marked UNKNOWN despite available relative data (≈4h29m). | No |
| DEF-03 | Low | MODEL_ROUTING_PLAN | Backup 2 | Claude Desktop reset marked UNKNOWN despite available absolute data (Tue 7:00 AM). | No |
| DEF-04 | Medium | TASK_RESEARCH_BRIEF | Verified Current State | Speaker labels claimed as `SPEAKER_00`/`SPEAKER_01` but actual code uses `speaker_01`/`speaker_02` | No, but misleads implementer |
| DEF-05 | **High** | TASK_RESEARCH_BRIEF | Verified Current State | Claims v1.2 "loses context across long calls" but `CentroidRegistry.match_cluster()` already performs embedding-based cross-chunk speaker matching. Fundamentally misrepresents existing capability. | **Yes — must be corrected before implementation to avoid duplicated architecture** |
| DEF-06 | Low | TASK_RESEARCH_BRIEF | AC-3 | "Спикер N" vs. actual "Говорящий N" display name convention not acknowledged | No |
| DEF-07 | **High** | TASK_RESEARCH_BRIEF | Option B Description | "regex + LLM validation" does not specify local/optional. User requires fully local architecture; cloud LLM in identification path is a defect. Must be clarified as "local-only and optional" or removed. | **Yes — ambiguity blocks implementation** |
| DEF-08 | Medium | TASK_RESEARCH_BRIEF | Ownership Map | Plans new `speaker_identity.py` for embedding/clustering without acknowledging existing `CentroidRegistry` + `EmbeddingExtractor` in `diarizer.py` | No, but causes confusion |
| DEF-09 | Medium | TASK_RESEARCH_BRIEF | Unresolved Blockers | Blocker 1 misstates the spike: embedding extraction already works in v1.2. The real spike is NLP pipeline latency. | No |
| DEF-10 | Low | MODEL_ROUTING_PLAN | Backups | Both backups are Claude-family; policy prefers different families. Acceptable for docs-only stage. | No |

### Summary Counts
- **High:** 2 (DEF-05, DEF-07) — both are blocking
- **Medium:** 3 (DEF-04, DEF-08, DEF-09)
- **Low:** 5 (DEF-01, DEF-02, DEF-03, DEF-06, DEF-10)
- **Total:** 10

---

## 6. Blocking Analysis

**DEF-05** and **DEF-07** are blocking because:

1. **DEF-05** (misrepresented current state): If the implementer believes v1.2 has no cross-chunk speaker continuity, they will architect a new `speaker_identity.py` module that reimplements what `CentroidRegistry` already does. The minimal vertical slice must build on the existing embedding infrastructure, not duplicate it.

2. **DEF-07** (LLM validation ambiguity): The user explicitly required "architecture fully local: do not permit a mandatory cloud LLM/API in the identification path." The research brief's Option B description says "regex + LLM validation" without specifying local-only and optional. An implementer could introduce a cloud API dependency. This must be disambiguated before any code is written.

Both defects affect the minimal vertical slice definition and the architecture decision that gates all implementation stages.

---

## 7. Final Verdict

### Status: **REWORK REQUIRED**

Two High-severity blocking defects (DEF-05, DEF-07) in the TASK_RESEARCH_BRIEF must be resolved before implementation begins. The research brief's "Verified Current State" materially misrepresents the existing embedding/clustering infrastructure, and the "LLM validation" phrase violates the user's local-only architecture requirement unless explicitly qualified.

### Required Actions Before Implementation Can Proceed

1. **DEF-05:** Rewrite "Verified Current State" to accurately describe `CentroidRegistry` with embedding-based cross-chunk matching already present in `recorder/diarizer.py`. Update the ownership map to specify whether v1.3 refactors existing classes or extends them in-place.

2. **DEF-07:** In Option B description, change "regex + LLM validation" to explicitly state that any LLM validation is **local-only** (e.g., a local model like the existing Whisper) **and optional** (regex alone must be sufficient for the minimal vertical slice). Alternatively, remove "LLM validation" from the core pipeline and note it as a future enhancement.

3. **DEF-04, DEF-08, DEF-09:** Correct factual inaccuracies about label format, existing code references, and blocker framing.

4. **DEF-01/02/03:** Record available reset time data (relative with snapshot note is acceptable per policy §4).

### Reviewer Attribution

- **Reviewer Agent:** Antigravity
- **Reviewer Model:** Claude Opus 4.6 Thinking
- **Reviewer Quota Pool:** Antigravity Claude/GPT pool (independent from writer's Gemini pool)
- **Review Type:** Independent cross-family review
- **Review Commit:** (to be filled after commit)
- **Product Code Changes by Reviewer:** NONE
