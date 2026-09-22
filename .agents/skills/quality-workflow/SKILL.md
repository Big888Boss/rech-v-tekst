---
name: quality-workflow
description: Required engineering workflow for a feature, bug fix, refactor or code review in an adopted project
type: prompt
whenToUse: When asked to implement, fix, refactor, review or finish software changes
---
Read the nearest AGENTS.md, its canonical policy source, and this repository's `.specify/memory/constitution.md`. If the canonical source is unavailable in the runtime, mark `INSTRUCTION_CONTEXT_UNVERIFIED` and ask for a verified adapter before a large milestone.

1. Inspect the relevant existing code, tests, architecture and CI. State the observed behavior, acceptance criteria, owned paths and compatibility boundary. Keep unrelated files untouched.
2. For a notable feature, create or update a short Spec Kit spec, plan and tasks before implementation. For a bug, reproduce and identify cause; add a regression test that demonstrates old failure where practical.
3. Use an isolated branch/worktree with one writer per file. For new logic, write meaningful tests including a boundary/failure case. Observe the failing test before the fix when practical, then implement and rerun it.
4. Run the repository's actual impacted tests, lint/typecheck, build/package check and secret/security scan as applicable. Never weaken or disable a check to make it pass. For UI changes, inspect the main flow in a real browser and record the revision and result.
5. Request independent review of the delivered diff. The author is not sole acceptance authority. Resolve defects and rerun affected checks on the final revision.
6. Return only with changed files/behavior, exact test commands/results, browser/security results where applicable, unknowns and limitations, reviewer verdict and rollback. Say NOT VERIFIED for anything not run. Never claim done from a plan, a clicked link, or a green-looking placeholder.
