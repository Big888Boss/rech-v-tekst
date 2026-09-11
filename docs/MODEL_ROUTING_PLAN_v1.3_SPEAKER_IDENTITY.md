# Model Routing Plan: v1.3 Speaker Identity

## Preflight Summary (America/New_York 2026-09-11)

### Primary Executor
- **Agent/Application:** Antigravity
- **Exact Model/Effort:** Gemini 3.1 Pro Low
- **Quota Pool:** Antigravity Gemini pool
- **Live Quota & Reset:** 100% weekly remaining (reset ~6d14h), 99% 5-hour remaining (reset ~1h39m). *Data from preflight snapshot.*
- **Protected Reserve:** 20% weekly, 15% 5-hour.
- **Current Load:** AVAILABLE (No active load).
- **Required Tools/Repo Access:** Needs full repository access, terminal execution, macOS UI testing capabilities.
- **Expected Quota Cost:** Medium (approx. 5% of 5-hour pool).
- **Promotion Trigger:** Not applicable (Primary).
- **Handoff Path:** If reserve floor hit or execution fails, save diff/commit, hand off to Backup 1.

### Backup/Reviewer 1
- **Agent/Application:** Antigravity
- **Exact Model/Effort:** Claude Opus 4.6 Thinking
- **Quota Pool:** Antigravity Claude/GPT pool
- **Live Quota & Reset:** 36% weekly remaining, 100% 5-hour remaining (reset ~4h29m). *Data from preflight snapshot.*
- **Protected Reserve:** 20% weekly, 15% 5-hour.
- **Current Load:** AVAILABLE (No active load).
- **Required Tools/Repo Access:** Needs read/write repository access.
- **Expected Quota Cost:** Low (Review only).
- **Promotion Trigger:** Primary executor exhaustion, queuing, or need for independent architecture review. 
- **Independence Note:** For this documentation stage, two Claude-family backups are acceptable. For implementation, Gemini is the writer and Claude is the independent reviewer. If failover to Claude writer occurs, a reviewer from a different available independent family must be selected after a new live preflight; if none is available, the lack of an independent family must be recorded and the operator notified before proceeding with the risky stage.
- **Handoff Path:** If reserve floor hit, save state and hand off to Backup 2.

### Backup/Reviewer 2
- **Agent/Application:** Claude Desktop
- **Exact Model/Effort:** Opus 5 High
- **Quota Pool:** Claude Desktop pool
- **Live Quota & Reset:** 85% weekly remaining. Reset time: Tuesday, 07:00.
- **Protected Reserve:** 20% weekly.
- **Current Load:** AVAILABLE.
- **Required Tools/Repo Access:** Read access for review.
- **Expected Quota Cost:** Low.
- **Promotion Trigger:** Backup 1 exhaustion.
- **Handoff Path:** If exhausted, halt and notify operator.

### Other Agents (Not Assigned)
- **Standard Codex:** 6% remaining. RESERVED. Product implementation PROHIBITED.
- **GPT-5.3-Codex-Spark:** 100% 5h and weekly remaining. AVAILABLE. OUT OF SCOPE for this large architecture/UI milestone.
- **Qoder:** Qwen3.8-Max BUSY on unrelated project.
- **Kimi:** EXHAUSTED until Sep 25.
- **Gemini Desktop Pro:** UNKNOWN.
