# ALLOCATION_REPORT: ONBOARDING-SUPPORT-v1.3.1-20260912

- **Research Brief Path**: `docs/governance/ONBOARDING-SUPPORT-v1.3.1-20260912/TASK_RESEARCH_BRIEF.md`
- **Selected Implementation Option**: Option 2 (Web-based HTML/JS Integration)
- **Resource Preflight Timestamp**: 2026-09-12T12:13:18-04:00

## Agent / Model / Quota Table

| Agent/Application | Model | Quota (Weekly/5h) | State | Role / Reason |
| :--- | :--- | :--- | :--- | :--- |
| Antigravity | Gemini 3.1 Pro Low | 95% / 99% | AVAILABLE | Primary Executor. Best fit for UI/web tasks. |
| Antigravity | Claude Sonnet 4.6 Thinking | 89% / 100% | AVAILABLE | Independent Reviewer (separate pool). |
| Antigravity | Gemini 3.8 Flash Medium | Shared with Primary | AVAILABLE | Backup 1 (Degraded reasoning option). |
| Antigravity | GPT-OSS 120B Medium | Shared with Reviewer | AVAILABLE | Backup 2 (Failover if Gemini pool exhausted). |
| Root Codex | Standard Codex | N/A | PROHIBITED | Coordinator only, implementation prohibited. |
| Root Codex | Spark | UNKNOWN | UNKNOWN | Not used. |

## Rationale
This allocation minimizes scarce-quota use by utilizing the healthy Antigravity Gemini pool for primary implementation, reserving the Claude/GPT pool for independent review. 

## Codex Implementation Status
- **Standard Codex**: PROHIBITED
- **GPT-5.3-Codex-Spark**: UNKNOWN / NOT USED
