# TASK_RESEARCH_BRIEF

## Intended User-Visible Outcome
Release version 1.3.1 of the macOS application ("Речь в текст") containing the new onboarding UI, user guide, and error report integration (from branch `feature/onboarding-docs-error-report-v1.3.1-20260912`), locally and on GitHub. Version 1.3.1 must be visible everywhere in the UI and metadata. 

## Measurable Acceptance Criteria
- Version `1.3.1` is reflected everywhere user-visible. Exact owned version files: `build.spec` (`CFBundleVersion`, `CFBundleShortVersionString`), and `.github/ISSUE_TEMPLATE/bug_report.yml`.
- Tests run clean. Specifically: `pytest tests/test_onboarding_ux.py -v` (the 12-test UI batch) and broader suite `pytest tests/ -v --maxfail=5`. Exit criteria: Exit code 0.
- Build (`build.sh`) runs clean.
- App signed/notarized status is honestly recorded: The app is ad-hoc signed only and not notarized, which will trigger Gatekeeper on fresh installations.
- Release notes published in Russian: "Версия 1.3.1. Добавлен интерфейс онбординга, документация пользователя и встроенная отправка отчетов об ошибках."
- Release artifact name: `Rech-v-tekst-v1.3.1.zip`. Output artifact checksum (SHA256) is recorded.
- The exact reviewed lineage is pushed to `main` without history loss.
- A public annotated GitHub tag (`v1.3.1`) is created targeting the accepted `main` commit.
- Public page and asset download are verified.
- The previous installation is preserved as a rollback backup (e.g. `/Applications/Речь в текст.v1.3.0.backup.app`). No destructive `rm -rf` is used for application bundles.
- The candidate is installed only after packaging verification.
- The native app opens successfully, and the UI components are verified without processing a real file.

## Current State
- Repository: `/Users/kuznetcovpavel/max/rech-v-tekst-speaker-identity-v1_3-20260911`
- Active branch: `feature/onboarding-docs-error-report-v1.3.1-20260912` (HEAD: `5c8c1fc61e83ab561aa3908efd4bd6fd18b0de21`)
- Main branch is behind HEAD, so this branch can cleanly merge into it.
- `build.spec` contains `1.3.0` metadata.
- `gh` CLI is missing. We will use existing `git` credentials to push, and the authenticated GitHub Web UI for the final release creation.

## Selected Implementation Option (Corrected)
**Merge to main, build locally, release via standard git push and GitHub Web UI**
- **Publishing Mechanism:** Push tag via `git push origin v1.3.1` using existing authenticated Git credentials. For the release asset and notes, the operator boundary is reached: the operator uses their already authenticated GitHub Web UI at `https://github.com/Big888Boss/rech-v-tekst/releases/new?tag=v1.3.1` to paste the Russian notes and upload the ZIP. This avoids exposing tokens or installing new tools.

### Order of Operations (with Rollback at each stage)
1. **Verify clean HEAD and tests:** `pytest tests/test_onboarding_ux.py -v` and `pytest tests/ -v`. (Failure: Stop process).
2. **Bump version & commit:** Edit `build.spec` and `.github/ISSUE_TEMPLATE/bug_report.yml`, then commit. (Failure: `git reset --hard HEAD`).
3. **Build:** Execute `./build.sh`. (Failure: Stop process, no artifact).
4. **Inspect bundle/version/signature:** `codesign -dv "dist/Речь в текст.app"` and `defaults read "$PWD/dist/Речь в текст.app/Contents/Info.plist" CFBundleShortVersionString`. (Failure: Fix build scripts).
5. **Stage backup, install, and smoke test:** 
   - Get current version: `defaults read "/Applications/Речь в текст.app/Contents/Info.plist" CFBundleShortVersionString` (Assume `1.3.0`).
   - Swap: `mv "/Applications/Речь в текст.app" "/Applications/Речь в текст.v1.3.0.backup.app"` and `mv "dist/Речь в текст.app" "/Applications/"`.
   - Open app and verify visually. 
   - (Failure: `mv "/Applications/Речь в текст.app" "/Applications/Речь в текст.candidate-failed.app" && mv "/Applications/Речь в текст.v1.3.0.backup.app" "/Applications/Речь в текст.app"`).
6. **Package/Checksum:** `ditto -c -k --keepParent "dist/Речь в текст.app" "dist/Rech-v-tekst-v1.3.1.zip"` and `shasum -a 256 "dist/Rech-v-tekst-v1.3.1.zip" > "dist/Rech-v-tekst-v1.3.1.zip.sha256"`.
7. **Update reports:** Finalize `QUALITY_REPORT.md` and `COMPLETION_REPORT.md`.
8. **Integrate to main:** `git checkout main && git merge feature/onboarding-docs-error-report-v1.3.1-20260912`. (Failure: `git merge --abort`).
9. **Tag exact accepted main commit:** `git tag -a v1.3.1 -m "Release v1.3.1"`.
10. **Push:** `git push origin main v1.3.1`. (Failure: `git tag -d v1.3.1`).
11. **Create release & upload assets:** Hand off to operator boundary to use GitHub Web UI.
12. **Verify public page and download.**

## File/Subsystem Ownership Map (Next Milestone)
- **Branch:** `main`
- **Version Sources:** `build.spec`, `.github/ISSUE_TEMPLATE/bug_report.yml`.
- **Release Notes / Governance Artifacts:** `docs/governance/RELEASE-v1.3.1-20260913/**`.
- **Generated Assets:** `dist/Rech-v-tekst-v1.3.1.zip` and `.sha256`. *Do not commit `dist` artifacts to the repository per policy.*
- **Deployment target:** `/Applications/Речь в текст.app`.
