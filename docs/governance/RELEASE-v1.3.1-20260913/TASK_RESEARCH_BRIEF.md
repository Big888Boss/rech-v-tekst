# TASK_RESEARCH_BRIEF

## Intended User-Visible Outcome
Release version 1.3.1 of the macOS application ("Речь в текст") containing the new onboarding UI, user guide, and error report integration (from branch `feature/onboarding-docs-error-report-v1.3.1-20260912`), locally and on GitHub. Version 1.3.1 must be visible everywhere in the UI and metadata. 

## Measurable Acceptance Criteria
- Version `1.3.1` is reflected everywhere user-visible. Exact owned version files: `build.spec` (`CFBundleVersion`, `CFBundleShortVersionString`), and `.github/ISSUE_TEMPLATE/bug_report.yml`.
- Tests run clean. The exact 12-test batch is: `PYTHONPATH=. .venv/bin/pytest tests/test_layout_and_tooltips_e2e.py tests/test_onboarding_ux.py -p no:cacheprovider -q`.
- Build (`build.sh`) runs clean.
- App signed/notarized status is honestly recorded. Ad-hoc signed and not notarized, Gatekeeper warnings disclosed.
- Release artifact name: `Rech-v-tekst-v1.3.1.zip` created using `ditto`, with output artifact checksum (SHA256) recorded and verified.
- The exact reviewed lineage is pushed to `main` without history loss.
- A public annotated GitHub tag (`v1.3.1`) is created targeting the accepted `main` commit.
- Public page and asset download are verified.
- The previous installation is preserved as a unique, non-colliding rollback backup (e.g. `v1.3.0.backup.app`). No destructive deletions are used.
- The candidate is copied (`cp -a` or `ditto`) into `/Applications` only after packaging and inspection. The `dist` candidate is left intact.
- The native app opens successfully, and UI components are verified without processing a real file.

## Current State
- Repository: `/Users/kuznetcovpavel/max/rech-v-tekst-speaker-identity-v1_3-20260911`
- Active branch: `feature/onboarding-docs-error-report-v1.3.1-20260912`
- `build.spec` contains `1.3.0` metadata.
- `gh` CLI is missing. We will use existing `git` credentials to push, and the authenticated GitHub Web UI for the final release creation.

## Selected Implementation Option
**Merge to main, build locally, test, package, install via safe copy, push tag, use GitHub Web UI for release.**

### Exact Order of Operations (with Checkpoints & Rollbacks)
1. **Verify clean HEAD and execute tests:**
   - Run exact 12-test batch: `PYTHONPATH=. .venv/bin/pytest tests/test_layout_and_tooltips_e2e.py tests/test_onboarding_ux.py -p no:cacheprovider -q`
   - Run broader suite after feasibility inspection.
   - *Failure Rollback:* Stop process.
2. **Bump version & commit:** Edit `build.spec` and `.github/ISSUE_TEMPLATE/bug_report.yml`, then commit. 
   - *Failure Rollback:* `git reset --hard HEAD`.
3. **Build:** Execute `./build.sh`.
   - *Failure Rollback:* Stop process.
4. **Inspect build, signature, and metadata:**
   - `defaults read "$PWD/dist/Речь в текст.app/Contents/Info.plist" CFBundleShortVersionString` (expect 1.3.1)
   - `defaults read "$PWD/dist/Речь в текст.app/Contents/Info.plist" CFBundleVersion` (expect 1.3.1)
   - `codesign --verify --deep --strict "dist/Речь в текст.app"`
   - `codesign -dv "dist/Речь в текст.app"`
   - `xattr -lr "dist/Речь в текст.app"` (Gatekeeper assessment: informative only, do not present as a signed distribution).
   - Check bundle guide: `ls "dist/Речь в текст.app/Contents/Resources/docs/USER_GUIDE_RU.md"` (or equivalent path depending on internal bundle).
   - *Failure Rollback:* Stop process, fix build scripts.
5. **Package and Checksum:**
   - `ditto -c -k --keepParent "dist/Речь в текст.app" "dist/Rech-v-tekst-v1.3.1.zip"`
   - `shasum -a 256 "dist/Rech-v-tekst-v1.3.1.zip" > "dist/Rech-v-tekst-v1.3.1.zip.sha256"`
   - `unzip -t "dist/Rech-v-tekst-v1.3.1.zip"`
   - `shasum -c "dist/Rech-v-tekst-v1.3.1.zip.sha256"`
6. **Stage backup, install candidate, and smoke test:**
   - **Backup Algorithm:** Determine current app version (e.g. `1.3.0`). Set `BACKUP_PATH="/Applications/Речь в текст.v1.3.0.backup.app"`. Test if `BACKUP_PATH` exists. If it exists, append a timestamp (e.g., `...backup_20260913_120000.app`). Never overwrite existing backups. Record chosen path in evidence.
   - Move existing: `mv "/Applications/Речь в текст.app" "$BACKUP_PATH"`
   - **Safe Install:** Copy candidate: `cp -a "dist/Речь в текст.app" "/Applications/"`
   - **Native Smoke:** Open app, verify UI visually, capture evidence.
   - *Failure Rollback:* `mv "/Applications/Речь в текст.app" "/Applications/Речь в текст.candidate-failed-$(date +%s).app" && mv "$BACKUP_PATH" "/Applications/Речь в текст.app"`.
7. **Update reports:** Finalize `QUALITY_REPORT.md` and `COMPLETION_REPORT.md`.
8. **Integrate to main:** `git checkout main && git merge feature/onboarding-docs-error-report-v1.3.1-20260912`.
9. **Tag exact accepted main commit:** `git tag -a v1.3.1 -m "Release v1.3.1"`.
10. **Push:** `git push origin main v1.3.1`.
11. **Create release & upload assets (Public UI Boundary):**
   - The operator logs into GitHub Web UI at `https://github.com/Big888Boss/rech-v-tekst/releases/new?tag=v1.3.1`.
   - Set Title: "Release v1.3.1".
   - Paste expanded Russian Notes (see below).
   - Drag and drop `Rech-v-tekst-v1.3.1.zip` and `.sha256`.
   - **BOUNDARY STOP:** Stop immediately before clicking the final public "Publish release" button. Wait for the required action-time confirmation from the operator.
12. **Verify:** After confirmation, publish and verify public page, asset download, and checksum.

## Release Notes (Russian)
Версия 1.3.1:
- Добавлена загрузка файлов (file upload).
- Добавлен чек-лист микрофона/BlackHole при первом запуске.
- Встроена офлайн-документация пользователя.
- Добавлены всплывающие подсказки кнопок.
- Внедрен процесс создания отчетов об ошибках (bug-report workflow).
- Полная конфиденциальность локальных данных (local-data privacy).
- Системные требования macOS и инструкция по установке.
- **Внимание (Gatekeeper):** Приложение подписано локально (ad-hoc) и не прошло нотаризацию Apple. При первом запуске потребуется подтверждение в настройках безопасности macOS.
- Интегрированы тесты пользовательского интерфейса, контрольные суммы (SHA256) проверены. Известные границы нативного тестирования соблюдены.

## File/Subsystem Ownership Map (Next Milestone)
- **Branch:** `main`
- **Version Sources:** `build.spec`, `.github/ISSUE_TEMPLATE/bug_report.yml`.
- **Governance:** `docs/governance/RELEASE-v1.3.1-20260913/**`.
- **Generated Assets:** `dist/Rech-v-tekst-v1.3.1.zip` and `.sha256`. Do not commit `dist` to git.
- **Deployment target:** `/Applications/Речь в текст.app`.
