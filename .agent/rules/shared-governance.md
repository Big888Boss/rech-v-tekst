---
description: Shared governance and engineering quality; always apply
alwaysApply: true
---

# Always Apply Workspace Rule

Canonical cross-agent policy: `/Users/kuznetcovpavel/max/AGENTS.md`
Policy ID: `multi-agent-governance-2026-09-09.2`

You must read and follow the canonical policy document before acting.
Project: `rech-v-tekst`, speaker identity v1.3. Its product milestone remains on `feature/speaker-identity-v1.3-20260911`. This quality rollout is isolated on `quality/github-main-workflow-20260915`.

## Critical Non-Negotiable Rules
- **Root Codex role:** Only Product Owner/coordinator/reviewer.
- **Implementation:** External executor performs implementation.
- **Mandatory Gates:** Research + live preflight are mandatory before large implementation.
- **Root implementation:** Product code changes by Root are prohibited without declared exception.
- **Reserve floors:** 20% weekly/monthly, 15% 5h/daily; Root 25%; Spark 20% weekly/15% 5h.
- **Model Plan:** Requires primary + two independent backup models.
- **Mandatory Reports:** `ERROR_LOG` and `QUALITY_REPORT` required for every large milestone.
- **Ownership:** One writer per file/subsystem.
- **Handoff:** Executor must return commit/diff/tests/runtime evidence.
- **Release:** Publication/installation/release only by separate explicit instruction.

Read `.specify/memory/constitution.md` for project quality gates. The operator authorized root Codex to make and push these scoped governance changes; this does not authorize product release or deployment.
