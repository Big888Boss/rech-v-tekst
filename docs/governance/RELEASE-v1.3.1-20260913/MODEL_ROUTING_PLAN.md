# MODEL_ROUTING_PLAN

## Overview
This plan defines the model routing for the release, build, publish, and deployment of `v1.3.1`.

## Resource Preflight Snapshot (2026-09-13 America/New_York)
- **Antigravity Gemini Pool:** 94% weekly remaining (reset 4d18h); 98% 5-hour remaining (reset 3h05m).
- **Antigravity Claude/GPT Pool:** 82% weekly remaining (reset 5d08h); 100% 5-hour remaining.
- **Codex Standard:** 82% weekly remaining, 5-hour unavailable; no credits. **(Codex implementation PROHIBITED)**
- **Spark:** Unknown quota. **(Spark PROHIBITED for deployment)**

## Primary Executor
- **Agent/Application:** Antigravity 
- **Model:** Gemini 3.1 Pro (Low)
- **Quota Pool:** Antigravity Gemini Pool
- **Task Fit:** Strong fit for primary implementation, UI visual verification, browser/deployment integration, and local shell execution.
- **Protected Reserve:** 20% weekly / 15% five-hour.
- **Current Load:** Low.
- **Expected Quota Cost:** Low (primarily terminal commands and UI verification).

## Backup Executor / Independent Reviewer
- **Agent/Application:** Antigravity
- **Model:** Claude Sonnet 4.6 Thinking
- **Quota Pool:** Antigravity Claude/GPT Pool (Separate from Gemini Pool)
- **Task Fit:** Strong independent reasoning capabilities, ideal for validating deployment correctness, checking scripts, and final quality review.
- **Promotion Trigger:** If Gemini 3.1 Pro reaches reserve floors, hits rate limits, or repeatedly fails to handle packaging/git operations.
- **Notice:** Second independent backup is unavailable because the operator explicitly requires Antigravity. This is noted and accepted per operator instructions.

## Handoff Path
If failover is required, the primary executor will halt, commit any safe atomic state, and document the current branch, changed files, and exact failing step in `ERROR_LOG.md`. The backup will resume from that point.
