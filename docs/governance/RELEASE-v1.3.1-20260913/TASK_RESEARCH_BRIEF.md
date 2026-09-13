# TASK_RESEARCH_BRIEF

## Intended User-Visible Outcome
Release version 1.3.1 of the macOS application ("Речь в текст") containing the new onboarding UI, user guide, and error report integration (from branch `feature/onboarding-docs-error-report-v1.3.1-20260912`), locally and on GitHub. Version 1.3.1 must be visible everywhere in the UI and metadata. 

## Measurable Acceptance Criteria
- Version `1.3.1` is reflected everywhere user-visible (`build.spec`, UI, etc.).
- Tests (`pytest`) and build (`build.sh`) run clean.
- App signed/notarized status is honestly recorded (currently unsigned or ad-hoc signed locally).
- Release notes published in Russian.
- Output artifact checksum (SHA256) is recorded.
- The exact reviewed lineage (`feature/onboarding-docs-error-report-v1.3.1-20260912`) is pushed to `main` without history loss.
- A public GitHub tag/release (`v1.3.1`) is created with the `Речь в текст.app.zip` (or similar) asset uploaded.
- Public page and asset download are verified.
- The previous installation is preserved as a rollback backup (`/Applications/Речь в текст.v1.2.backup.app`).
- The candidate is installed only after packaging verification.
- The native app opens successfully, and the onboarding/upload/help/error-report UI components are verified without processing a real file.
- The app is left running and ready for the user's test.

## Current State
- Repository: `/Users/kuznetcovpavel/max/rech-v-tekst-speaker-identity-v1_3-20260911`
- Active branch: `feature/onboarding-docs-error-report-v1.3.1-20260912` (HEAD: `5c8c1fc61e83ab561aa3908efd4bd6fd18b0de21`)
- Main branch is behind HEAD, so this branch can cleanly merge into it.
- `build.spec` contains `1.3.0` metadata which needs bumping to `1.3.1`.
- Tests run cleanly in previous step (173 tests, 0 net new failures).
- `gh` CLI is currently not found on PATH or not installed; we'll need to use standard `git push --tags` and manual or automated GH API, or instruct the user/agent to install/use `gh`.
- We currently ad-hoc sign or do not sign the app; notarization is not set up, which must be honestly documented.

## Implementation Options

**Option 1: Merge to main, build locally, release via standard git push and GitHub UI**
- **Correctness:** High. Ensures linear history.
- **Risk:** Low. If build fails, we don't push the tag.
- **Reversibility:** High. Tags can be deleted, local app can be uninstalled.
- **Time:** Fast.
- **Operations Burden:** Moderate. Requires manual or API-based release creation if `gh` CLI is missing.
- **Quota:** Low. 

**Option 2: Release directly from feature branch, relying on CI/CD (GitHub Actions) for build**
- **Correctness:** Moderate. The prompt says "build locally and install", relying on CI/CD violates the local installation requirement unless we download the artifact.
- **Risk:** Higher, relies on remote infrastructure that might not be configured for this macOS app.
- **Reversibility:** Moderate. 
- **Time:** Slower. 
- **Operations Burden:** High. Requires setting up macOS runners and secrets for signing.
- **Quota:** High.

## Selected Option: Option 1
Option 1 is selected because it strictly adheres to local build verification, local rollback, and testing before pushing the release tag, without relying on unconfigured CI/CD. 

### Minimal Vertical Slice & Milestone Boundaries
1. Update version to 1.3.1 in `build.spec`, UI, etc.
2. Run tests.
3. Merge `feature/onboarding-docs-error-report-v1.3.1-20260912` to `main`.
4. Build `Речь в текст.app` locally.
5. Create `.zip` and compute SHA256.
6. Backup old app in `/Applications`.
7. Install new app to `/Applications`.
8. Tag and push `main` to GitHub.
9. Create GitHub release (using API or user-provided `gh` tool) and upload asset.
10. Launch app and verify UI visually.

## File/Subsystem Ownership Map
- Version Metadata: `build.spec`, `ui.py`, `static/index.html` (if version is hardcoded).
- Build/Package scripts: `build.sh`.
- Deployment target: `/Applications/Речь в текст.app`.
- Remote: `origin` (GitHub).

## Verification, Rollback, and Evidence Plan
- **Verification:** Run `pytest`, `shasum -a 256`, and verify UI loads on macOS. Check GitHub releases page.
- **Rollback:** `rm -rf "/Applications/Речь в текст.app" && mv "/Applications/Речь в текст.v1.2.backup.app" "/Applications/Речь в текст.app"`.
- **Evidence:** Screenshots of the UI, terminal output of test and checksum, link to GitHub release.
