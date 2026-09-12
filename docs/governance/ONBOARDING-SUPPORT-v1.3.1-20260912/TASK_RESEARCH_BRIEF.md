# TASK_RESEARCH_BRIEF: ONBOARDING-SUPPORT-v1.3.1-20260912

## Verified Current State
- Repository: `/Users/kuznetcovpavel/max/rech-v-tekst-speaker-identity-v1_3-20260911`
- Base commit: `9b6c7b73957ab39a1d423be7765c91e4661bab2d` (clean remote main).
- UI Architecture: Python backend (Flask/webview) with HTML/JS/CSS frontend in `static/`.
- Missing: No first-run onboarding checklist, missing user guide integration, and no bug report flow.

## Measurable Acceptance Criteria
- Start screen prominently displays primary action «Загрузить аудио или видео».
- First-run onboarding checklist is visible (covers file/source, language, microphone/BlackHole status, transcription start).
- Microphone and BlackHole status include clear human instructions without programmatically altering macOS settings.
- Loading, server-error, and retry states are implemented.
- Russian tooltips are added for every meaningful button.
- Dismissible first-run help with persistent setting «Показать подсказки» is present.
- `docs/USER_GUIDE_RU.md` is complete (covers first launch, mic permission, BlackHole setup, etc.).
- UI «Инструкция» panel shares content with `docs/USER_GUIDE_RU.md`.
- Bug report template exists at `.github/ISSUE_TEMPLATE/bug_report.yml`.
- In-app «Сообщить об ошибке» flow allows copying a prefilled template and opening GitHub issues (no automatic upload of recordings, transcripts, or logs).
- Translation and long-call test features remain unchanged (explicitly excluded).

## Implementation Options

### Option 1: Native macOS UI extensions
Implement the onboarding checklist and bug report flow using native Python UI dialogs (e.g., PySide/Tkinter/AppKit).
- **Correctness**: High, but splits UI logic.
- **Risk**: High (architectural mismatch, breaks existing webview pattern).
- **Reversibility**: Hard to cleanly remove.
- **Time/Ops**: Very high, requires new dependencies.

### Option 2: Web-based HTML/JS Integration (Selected)
Enhance the existing `static/index.html`, `static/app.js`, and `static/app.css` to include the onboarding flow, settings, and bug report modal. Read the markdown user guide dynamically or bundle it for the instruction panel.
- **Correctness**: High, aligns with existing architecture.
- **Risk**: Low, purely frontend addition.
- **Reversibility**: Easy (just revert HTML/JS additions).
- **Time/Ops**: Low, utilizes existing webview setup.
- **Expected Quota**: Low/Medium.

**Reason for rejection of Option 1**: Splits the UI into two different paradigms (web and native), increasing maintenance burden and breaking the current consistent architecture.

## Dependencies, Unknowns, and Privacy Risks
- **Privacy Risk**: Bug reports could inadvertently contain sensitive user audio or transcripts.
- **Mitigation**: The bug report flow will explicitly require the user to copy/paste the template. No automatic upload of logs or recordings will occur.
- **Unknowns**: Best method to synchronize `docs/USER_GUIDE_RU.md` with the UI. Will likely load it via a frontend fetch or inline it during build. No programmatic macOS settings changes are permitted for mic/BlackHole permissions.

## Minimal Vertical Slice & Milestone Boundaries
1. Create `docs/USER_GUIDE_RU.md` and `.github/ISSUE_TEMPLATE/bug_report.yml`.
2. Add onboarding HTML/CSS to `static/index.html` and logic in `static/app.js`.
3. Implement the bug report copy-to-clipboard flow.
4. (Translation and long-call tests are explicitly excluded).

## Exact Owned Paths for One Writer
- `static/index.html`
- `static/app.js`
- `static/app.css`
- `docs/USER_GUIDE_RU.md`
- `.github/ISSUE_TEMPLATE/bug_report.yml`

## Verification Plan
- **Commands**: Build via `./build.sh` or run locally via `python macos_app.py`.
- **Evidence**: Provide real UI screenshots of the start screen with the checklist, the «Инструкция» panel, and the «Сообщить об ошибке» modal.
- **Rollback Plan**: `git revert` the implementation commit if failures occur.
- **Blockers**: None at this stage.
