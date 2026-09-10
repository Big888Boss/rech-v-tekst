# MODEL_ROUTING_PLAN — Release Candidate v1.1

Canonical Policy ID: `multi-agent-governance-2026-09-09.2`
Project: `rech-v-tekst`
Milestone: Complete release candidate v1.1 for long-call transcription with offline speaker diarization
Canonical Repository: `/Users/kuznetcovpavel/max/rech-v-tekst-diarization-v1_1-20260909-c1c2decc`
Timestamp: 2026-09-09T22:30:00-04:00

---

## 1. Candidate Executors and Quota Pools

| Role | Candidate / Tool | Model & Effort Mode | Quota Pool | Live Status & Verified Capacity | Reserved Floor | Promotion Trigger |
|---|---|---|---|---|---|---|
| **Primary Executor** | Antigravity | Gemini 3.8 Flash (Medium/High) | Gemini Pool (Antigravity) | AVAILABLE (~92% weekly, ~96% 5h remaining) | 15% 5h / 20% weekly | Initial default for all implementation, testing, and UI work |
| **Backup Executor 1** | Qoder CN | Qwen3.8-Max | Qoder Pool | AVAILABLE (483 / 800 remaining) | 20% monthly | Promoted if Primary returns quota/rate-limit error or hits 15%/20% reserve floor |
| **Backup Executor 2** | Kimi Code | K3-256k / K2.7 Code | Kimi Pool | AVAILABLE (9.12% 5h, 4.36% 7d used) | 15% 5h / 20% weekly | Promoted if Backup 1 is unavailable or exhausted |
| **Independent Reviewer** | Claude Desktop / Code | Claude Opus 4.6 Thinking / Opus 5 | Claude Quota Pool | AVAILABLE (1% weekly used) | 25% pool reserve | Assigned post-commit for independent quality review and verification |
| **Product Owner / Coordinator** | Root Codex | GPT-6 Astra / medium | Codex Quota Pool | COORDINATOR ONLY (Product implementation PROHIBITED) | 25% reserve | Synthesizes reports, defines acceptance gates, reviews diff |

---

## 2. Reserve Enforcement and Automatic Failover Protocol

1. **Floors**:
   - Keep >= 20% weekly/monthly quota unused across all pools.
   - Keep >= 15% 5-hour quota unused across all pools.
   - Keep >= 25% Root Codex allowance unused.
2. **Failover Procedure**:
   - Save atomic handoff report to `docs/` or `outputs/`.
   - Freeze active work in current quota pool.
   - Transfer handoff and remaining milestone to the next independent pool (Antigravity -> Qoder -> Kimi).
   - Record failover reason, model identity, and timestamp in `ERROR_LOG`.
