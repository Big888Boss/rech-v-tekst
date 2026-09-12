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
- Russian tooltips (`data-tooltip`) are added for EVERY meaningful button (see Control Inventory below).
- Complete canonical guide (`docs/USER_GUIDE_RU.md`) is rendered inline in the instruction panel and works fully offline. Markdown rendering must be safe: escape raw HTML and allowlist only safe tags (headings, paragraphs, lists, inline code, links restricted to http/https targets). No raw `innerHTML` injection of unescaped content.
- Safe bug-report copy flow is implemented, linking exactly to `https://github.com/Big888Boss/rech-v-tekst/issues/new?template=bug_report.yml`.
- No automatic upload of sensitive data (logs, audio, transcripts) occurs.
- Translation and long-call behaviors remain completely unchanged.
- README.md includes a link to the user guide.
- Packaged macOS app successfully includes `docs/` and serves it offline.

## Implementation Options
### Option 2: Web-based HTML/JS Integration (Selected)
Enhance the existing `static/index.html`, `static/app.js`, and `static/app.css` to include the onboarding flow, settings persistence, and bug report modal.
**Chosen Synchronization Design:** Use `docs/USER_GUIDE_RU.md` as the single canonical source. To ensure it works inside the packaged macOS app offline, `docs/` will be added to the `datas` array in `build.spec`, and a `/docs/` route will be added to `recorder/http_server.py`'s `do_GET` handler. The frontend will fetch `/docs/USER_GUIDE_RU.md` and render it safely into the «Инструкция» panel.

## Control Inventory for Tooltips
Every meaningful button/control must have a Russian tooltip. This includes existing and new controls:
- **Main Actions:** `btnPrimaryStart`, `btnQuickUpload`, `btnPreflightTest`, `btnUploadSubmit`, `btnUploadRetry`, `btnStopCapture`, `btnCancelProcess`, `btnCancelDiarization`.
- **Navigation/Panels:** `btnOpenSettings`, `btnToggleHelp`, `btnOpenFeaturesDoc`, `btnToggleHintsMode`, `btnDownloadFeaturesDoc`, `btnOpenFeaturesDocPanel`, `btnCloseHelpPanel`, `btnRetryHelpDoc`, `btnCloseInspector`, `btnClearLog`.
- **Table Controls:** Sort buttons (Session, Source, Date, Duration, Status), Pagination (`btnPrevPage`, `btnNextPage`), dynamic row actions (Inspect, Retry, Queue Remove, Queue Restore, Process).
- **Inspector/Export:** `btnExportMenu`, Export links (TXT, MD, SRT, VTT, JSON), `btnDiarizeSession`, `btnRequestSummary`, Tabs (`tabTranscript`, `tabSummary`).
- **Modals:** `btnCancelSummary`, `btnSubmitSummary`, `btnCancelQueueRemove`, `btnConfirmQueueRemove`, `btnCloseSettingsModal`, `btnCancelSettings`, `btnSaveSettings`, `btnRecheckSettings`, `btnStartInstall`, `btnStartDiarizationInstall`.
- **New Onboarding/Bug Report Controls:** Onboarding dismiss button, 'Сообщить об ошибке' button, Bug report modal close button, Copy template button, Open GitHub Issue button.

## Exact Owned Paths for One Writer
- `static/index.html`
- `static/app.js`
- `static/app.css`
- `docs/USER_GUIDE_RU.md`
- `recorder/http_server.py`
- `build.spec`
- `.github/ISSUE_TEMPLATE/bug_report.yml`
- `README.md`
- `tests/test_onboarding_ux.py` (New focused test file)

## Verification Plan
- **Automated Tests**: While `tests/test_layout_and_tooltips_e2e.py` exists, it does not explicitly cover the new onboarding sequence. Therefore, no fully suitable automated test currently exists for the onboarding UI. A new focused test `tests/test_onboarding_ux.py` will be created to verify the onboarding checklist, safe markdown rendering, and tooltip presence.
  - **Commands**:
    `pytest tests/test_layout_and_tooltips_e2e.py`
    `pytest tests/test_onboarding_ux.py`
  - **Expected**: All tests pass (Success).
- **Evidence Matrix (Real Screenshots/Video)**:
  - Initial screen showing onboarding checklist.
  - Checklist dismissed, then reopened via "Показать подсказки" (localStorage).
  - Instruction panel showing inline rendered markdown guide offline (safe rendering).
  - Bug-report modal with prefilled template.
  - Server-error/retry state (simulate backend disconnect).
  - Microphone/BlackHole detection states toggled.
  - Tooltip sampling (hover on 'Загрузить аудио', 'Сообщить об ошибке').
  - Keyboard focus trap, Escape to close, and scroll behavior on the bug report modal.
- **Rollback Plan**: In case of failure, execute an atomic `git revert <implementation-commit-sha>` to return to accepted base `9b6c7b73957ab39a1d423be7765c91e4661bab2d` without discarding local work or history.
- **Version Boundaries**: v1.3.1 candidate. Publication and installation are explicitly excluded until independent review passes.
