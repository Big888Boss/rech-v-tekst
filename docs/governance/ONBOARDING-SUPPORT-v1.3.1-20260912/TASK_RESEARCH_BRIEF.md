# TASK_RESEARCH_BRIEF: ONBOARDING-SUPPORT-v1.3.1-20260912

## Verified Current State
- Repository: `/Users/kuznetcovpavel/max/rech-v-tekst-speaker-identity-v1_3-20260911`
- Base commit: `9b6c7b73957ab39a1d423be7765c91e4661bab2d` (clean remote main).
- UI Architecture: Python backend (Flask/webview) with HTML/JS/CSS frontend in `static/`.
- Missing: No first-run onboarding checklist, missing user guide integration, and no bug report flow.

## Measurable Acceptance Criteria
- Start screen prominently displays primary action «Загрузить аудио или видео» (always-visible).
- First-run onboarding checklist is visible (covers file/source, language, microphone/BlackHole status, transcription start) and can be dismissed. It can be reopened via a persistent «Показать подсказки» setting across relaunches (saved in localStorage).
- Microphone and BlackHole status in the checklist correctly reflect actual macOS state without false positives or negatives, and include clear human instructions without programmatically altering macOS settings.
- Loading, server-error, and retry states are gracefully handled.
- Russian tooltips (`data-tooltip`) are added for every meaningful button (testable on 'Загрузить аудио', 'Сообщить об ошибке', etc.).
- Complete canonical guide (`docs/USER_GUIDE_RU.md`) is rendered inline in the instruction panel and works fully offline.
- Safe bug-report copy flow is implemented, linking exactly to `https://github.com/OWNER/REPO/issues/new`.
- No automatic upload of sensitive data (logs, audio, transcripts) occurs.
- Translation and long-call behaviors remain completely unchanged.
- README.md includes a link to the user guide.
- Packaged macOS app successfully includes `docs/` and serves it offline.

## Implementation Options

### Option 1: Native macOS UI extensions
Implement the onboarding checklist and bug report flow using native Python UI dialogs (e.g., PySide/Tkinter/AppKit).

### Option 2: Web-based HTML/JS Integration (Selected)
Enhance the existing `static/index.html`, `static/app.js`, and `static/app.css` to include the onboarding flow, settings persistence, and bug report modal. 
**Chosen Synchronization Design:** Use `docs/USER_GUIDE_RU.md` as the single canonical source. To ensure it works inside the packaged macOS app offline, `docs/` will be added to the `datas` array in `build.spec`, and a `/docs/` route will be added to `recorder/http_server.py`'s `do_GET` handler. The frontend will fetch `/docs/USER_GUIDE_RU.md` and render it into the «Инструкция» panel.

**Reason for rejection of Option 1**: Splits the UI into two different paradigms (web and native), increasing maintenance burden and breaking the current consistent architecture.

## Dependencies, Unknowns, and Privacy Risks
- **Privacy Risk**: Bug reports could inadvertently contain sensitive user audio or transcripts.
- **Mitigation**: The bug report flow will explicitly require the user to copy/paste the template. No automatic upload of logs or recordings will occur.

## Map of Real Current Controls
- **Already present and reused:**
  - Upload: `btnQuickUpload`, `audioFileInput`, `btnUploadSubmit`, `uploadContainer`
  - Recording: `btnPrimaryStart`, `btnStopCapture`
  - Language: `languageSelect`
  - Mic/BlackHole permissions: Handled via `loadPreflight()` deriving `lastPermissions`, `lastPermissionsDevice`, `isBlackHoleDevice`, and `isBlackHoleMissing`.
  - Server health: Handled via `connectionBanner`, `apiGet`, `apiPost`, watchdog.
  - Queue deletion: `btnConfirmQueueRemove`, `queueConfirmModal`.
  - Exports: `exportDropdown`, `exportTxtLink`, `exportMdLink`, `exportSrtLink`, `exportVttLink`, `exportJsonLink`.
  - Diarization: `diarizationEnabledCheck`, `btnDiarizeSession`, `btnCancelDiarization`.
  - Speaker rename/reset: `speakerLegendContainer`.
- **Must be added:**
  - Settings persistence for «Показать подсказки» using `localStorage`.
  - Bug report copy-to-clipboard button and modal.
  - Onboarding checklist HTML elements and logic.
  - `docs/USER_GUIDE_RU.md` offline serving logic (`build.spec`, `http_server.py`).

## Exact Owned Paths for One Writer
- `static/index.html`
- `static/app.js`
- `static/app.css`
- `docs/USER_GUIDE_RU.md`
- `recorder/http_server.py`
- `build.spec`
- `.github/ISSUE_TEMPLATE/bug_report.yml`
- `README.md`

## Verification Plan
- **Commands**: 
  - `python3 macos_app.py` (Run locally)
  - `./build.sh` (Build packaged app)
  - `open dist/Речь\ в\ текст.app` (Run packaged app)
- **Evidence Matrix (Real Screenshots/Video)**: 
  - Initial screen showing onboarding checklist.
  - Checklist dismissed, then reopened via "Показать подсказки".
  - Instruction panel showing inline rendered markdown guide offline.
  - Bug-report modal with prefilled template.
  - Server-error/retry state (simulate backend disconnect).
  - Microphone/BlackHole detection states.
  - Tooltip sampling (hover on 'Загрузить аудио', 'Сообщить об ошибке').
  - Keyboard focus trap, Escape to close, and scroll behavior on the bug report modal.
- **Rollback Plan**: `git reset --hard 9b6c7b73957ab39a1d423be7765c91e4661bab2d`
- **Version Boundaries**: v1.3.1 candidate. Publication and installation are explicitly excluded until independent review passes.
- **Blockers**: None.
