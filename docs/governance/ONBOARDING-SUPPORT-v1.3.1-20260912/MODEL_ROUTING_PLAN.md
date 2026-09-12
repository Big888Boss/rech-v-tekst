# MODEL_ROUTING_PLAN: ONBOARDING-SUPPORT-v1.3.1-20260912

## Primary Executor
- **Agent/Model**: Antigravity / Gemini 3.1 Pro Low (Antigravity Gemini pool)
- **Task Fit**: Excellent for web UI (HTML/JS/CSS) additions and documentation writing.
- **Required Tools/Access**: File read/write in repository.
- **Live Quota**: Weekly 95% remaining, 5-hour 99% remaining.
- **Current Load**: AVAILABLE.
- **Expected Cost**: Medium.
- **Condition for Backup Promotion**: Fallback if Gemini 3.1 Pro Low encounters quota exhaustion or capability failure on complex UI state management.

## Reviewer
- **Agent/Model**: Antigravity / Claude Sonnet 4.6 Thinking (Antigravity Claude/GPT pool)
- **Role**: Independent reviewer from a separate quota pool.
- **Live Quota**: Weekly 89% remaining, 5-hour 100% remaining.

## Ordered Backups
**Note:** There is no verified third independent external pool for this milestone. Backups listed share pools with the primary or reviewer.
1. **Backup 1**: Gemini 3.8 Flash Medium
   - **Pool**: Same Antigravity Gemini pool as Primary.
   - **Condition**: Use if 3.1 Pro Low fails but Gemini pool quota is still healthy (degraded reasoning).
2. **Backup 2**: GPT-OSS 120B Medium
   - **Pool**: Same Antigravity Claude/GPT pool as Reviewer.
   - **Condition**: Use if Gemini pool is exhausted. (Warning: This compromises reviewer independence as it shares the Claude/GPT pool).

## Missing Capability Constraint
No verified third independent external pool exists for this milestone. This constraint is recorded and acknowledged.

## Root Codex
- Standard pool: PROHIBITED for implementation.
- Spark pool: UNKNOWN / NOT USED.
