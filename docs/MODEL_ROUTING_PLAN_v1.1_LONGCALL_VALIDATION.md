# MODEL_ROUTING_PLAN — Long-Call Acceptance & Endurance Validation

Canonical Policy ID: `multi-agent-governance-2026-09-09.2`
Project: `rech-v-tekst`
Milestone: Rigorous Acceptance and Endurance Validation of Offline Speaker Diarization for Long Calls (>= 2:00:00)
Base Commit: `92a6c7493ca6e7776215a2ad39baeeb9a2621f30`
Isolated Worktree: `/Users/kuznetcovpavel/max/rech-v-tekst-longcall-validation-v1_1-20260910`
Branch: `validation/v1.1-long-call-20260910`
Timestamp: 2026-09-10T05:55:00-04:00

---

## 1. Candidate Executors and Quota Pools

| Role | Candidate / Tool | Model & Effort Mode | Quota Pool | Live Status & Verified Capacity | Reserved Floor | Promotion Trigger |
|---|---|---|---|---|---|---|
| **Primary Executor** | Antigravity | Gemini 3.8 Flash (High) | Gemini Pool (Antigravity) | AVAILABLE (~90% weekly, ~94% 5h remaining) | 15% 5h / 20% weekly | Default executor for validation scripts, endurance runs, audio synthesis, and documentation |
| **Backup Executor 1** | Qoder CN | Qwen3.8-Max | Qoder Pool | AVAILABLE (483 / 800 remaining) | 20% monthly | Promoted if Primary returns quota/rate-limit error or hits 15%/20% reserve floor |
| **Backup Executor 2** | Kimi Code | K3-256k / K2.7 Code | Kimi Pool | AVAILABLE (9.12% 5h, 4.36% 7d used) | 15% 5h / 20% weekly | Promoted if Backup 1 is unavailable or exhausted |
| **Independent Reviewer** | Claude Desktop / Code | Claude Opus 4.6 Thinking / Opus 5 | Claude Quota Pool | AVAILABLE (1% weekly used) | 25% pool reserve | Assigned post-validation for final review and verification |
| **Product Owner / Coordinator** | Root Codex | GPT-6 Astra / medium | Codex Quota Pool | COORDINATOR ONLY (Product implementation PROHIBITED) | 25% reserve | Reviews validation artifacts and reports, gives acceptance decision |

---

## 2. Reserve Enforcement and Automatic Failover Protocol

1. **Reserve Floors**:
   - Weekly / monthly quota unused floor >= 20% across all external pools.
   - 5-hour quota unused floor >= 15% across all external pools.
   - Root Codex allowance floor >= 25% (Root Codex strictly acts as PO/reviewer, no implementation).
2. **Failover Procedure**:
   - Save atomic checkpoint and logs to `docs/` and `work/`.
   - Freeze active work in current quota pool.
   - Transfer execution context to next designated pool in order (Antigravity -> Qoder -> Kimi).
   - Record failover reason, model identity, and timestamp in `ERROR_LOG_v1.1_LONGCALL_VALIDATION.md`.
