# Model Routing Plan: v1.3 Speaker Identity

## Preflight Summary (America/New_York 2026-09-11)

### Primary Executor
- **Agent/Application:** Antigravity
- **Model/Effort:** Gemini 3.1 Pro Low
- **Quota Pool:** Antigravity Gemini pool (weekly 100% remaining, 5-hour 99% remaining)
- **Status:** AVAILABLE
- **Role & Reason:** Primary implementation; has the necessary quota and UI/browser context for visual evidence and large implementation.

### Backup/Reviewer 1
- **Agent/Application:** Antigravity
- **Model/Effort:** Claude Opus 4.6 Thinking
- **Quota Pool:** Antigravity Claude/GPT pool (weekly 36% remaining, 5-hour 100% remaining)
- **Status:** AVAILABLE
- **Role & Reason:** Independent review and failover; preserved for independent quota pool.
- **Promotion Trigger:** Primary exhaustion, queuing, or need for independent architecture review.

### Backup/Reviewer 2
- **Agent/Application:** Claude Desktop
- **Model/Effort:** Opus 5 High
- **Quota Pool:** Claude Desktop pool (weekly 85% remaining)
- **Status:** AVAILABLE
- **Role & Reason:** Second independent reviewer or secondary fallback.

### Other Agents (Not Assigned)
- **Qoder Qwen3.8-Max:** BUSY on unrelated project (345/800 requests remaining).
- **Kimi:** EXHAUSTED until Sep 25.
- **Gemini Desktop Pro:** UNKNOWN (requires probe).
- **Root Codex standard pool:** 6% remaining (RESERVED, product implementation PROHIBITED).
- **GPT-5.3-Codex-Spark:** 100% remaining (AVAILABLE, but OUT OF SCOPE for this large architecture and UI milestone).

## Handoff Path
If Gemini 3.1 Pro Low drops below reserve floors, save current state, branch, changed files, and next actions to a handoff document, stop assignment, and hand off to Antigravity Claude Opus 4.6 Thinking.

*Note: No application changes, dependency additions, release, installation, or publication during this stage.*
