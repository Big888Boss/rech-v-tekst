# Project quality constitution
Version 1.0.0 | Ratified 2026-09-15

Read the canonical policy referenced in AGENTS.md. If that source is unavailable on this host, mark `INSTRUCTION_CONTEXT_UNVERIFIED` before a large milestone. The canonical policy governs research, branch isolation, delegation, quotas and evidence. This project constitution adds engineering quality and repository-specific verification.

Current native checks: unittest discovery for recorder behavior; Python syntax; macOS package smoke when applicable; browser UI check for UI changes.
A notable feature must document acceptance criteria, compatibility boundary and owned paths before coding. Keep the existing implementation as source of truth. A bug fix must include a practical failing-then-passing regression test. No test may be disabled for green CI. Security-sensitive changes need focused review. UI changes need browser evidence. Handoff names changed paths, tests actually run, unknowns and rollback.
