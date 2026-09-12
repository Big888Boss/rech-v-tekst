/**
 * Webinar Recorder Client Application
 * Strictly complies with Apple Design UTILITY specifications:
 * - Non-disruptive polling without stealing focus or resetting user filters (DSH-006, CMP-020)
 * - Two-channel status representation (A11Y-005)
 * - Accessible dialogs with focus trap and safe fallback return (CMP-030, A11Y-007)
 * - State matrix handling: loading, empty, error, success, offline, stale (A11Y-010)
 * - Centralized computed action availability gated on initial load (R03, CMP-011)
 * - AbortController watchdog covering full request and body parsing (R04)
 * - Dynamic sort indicators with synchronized ARIA (R05, CMP-020)
 * - Respect for prefers-reduced-motion (R07, MOT-004)
 * - Preflight generation tracking preventing stale device/source races (R01)
 * - In-memory cached search for transcripts (R10, CMP-011)
 * - Exception-first unified dataset with fixed severity sorting (R11, CMP-001, DSH-007)
 * - Live recording duration and adjacent help messages for Stop/Cancel (GAP-PROGRESS)
 */

(function () {
  'use strict';

  // Core State
  let csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
  let activeSessionId = null;
  let activeOperationKind = null; // 'recording', 'transcribing', or null
  let inspectedSessionId = null;
  let inspectedSessionData = null; // In-memory cache: { manifest, segments, transcript, summary }
  let pollIntervalId = null;
  let isPollInFlight = false;
  let lastSuccessfulPollTime = Date.now();
  let currentSortField = 'created_at';
  let currentSortDirection = 'desc'; // 'asc' or 'desc'
  let cachedSessions = [];
  let isRequestInProgress = false;
  let isOnline = true;
  let initialDataLoaded = false;
  let preflightGeneration = 0;
  let lastPermissions = 'unknown';
  let lastPermissionsDevice = null;
  let recordingStartTime = null;
  let serverTools = { ffmpeg: null, ffprobe: null };
  let lastPreflightData = null;
  let lastPreflightSourceKind = null;

  // Pagination state (CMP-020, DSH-007)
  let currentPage = 1;
  const pageSize = 25;

  // DOM Elements
  const el = (id) => document.getElementById(id);
  const elements = {
    systemStatusBadge: el('systemStatusBadge'),
    systemStatusText: el('systemStatusText'),
    lastSyncTime: el('lastSyncTime'),
    btnPrimaryStart: el('btnPrimaryStart'),
    btnPrimaryStartLabel: el('btnPrimaryStartLabel'),
    connectionBanner: el('connectionBanner'),
    connectionBannerText: el('connectionBannerText'),
    diskFreeVal: el('diskFreeVal'),
    diskHoursVal: el('diskHoursVal'),
    sourceSelect: el('sourceSelect'),
    deviceSelect: el('deviceSelect'),
    deviceSelectGroup: el('deviceSelectGroup'),
    sessionTitleInput: el('sessionTitleInput'),
    languageSelect: el('languageSelect'),
    diarizationEnabledCheck: el('diarizationEnabledCheck'),
    diarizationSpeakersSelect: el('diarizationSpeakersSelect'),
    btnPreflightTest: el('btnPreflightTest'),
    preflightResult: el('preflightResult'),
    preflightSymbol: el('preflightSymbol'),
    preflightMessage: el('preflightMessage'),
    uploadContainer: el('uploadContainer'),
    uploadDependencyWarning: el('uploadDependencyWarning'),
    uploadDependencyMessage: el('uploadDependencyMessage'),
    uploadDependencyRemediation: el('uploadDependencyRemediation'),
    audioFileInput: el('audioFileInput'),
    uploadProgressContainer: el('uploadProgressContainer'),
    uploadProgressBar: el('uploadProgressBar'),
    uploadProgressText: el('uploadProgressText'),
    uploadError: el('uploadError'),
    btnUploadSubmit: el('btnUploadSubmit'),
    btnQuickUpload: el('btnQuickUpload'),
    uploadRetryContainer: el('uploadRetryContainer'),
    btnUploadRetry: el('btnUploadRetry'),
    activeWorkSection: el('activeWorkSection'),
    btnStopCapture: el('btnStopCapture'),
    btnCancelProcess: el('btnCancelProcess'),
    btnCancelDiarization: el('btnCancelDiarization'),
    activeControlHelp: el('activeControlHelp'),
    activeSessionId: el('activeSessionId'),
    activeElapsed: el('activeElapsed'),
    activeEtaBlock: el('activeEtaBlock'),
    activeEta: el('activeEta'),
    activeProgressBlock: el('activeProgressBlock'),
    activeProgressBar: el('activeProgressBar'),
    activeProgressLabel: el('activeProgressLabel'),
    activeDiarizationProgressBlock: el('activeDiarizationProgressBlock'),
    activeDiarizationProgressBar: el('activeDiarizationProgressBar'),
    activeDiarizationProgressLabel: el('activeDiarizationProgressLabel'),
    sessionFilterInput: el('sessionFilterInput'),
    statusFilterSelect: el('statusFilterSelect'),
    sessionsTable: el('sessionsTable'),
    sessionsTableBody: el('sessionsTableBody'),
    tablePagination: el('tablePagination'),
    paginationSummary: el('paginationSummary'),
    btnPrevPage: el('btnPrevPage'),
    btnNextPage: el('btnNextPage'),
    pageIndicator: el('pageIndicator'),
    inspectorSection: el('inspectorSection'),
    inspectorSessionTitle: el('inspectorSessionTitle'),
    inspectorWordCount: el('inspectorWordCount'),
    dropdownWrapper: document.querySelector('.dropdown-wrapper'),
    btnExportMenu: el('btnExportMenu'),
    exportDropdown: el('exportDropdown'),
    exportTxtLink: el('exportTxtLink'),
    exportMdLink: el('exportMdLink'),
    exportSrtLink: el('exportSrtLink'),
    exportVttLink: el('exportVttLink'),
    exportJsonLink: el('exportJsonLink'),
    btnDiarizeSession: el('btnDiarizeSession'),
    btnRequestSummary: el('btnRequestSummary'),
    tabTranscript: el('tabTranscript'),
    tabSummary: el('tabSummary'),
    panelTranscript: el('panelTranscript'),
    panelSummary: el('panelSummary'),
    transcriptSearchInput: el('transcriptSearchInput'),
    speakerLegendContainer: el('speakerLegendContainer'),
    transcriptView: el('transcriptView'),
    summaryView: el('summaryView'),
    logConsole: el('logConsole'),
    btnClearLog: el('btnClearLog'),
    summaryModal: el('summaryModal'),
    summaryForm: el('summaryForm'),
    summaryTemplateSelect: el('summaryTemplateSelect'),
    summaryProviderSelect: el('summaryProviderSelect'),
    summaryOptInCheck: el('summaryOptInCheck'),
    btnCancelSummary: el('btnCancelSummary'),
    btnSubmitSummary: el('btnSubmitSummary'),
    btnToggleHelp: el('btnToggleHelp'),
    btnCloseHelpPanel: el('btnCloseHelpPanel'),
    helpPanel: el('helpPanel'),
    helpDocContainer: el('helpDocContainer'),
    helpDocLoading: el('helpDocLoading'),
    helpDocError: el('helpDocError'),
    helpDocErrorText: el('helpDocErrorText'),
    btnRetryHelpDoc: el('btnRetryHelpDoc'),
    helpDocContent: el('helpDocContent'),
    btnDownloadFeaturesDoc: el('btnDownloadFeaturesDoc'),
    btnOpenFeaturesDoc: el('btnOpenFeaturesDoc'),
    btnToggleHintsMode: el('btnToggleHintsMode'),
    btnToggleHintsModeText: el('btnToggleHintsModeText'),
    btnCloseInspector: el('btnCloseInspector'),
    appGlobalTooltip: el('appGlobalTooltip'),
    dialogTooltip: el('dialogTooltip'),
    whisperModelWarning: el('whisperModelWarning'),
    whisperModelWarningText: el('whisperModelWarningText'),
    inspectorErrorBanner: el('inspectorErrorBanner'),
    inspectorErrorText: el('inspectorErrorText'),
    queueConfirmModal: el('queueConfirmModal'),
    queueConfirmSessionName: el('queueConfirmSessionName'),
    queueConfirmForm: el('queueConfirmForm'),
    btnCancelQueueRemove: el('btnCancelQueueRemove'),
    btnConfirmQueueRemove: el('btnConfirmQueueRemove'),
    queueDialogTooltip: el('queueDialogTooltip'),
    btnHeaderUpload: el('btnHeaderUpload'),
    btnBugReportOpen: el('btnBugReportOpen'),
    bugReportModal: el('bugReportModal'),
    btnBugReportClose: el('btnBugReportClose'),
    btnBugReportCopy: el('btnBugReportCopy'),
    bugReportTemplateContent: el('bugReportTemplateContent'),
    bugReportStatusFeedback: el('bugReportStatusFeedback'),
    onboardingChecklist: el('onboardingChecklist'),
    btnDismissOnboarding: el('btnDismissOnboarding'),
    btnToggleOnboarding: el('btnToggleOnboarding'),
    btnToggleOnboardingText: el('btnToggleOnboardingText'),
    onboardingStatusMsg: el('onboardingStatusMsg'),
    btnOpenSettings: el('btnOpenSettings'),
    settingsModal: el('settingsModal'),
    btnCloseSettingsModal: el('btnCloseSettingsModal'),
    btnCancelSettings: el('btnCancelSettings'),
    btnSaveSettings: el('btnSaveSettings'),
    btnRecheckSettings: el('btnRecheckSettings'),
    btnStartInstall: el('btnStartInstall'),
    settingsForm: el('settingsForm'),
    cardScenarioImport: el('cardScenarioImport'),
    badgeScenarioImport: el('badgeScenarioImport'),
    descScenarioImport: el('descScenarioImport'),
    cardScenarioMic: el('cardScenarioMic'),
    badgeScenarioMic: el('badgeScenarioMic'),
    descScenarioMic: el('descScenarioMic'),
    cardScenarioSystem: el('cardScenarioSystem'),
    badgeScenarioSystem: el('badgeScenarioSystem'),
    descScenarioSystem: el('descScenarioSystem'),
    cardScenarioTranscribe: el('cardScenarioTranscribe'),
    badgeScenarioTranscribe: el('badgeScenarioTranscribe'),
    descScenarioTranscribe: el('descScenarioTranscribe'),
    cardScenarioDiarize: el('cardScenarioDiarize'),
    badgeScenarioDiarize: el('badgeScenarioDiarize'),
    descScenarioDiarize: el('descScenarioDiarize'),
    diarizationInstallerBox: el('diarizationInstallerBox'),
    diarizationInstallerMessage: el('diarizationInstallerMessage'),
    btnStartDiarizationInstall: el('btnStartDiarizationInstall'),
    infoWhisperVersion: el('infoWhisperVersion'),
    infoWhisperPath: el('infoWhisperPath'),
    infoModelPath: el('infoModelPath'),
    infoModelSize: el('infoModelSize'),
    infoModelStatus: el('infoModelStatus'),
    infoDiskSpace: el('infoDiskSpace'),
    installerBox: el('installerBox'),
    installerMessage: el('installerMessage'),
    installerProgressContainer: el('installerProgressContainer'),
    installerProgressBar: el('installerProgressBar'),
    installerProgressPercent: el('installerProgressPercent'),
    installerProgressEta: el('installerProgressEta'),
    inputWhisperBin: el('inputWhisperBin'),
    inputModelPath: el('inputModelPath'),
    selectLanguage: el('selectLanguage'),
    checkGpuEnabled: el('checkGpuEnabled'),
    inputGpuThreads: el('inputGpuThreads'),
    inputCpuThreads: el('inputCpuThreads'),
    envBadgeWhisperBin: el('envBadgeWhisperBin'),
    envBadgeModelPath: el('envBadgeModelPath'),
    envBadgeLanguage: el('envBadgeLanguage'),
    envBadgeNoGpu: el('envBadgeNoGpu'),
    envBadgeThreads: el('envBadgeThreads'),
    envBadgeCpuThreads: el('envBadgeCpuThreads'),
    settingsAlert: el('settingsAlert'),
    settingsAlertText: el('settingsAlertText'),
    settingsDialogTooltip: el('settingsDialogTooltip'),
  };

  // Helper for API calls with watchdog timeout covering full body read (R04)
  async function apiPost(endpoint, data = {}, timeoutMs = 5000) {
    isRequestInProgress = true;
    updateActionStates();
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': csrfToken,
        },
        body: JSON.stringify({ ...data, csrf_token: csrfToken }),
        signal: controller.signal,
      });

      const res = await response.json();
      if (!response.ok) {
        throw new Error(res.error || `HTTP error ${response.status}`);
      }
      return res;
    } finally {
      clearTimeout(timeoutId);
      isRequestInProgress = false;
      updateActionStates();
    }
  }

  async function apiGet(endpoint, timeoutMs = 5000) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

    try {
      const response = await fetch(endpoint, {
        method: 'GET',
        headers: { 'Accept': 'application/json' },
        signal: controller.signal,
      });

      const res = await response.json();
      if (!response.ok) {
        throw new Error(res.error || `HTTP error ${response.status}`);
      }
      return res;
    } finally {
      clearTimeout(timeoutId);
    }
  }

  function isAnyOperationActive() {
    return Boolean(
      isRequestInProgress ||
      activeOperationKind === 'recording' ||
      activeOperationKind === 'transcribing' ||
      activeOperationKind === 'summary' ||
      activeOperationKind === 'install'
    );
  }

  function hasUploadMediaTools() {
    return Boolean(
      serverTools &&
      serverTools.ffmpeg === true && serverTools.ffprobe === true &&
      serverTools.ready !== false
    );
  }

  function isBlackHoleDevice(d) {
    if (!d) return false;
    const kind = d.kind || '';
    if (kind === 'blackhole') return true;
    const lower = (d.name || '').toLowerCase();
    return lower.includes('blackhole');
  }

  function isMicDevice(d) {
    if (!d) return false;
    const kind = d.kind || '';
    if (kind === 'blackhole') return false;
    if (kind === 'mic' || kind === 'builtin_mic' || kind === 'external_mic') return true;
    const lower = (d.name || '').toLowerCase();
    if (lower.includes('blackhole')) return false;
    if (lower.includes('mic') || lower.includes('микрофон') || lower.includes('macbook') || lower.includes('built-in') || lower.includes('встроен')) return true;
    return kind !== 'blackhole';
  }

  // Centralized Computed Action Availability Model (R03, CMP-011, M01)
  function updateActionStates() {
    // Initial snapshot loading: all action mutations disabled until initial data loaded
    if (!initialDataLoaded) {
      const msg = 'Загрузка данных с сервера...';
      elements.btnPrimaryStart.disabled = true;
      elements.btnPrimaryStart.title = msg;
      elements.btnPrimaryStart.setAttribute('data-tooltip-disabled', msg);
      elements.btnPreflightTest.disabled = true;
      elements.btnPreflightTest.title = msg;
      elements.btnPreflightTest.setAttribute('data-tooltip-disabled', msg);
      elements.btnUploadSubmit.disabled = true;
      elements.btnUploadSubmit.title = msg;
      elements.btnUploadSubmit.setAttribute('data-tooltip-disabled', msg);
      if (elements.btnUploadRetry) {
        elements.btnUploadRetry.disabled = true;
        elements.btnUploadRetry.title = msg;
        elements.btnUploadRetry.setAttribute('data-tooltip-disabled', msg);
      }
      elements.btnStopCapture.disabled = true;
      elements.btnCancelProcess.disabled = true;
      elements.btnRequestSummary.disabled = true;
      elements.btnSubmitSummary.disabled = true;
      return;
    }

    const isBusy = isRequestInProgress;
    const isRecording = activeOperationKind === 'recording';
    const isTranscribing = activeOperationKind === 'transcribing';
    const isSummarizing = activeOperationKind === 'summary';
    const isOperating = isAnyOperationActive();
    const hasDevice = elements.deviceSelect && elements.deviceSelect.value !== '';
    const currentSource = elements.sourceSelect ? elements.sourceSelect.value : 'zoom';
    const isUploadSource = currentSource === 'upload';
    const isZoomSource = currentSource === 'zoom';
    const isMicSource = currentSource === 'mic';
    const hasFileSelected = Boolean(elements.audioFileInput && elements.audioFileInput.files?.[0]);

    // Offline mode: disable all mutating operations, preserve reading/filtering/exports
    if (!isOnline) {
      const msg = 'Нет связи с локальным сервером';
      elements.btnPrimaryStart.disabled = true;
      elements.btnPrimaryStart.title = msg;
      elements.btnPrimaryStart.setAttribute('data-tooltip-disabled', msg);
      elements.btnPreflightTest.disabled = true;
      elements.btnPreflightTest.title = msg;
      elements.btnPreflightTest.setAttribute('data-tooltip-disabled', msg);
      elements.btnUploadSubmit.disabled = true;
      elements.btnUploadSubmit.title = msg;
      elements.btnUploadSubmit.setAttribute('data-tooltip-disabled', msg);
      if (elements.btnUploadRetry) {
        elements.btnUploadRetry.disabled = true;
        elements.btnUploadRetry.title = msg;
        elements.btnUploadRetry.setAttribute('data-tooltip-disabled', msg);
      }
      elements.btnStopCapture.disabled = true;
      elements.btnCancelProcess.disabled = true;
      elements.btnRequestSummary.disabled = true;
      elements.btnSubmitSummary.disabled = true;

      // Disable retry/process/queue buttons in tables
      document.querySelectorAll('button[data-action="retry"], button[data-action="process"], button[data-action="queue-remove"], button[data-action="queue-restore"]').forEach((btn) => {
        btn.disabled = true;
        btn.title = msg;
        btn.setAttribute('data-tooltip-disabled', msg);
      });
      return;
    }

    // Update Whisper / Model availability warning
    updateWhisperModelWarning();

    // When online:
    const currentDev = elements.deviceSelect ? elements.deviceSelect.value : null;
    const isCurrentDeviceDenied = (lastPermissions === 'denied' || lastPermissions === false) &&
      (lastPermissionsDevice === null || lastPermissionsDevice === currentDev);

    const isPreflightForCurrentSource = lastPreflightSourceKind === currentSource;
    const hasPreflightData = isPreflightForCurrentSource && Boolean(lastPreflightData);

    const isBlackHoleMissing = isZoomSource && Boolean(
      !hasPreflightData ||
      (lastPreflightData && (
        Boolean(lastPreflightData.error && lastPreflightData.error.toLowerCase().includes('blackhole')) ||
        !lastPreflightData.selected_device ||
        !isBlackHoleDevice(lastPreflightData.selected_device)
      )) ||
      !elements.deviceSelect ||
      elements.deviceSelect.value === ''
    );

    const hasValidDeviceForSource = Boolean(
      hasPreflightData &&
      !lastPreflightData.error &&
      lastPreflightData.selected_device &&
      elements.deviceSelect &&
      elements.deviceSelect.value !== '' &&
      String(lastPreflightData.selected_device.index) === String(elements.deviceSelect.value) &&
      (
        (isZoomSource && isBlackHoleDevice(lastPreflightData.selected_device)) ||
        (isMicSource && isMicDevice(lastPreflightData.selected_device))
      )
    );

    // 1. Primary Start button (M01: denied device A does not block device B)
    if (isRecording || isTranscribing || isSummarizing || isBusy) {
      elements.btnPrimaryStart.disabled = true;
      const r = isSummarizing ? 'Выполняется создание саммари' : 'Выполняется операция';
      elements.btnPrimaryStart.title = r;
      elements.btnPrimaryStart.setAttribute('data-tooltip-disabled', r);
    } else if (isUploadSource) {
      elements.btnPrimaryStart.disabled = true;
      const r = 'Для импорта файла используйте кнопку «Импортировать файл»';
      elements.btnPrimaryStart.title = r;
      elements.btnPrimaryStart.setAttribute('data-tooltip-disabled', r);
    } else if (isBlackHoleMissing) {
      elements.btnPrimaryStart.disabled = true;
      const r = 'Запись недоступна: виртуальное аудиоустройство BlackHole не найдено';
      elements.btnPrimaryStart.title = r;
      elements.btnPrimaryStart.setAttribute('data-tooltip-disabled', r);
    } else if (isCurrentDeviceDenied) {
      elements.btnPrimaryStart.disabled = true;
      const r = 'Доступ к микрофону заблокирован в macOS';
      elements.btnPrimaryStart.title = r;
      elements.btnPrimaryStart.setAttribute('data-tooltip-disabled', r);
    } else if (!hasValidDeviceForSource) {
      elements.btnPrimaryStart.disabled = true;
      const r = 'Выберите доступное аудиоустройство в списке';
      elements.btnPrimaryStart.title = r;
      elements.btnPrimaryStart.setAttribute('data-tooltip-disabled', r);
    } else {
      elements.btnPrimaryStart.disabled = false;
      elements.btnPrimaryStart.removeAttribute('title');
      elements.btnPrimaryStart.removeAttribute('data-tooltip-disabled');
    }

    // 2. Preflight Test button
    if (isRecording || isTranscribing || isSummarizing || isBusy || isUploadSource) {
      elements.btnPreflightTest.disabled = true;
      const r = 'Тест недоступен во время выполнения операций';
      elements.btnPreflightTest.title = r;
      elements.btnPreflightTest.setAttribute('data-tooltip-disabled', r);
    } else if (isBlackHoleMissing) {
      elements.btnPreflightTest.disabled = true;
      const r = 'Тест недоступен: устройство BlackHole не найдено';
      elements.btnPreflightTest.title = r;
      elements.btnPreflightTest.setAttribute('data-tooltip-disabled', r);
    } else if (!hasValidDeviceForSource) {
      elements.btnPreflightTest.disabled = true;
      const r = 'Для теста уровня необходимо доступное аудиоустройство';
      elements.btnPreflightTest.title = r;
      elements.btnPreflightTest.setAttribute('data-tooltip-disabled', r);
    } else {
      elements.btnPreflightTest.disabled = false;
      elements.btnPreflightTest.removeAttribute('title');
      elements.btnPreflightTest.removeAttribute('data-tooltip-disabled');
    }

    // 3. Upload Submit and Retry buttons (Unified tool check, offline/concurrency, and file selection)
    const hasTools = hasUploadMediaTools();
    const canUpload = initialDataLoaded && isOnline && hasTools && hasFileSelected && !isOperating;

    elements.btnUploadSubmit.disabled = !canUpload;
    if (elements.btnUploadRetry) {
      elements.btnUploadRetry.disabled = !canUpload;
    }

    let uploadBlockReason = null;
    if (!initialDataLoaded) {
      uploadBlockReason = 'Загрузка данных с сервера...';
    } else if (!isOnline) {
      uploadBlockReason = 'Нет связи с локальным сервером';
    } else if (isOperating) {
      uploadBlockReason = 'Операция недоступна во время выполнения других процессов';
    } else if (!hasTools) {
      const missing = [];
      if (!serverTools || serverTools.ffmpeg !== true) missing.push('FFmpeg');
      if (!serverTools || serverTools.ffprobe !== true) missing.push('ffprobe');
      uploadBlockReason = `Импорт недоступен: отсутствуют или не проверены утилиты (${missing.join(', ')})`;
    } else if (!hasFileSelected) {
      uploadBlockReason = 'Выберите файл для импорта';
    }

    if (uploadBlockReason) {
      elements.btnUploadSubmit.title = uploadBlockReason;
      elements.btnUploadSubmit.setAttribute('data-tooltip-disabled', uploadBlockReason);
      if (elements.btnUploadRetry) {
        elements.btnUploadRetry.title = uploadBlockReason;
        elements.btnUploadRetry.setAttribute('data-tooltip-disabled', uploadBlockReason);
      }
    } else {
      elements.btnUploadSubmit.removeAttribute('title');
      elements.btnUploadSubmit.removeAttribute('data-tooltip-disabled');
      if (elements.btnUploadRetry) {
        elements.btnUploadRetry.removeAttribute('title');
        elements.btnUploadRetry.removeAttribute('data-tooltip-disabled');
      }
    }

    // 4. Active operation controls
    elements.btnStopCapture.disabled = !isRecording || isBusy;
    if (elements.btnStopCapture.disabled) {
      elements.btnStopCapture.setAttribute('data-tooltip-disabled', 'Остановка доступна только во время активной записи');
    } else {
      elements.btnStopCapture.removeAttribute('data-tooltip-disabled');
    }

    elements.btnCancelProcess.disabled = !isTranscribing || isBusy;
    if (elements.btnCancelProcess.disabled) {
      elements.btnCancelProcess.setAttribute('data-tooltip-disabled', 'Отмена доступна только во время активного распознавания');
    } else {
      elements.btnCancelProcess.removeAttribute('data-tooltip-disabled');
    }

    // 5. Summary button
    elements.btnRequestSummary.disabled = isBusy || isRecording || isSummarizing;
    if (elements.btnRequestSummary.disabled) {
      elements.btnRequestSummary.setAttribute('data-tooltip-disabled', 'Создание конспекта недоступно во время записи или других операций');
    } else {
      elements.btnRequestSummary.removeAttribute('data-tooltip-disabled');
    }

    const prov = elements.summaryProviderSelect ? elements.summaryProviderSelect.value : 'claude';
    if (prov === 'none') {
      elements.btnSubmitSummary.disabled = isBusy || isSummarizing;
    } else {
      elements.btnSubmitSummary.disabled = isBusy || isSummarizing || !elements.summaryOptInCheck.checked;
    }
    if (elements.btnSubmitSummary.disabled) {
      elements.btnSubmitSummary.setAttribute('data-tooltip-disabled', 'Для запуска подтвердите согласие на отправку текста расшифровки');
    } else {
      elements.btnSubmitSummary.removeAttribute('data-tooltip-disabled');
    }

    // 6. Dynamic table row actions
    const isWhisperMissing = !serverTools || serverTools.whisper !== true;
    const isModelMissing = !serverTools || serverTools.model !== true;
    const hasWhisperIssue = isWhisperMissing || isModelMissing;

    document.querySelectorAll('button[data-action="process"]').forEach((btn) => {
      if (isBusy || isRecording || isTranscribing || isSummarizing) {
        btn.disabled = true;
        btn.setAttribute('data-tooltip-disabled', 'Действие недоступно во время выполнения другой операции');
      } else if (hasWhisperIssue) {
        btn.disabled = true;
        const reason = isWhisperMissing && isModelMissing
          ? 'Распознавание недоступно: утилита и модель Whisper не найдены на сервере'
          : (isWhisperMissing
            ? 'Распознавание недоступно: утилита Whisper не найдена на сервере'
            : 'Распознавание недоступно: модель Whisper не найдена на сервере');
        btn.setAttribute('data-tooltip-disabled', reason);
        btn.title = reason;
      } else {
        btn.disabled = false;
        btn.removeAttribute('data-tooltip-disabled');
        btn.removeAttribute('title');
      }
    });

    document.querySelectorAll('button[data-action="retry"]').forEach((btn) => {
      btn.disabled = isBusy || isRecording || isTranscribing || isSummarizing;
      if (btn.disabled) {
        btn.setAttribute('data-tooltip-disabled', 'Действие недоступно во время выполнения другой операции');
      } else {
        btn.removeAttribute('data-tooltip-disabled');
        btn.removeAttribute('title');
      }
    });

    document.querySelectorAll('button[data-action="queue-remove"], button[data-action="queue-restore"]').forEach((btn) => {
      btn.disabled = isBusy || isRecording || isTranscribing || isSummarizing;
      if (btn.disabled) {
        btn.setAttribute('data-tooltip-disabled', 'Действие недоступно во время выполнения другой операции');
      } else {
        btn.removeAttribute('data-tooltip-disabled');
        btn.removeAttribute('title');
      }
    });

    // Opening/inspecting/filtering/sorting is always enabled
    document.querySelectorAll('button[data-action="inspect"]').forEach((btn) => {
      btn.disabled = false;
      btn.removeAttribute('data-tooltip-disabled');
    });
  }

  function updateWhisperModelWarning() {
    if (!elements.whisperModelWarning) return;
    if (!initialDataLoaded) {
      elements.whisperModelWarning.hidden = true;
      return;
    }
    const missing = [];
    if (!serverTools || serverTools.whisper !== true) missing.push('утилита Whisper (CLI)');
    if (!serverTools || serverTools.model !== true) missing.push('модель Whisper (ggml)');
    if (missing.length > 0) {
      elements.whisperModelWarning.hidden = false;
      if (elements.whisperModelWarningText) {
        elements.whisperModelWarningText.textContent =
          `Автономное распознавание недоступно: на сервере не обнаружены ${missing.join(' и ')}. ` +
          `Кнопка «Распознать» заблокирована. Аудиозаписи сохраняются без изменений.`;
      }
    } else {
      elements.whisperModelWarning.hidden = true;
    }
  }

  // Two-channel status helper
  function updateSystemStatus(state, label) {
    elements.systemStatusBadge.setAttribute('data-state', state);
    elements.systemStatusText.textContent = label;
  }

  // Format seconds into HH:MM:SS
  function formatSec(seconds) {
    if (!seconds || isNaN(seconds) || seconds < 0) return '00:00:00';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }

  // Format bytes into readable string
  function formatBytes(bytes) {
    if (!bytes || isNaN(bytes)) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let val = bytes;
    let u = 0;
    while (val >= 1024 && u < units.length - 1) {
      val /= 1024;
      u++;
    }
    return `${val.toFixed(1)} ${units[u]}`;
  }

  // Escape HTML entities safely
  function escapeHtml(str) {
    if (str === undefined || str === null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  // Load Audio Preflight (Read-only on init; R01 generation tracking)
  async function loadPreflight(testVolume = false) {
    preflightGeneration++;
    const currentGen = preflightGeneration;
    const kind = elements.sourceSelect.value;

    if (kind === 'upload') {
      elements.deviceSelectGroup.hidden = true;
      elements.uploadContainer.hidden = false;
      updateActionStates();
      return;
    }

    elements.deviceSelectGroup.hidden = false;
    elements.uploadContainer.hidden = true;

    const devIdx = (lastPreflightSourceKind === kind && elements.deviceSelect.value !== '')
      ? elements.deviceSelect.value
      : null;

    if (testVolume) {
      elements.sourceSelect.disabled = true;
      elements.deviceSelect.disabled = true;
      elements.preflightMessage.textContent = 'Идёт измерение звукового сигнала (2 сек)...';
      elements.preflightSymbol.textContent = '◐';
      elements.btnPreflightTest.disabled = true;
    }

    try {
      let pf;
      if (testVolume) {
        const res = await apiPost('/api/preflight/test', {
          kind: kind === 'mic' ? 'mic' : 'blackhole',
          device_index: devIdx !== null ? parseInt(devIdx, 10) : null,
          index: devIdx !== null ? parseInt(devIdx, 10) : null,
        });
        pf = res.preflight;
      } else {
        const query = new URLSearchParams({
          kind: kind === 'mic' ? 'mic' : 'blackhole',
        });
        if (devIdx !== null) {
          query.append('index', devIdx);
          query.append('device_index', devIdx);
        }
        const res = await apiGet(`/api/preflight?${query.toString()}`);
        pf = res.preflight;
      }

      // Check generation: if selection or source changed while request in flight, discard!
      if (currentGen !== preflightGeneration || elements.sourceSelect.value !== kind) {
        return;
      }

      lastPreflightData = pf;
      lastPreflightSourceKind = kind;

      const isZoom = kind === 'zoom';
      const availableDevices = pf.devices || [];
      const matchingDevices = availableDevices.filter((d) => {
        if (isZoom) {
          return isBlackHoleDevice(d);
        }
        return isMicDevice(d);
      });

      // Populate devices dropdown strictly with matching devices
      if (matchingDevices.length > 0) {
        elements.deviceSelect.innerHTML = '';
        matchingDevices.forEach((d) => {
          const opt = document.createElement('option');
          opt.value = String(d.index);
          opt.textContent = `[${d.index}] ${d.name} (${d.kind})`;
          elements.deviceSelect.appendChild(opt);
        });

        // Set selected device: strictly synchronize with pf.selected_device if it matches one of matchingDevices
        const targetDevice = (pf.selected_device && matchingDevices.some((d) => d.index === pf.selected_device.index))
          ? pf.selected_device
          : matchingDevices[0];

        if (targetDevice) {
          elements.deviceSelect.value = String(targetDevice.index);
        }
      } else {
        const emptyLabel = isZoom ? 'BlackHole не найден' : 'Микрофон не найден';
        elements.deviceSelect.innerHTML = `<option value="">${emptyLabel}</option>`;
        elements.deviceSelect.value = '';
      }

      const isBlackHoleNotFound = isZoom && (
        Boolean(pf.error && pf.error.toLowerCase().includes('blackhole')) ||
        matchingDevices.length === 0 ||
        !pf.selected_device ||
        !isBlackHoleDevice(pf.selected_device)
      );

      // Save permission string (R03: strings granted/denied/unknown/enumerated_capture_untested/error)
      lastPermissions = pf.permissions || 'unknown';
      lastPermissionsDevice = pf.selected_device ? String(pf.selected_device.index) : (devIdx !== null ? String(devIdx) : null);

      // Render preflight feedback
      if (isBlackHoleNotFound) {
        elements.preflightSymbol.textContent = '❌';
        elements.preflightSymbol.className = 'status-symbol error-symbol';
        elements.preflightMessage.textContent =
          'BlackHole не найден. Запись системного звука недоступна. Установите/включите BlackHole или выберите микрофон; для готового видео выберите импорт.';
      } else if (pf.error) {
        elements.preflightSymbol.textContent = '❌';
        elements.preflightSymbol.className = 'status-symbol error-symbol';
        elements.preflightMessage.textContent = `Диагностика: ${pf.error}`;
      } else if (lastPermissions === 'denied' || pf.permissions === false) {
        elements.preflightSymbol.textContent = '⚠️';
        elements.preflightSymbol.className = 'status-symbol error-symbol';
        elements.preflightMessage.textContent = 'Доступ к микрофону заблокирован в macOS. Разрешите доступ в настройках системы.';
      } else if (!pf.devices || pf.devices.length === 0) {
        elements.preflightSymbol.textContent = '⚠️';
        elements.preflightSymbol.className = 'status-symbol error-symbol';
        elements.preflightMessage.textContent = 'Аудиоустройства не найдены в системе.';
      } else if (testVolume && pf.volume_check) {
        const vc = pf.volume_check;
        const rateInfo = pf.native_stream?.sample_rate ? ` (${pf.native_stream.sample_rate} Hz)` : '';
        if (vc.status === 'ok') {
          elements.preflightSymbol.textContent = '✅';
          elements.preflightSymbol.className = 'status-symbol';
          elements.preflightMessage.textContent = `Звук обнаружен: пик ${vc.max_volume_db} dB${rateInfo}. Устройство готово к записи.`;
        } else if (vc.status === 'silence') {
          elements.preflightSymbol.textContent = '⚠️';
          elements.preflightSymbol.className = 'status-symbol error-symbol';
          elements.preflightMessage.textContent = `Тишина (${vc.max_volume_db} dB). Проверьте системный аудиовыход или микрофон.`;
        } else {
          elements.preflightSymbol.textContent = '❌';
          elements.preflightSymbol.className = 'status-symbol error-symbol';
          elements.preflightMessage.textContent = `Ошибка проверки уровня: ${vc.message}`;
        }
      } else {
        const selName = pf.selected_device ? `[${pf.selected_device.index}] ${pf.selected_device.name}` : 'выбранное устройство';
        const rateInfo = pf.native_stream?.sample_rate ? ` (${pf.native_stream.sample_rate} Hz)` : '';
        elements.preflightSymbol.textContent = '○';
        elements.preflightSymbol.className = 'status-symbol';
        if (lastPermissions === 'unknown' || lastPermissions === 'enumerated_capture_untested') {
          elements.preflightMessage.textContent = `Выбрано: ${selName}${rateInfo}. Захват не тестировался. Нажмите «Проверить уровень».`;
        } else {
          elements.preflightMessage.textContent = `Выбрано: ${selName}${rateInfo}. Доступ предоставлен. Нажмите «Проверить уровень» для проверки звука.`;
        }
      }

      // Update onboarding status
      if (elements.onboardingStatusMsg) {
        if (isBlackHoleNotFound) {
          elements.onboardingStatusMsg.textContent = '❌ BlackHole не установлен.';
        } else if (lastPermissions === 'denied' || pf.permissions === false) {
          elements.onboardingStatusMsg.textContent = '⚠️ Разрешите доступ к микрофону в системных настройках macOS.';
        } else if (pf.volume_check && pf.volume_check.status === 'ok') {
          elements.onboardingStatusMsg.textContent = '✅ Источник готов (звук обнаружен).';
        } else {
          elements.onboardingStatusMsg.textContent = '⚪️ Источник выбран (не проверено).';
        }
      }
    } catch (err) {
      if (currentGen === preflightGeneration) {
        elements.preflightSymbol.textContent = '❌';
        elements.preflightSymbol.className = 'status-symbol error-symbol';
        elements.preflightMessage.textContent = `Ошибка диагностики: ${err.message}`;
      }
    } finally {
      elements.sourceSelect.disabled = false;
      elements.deviceSelect.disabled = false;
      updateActionStates();
    }
  }

  let lastRenderedSessionsJson = '';

  // Render Sessions Table with Exception-First unified order and pagination (R11, CMP-001, DSH-007)
  function renderSessionsTable(sessions) {
    if (sessions !== undefined) {
      cachedSessions = sessions || [];
    }
    if (!initialDataLoaded) {
      return;
    }
    elements.sessionsTableBody.removeAttribute('aria-busy');

    const query = elements.sessionFilterInput.value.trim().toLowerCase();
    const statusFilter = elements.statusFilterSelect.value;

    let filtered = cachedSessions.filter((s) => {
      const inQueue = s.in_queue !== false;
      if (statusFilter === 'removed') {
        if (inQueue) return false;
      } else {
        if (!inQueue) return false;
        if (statusFilter !== 'all' && s.status !== statusFilter) return false;
      }
      if (query && !s.session_id.toLowerCase().includes(query) && !(s.title || '').toLowerCase().includes(query)) {
        return false;
      }
      return true;
    });

    // Separate exceptions (failed/interrupted) and normal records (R11 exception-first)
    const isException = (s) => s.status === 'failed' || s.status === 'interrupted';
    const exceptionsList = filtered.filter(isException);
    const normalList = filtered.filter((s) => !isException(s));

    // Exceptions are sorted strictly by severity (failed > interrupted) and age (oldest first)
    exceptionsList.sort((a, b) => {
      if (a.status !== b.status) {
        return a.status === 'failed' ? -1 : 1;
      }
      const da = a.created_at || '';
      const db = b.created_at || '';
      return da.localeCompare(db);
    });

    // Sort normal records by user selection
    const sortFn = (a, b) => {
      let va = a[currentSortField] ?? '';
      let vb = b[currentSortField] ?? '';
      if (typeof va === 'number' && typeof vb === 'number') {
        return currentSortDirection === 'asc' ? va - vb : vb - va;
      }
      return currentSortDirection === 'asc'
        ? String(va).localeCompare(String(vb))
        : String(vb).localeCompare(String(va));
    };

    normalList.sort(sortFn);

    // Unified dataset: exceptions pinned at the top, followed by user-sorted normal records
    const unified = [...exceptionsList, ...normalList];
    const totalCount = unified.length;
    const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));

    if (currentPage > totalPages) {
      currentPage = totalPages;
    }

    // Pagination slicing
    const startIndex = (currentPage - 1) * pageSize;
    const endIndex = Math.min(startIndex + pageSize, totalCount);
    const pageItems = unified.slice(startIndex, endIndex);

    // Update pagination controls
    if (totalCount > pageSize) {
      elements.tablePagination.hidden = false;
      if (totalCount > 100) {
        elements.paginationSummary.textContent = `Показано ${startIndex + 1}–${endIndex} из ${totalCount} сессий (поддерживаемый диапазон UI: до 100 записей; используйте поиск/фильтры)`;
      } else {
        elements.paginationSummary.textContent = `Показано ${startIndex + 1}–${endIndex} из ${totalCount} сессий`;
      }
      elements.pageIndicator.textContent = `${currentPage} / ${totalPages}`;
      elements.btnPrevPage.disabled = currentPage <= 1;
      elements.btnNextPage.disabled = currentPage >= totalPages;
    } else {
      elements.tablePagination.hidden = true;
    }

    // Avoid unnecessary DOM recreation if data has not changed
    const serialized = JSON.stringify({
      query,
      statusFilter,
      sortField: currentSortField,
      sortDir: currentSortDirection,
      page: currentPage,
      items: pageItems.map((s) => ({
        id: s.session_id,
        title: s.title,
        status: s.status,
        in_queue: s.in_queue !== false,
        dur: s.total_duration_sec,
        date: s.created_at,
        src: s.source_kind,
        err: s.error_message || '',
      })),
    });

    if (serialized === lastRenderedSessionsJson) {
      return;
    }
    lastRenderedSessionsJson = serialized;

    // Track active focused elemen
    let activeAction = null;
    let activeSessionId = null;
    if (document.activeElement && elements.sessionsTableBody.contains(document.activeElement)) {
      activeAction = document.activeElement.getAttribute('data-action');
      activeSessionId = document.activeElement.getAttribute('data-session-id');
    }

    if (totalCount === 0) {
      elements.sessionsTableBody.innerHTML = `
        <tr class="empty-row">
          <td colspan="6" class="empty-cell">Сессии по заданным фильтрам не найдены.</td>
        </tr>
      `;
      return;
    }

    elements.sessionsTableBody.innerHTML = '';
    pageItems.forEach((s) => {
      const tr = document.createElement('tr');
      tr.setAttribute('data-session-id', s.session_id);

      // 1. Session ID & Title (Identity column)
      const tdId = document.createElement('td');
      tdId.className = 'font-mono';
      tdId.textContent = s.title || s.session_id;

      // 2. Source
      const tdSource = document.createElement('td');
      tdSource.textContent = s.source_kind === 'zoom' ? 'Zoom' : (s.source_kind === 'mic' ? 'Микрофон' : 'Импорт');

      // 3. Date
      const tdDate = document.createElement('td');
      tdDate.textContent = s.created_at || '—';

      // 4. Duration
      const tdDur = document.createElement('td');
      tdDur.className = 'numeric-col font-mono';
      tdDur.textContent = formatSec(s.total_duration_sec);

      // 5. Status (2 channels: text + symbol)
      const tdStatus = document.createElement('td');
      const statusSpan = document.createElement('span');
      statusSpan.className = 'row-status';
      statusSpan.setAttribute('data-status', s.status);

      const symbolMap = {
        completed: '✓',
        ready: '○',
        recording: '●',
        processing: '◐',
        failed: '✕',
        interrupted: '⚠️',
      };
      const labelMap = {
        completed: 'Готово',
        ready: 'Ожидает',
        recording: 'Запись...',
        processing: 'Распознавание...',
        failed: 'Ошибка',
        interrupted: 'Прервано',
      };

      statusSpan.innerHTML = `
        <span class="status-symbol" aria-hidden="true">${symbolMap[s.status] || '•'}</span>
        <span>${labelMap[s.status] || s.status}</span>
      `;
      tdStatus.appendChild(statusSpan);

      if (s.in_queue === false) {
        const removedBadge = document.createElement('span');
        removedBadge.className = 'badge-removed';
        removedBadge.textContent = 'Убрано';
        tdStatus.appendChild(removedBadge);
      }

      if (s.error_message) {
        const errBadge = document.createElement('span');
        errBadge.className = 'status-error-inline';
        errBadge.tabIndex = 0;
        errBadge.setAttribute('role', 'note');
        errBadge.setAttribute('aria-label', `Ошибка: ${s.error_message}`);
        errBadge.setAttribute('data-tooltip', s.error_message);
        errBadge.textContent = `Ошибка: ${s.error_message}`;
        tdStatus.appendChild(errBadge);
      }

      if (s.diarization_status === 'completed' || s.has_diarization) {
        const dBadge = document.createElement('span');
        dBadge.className = 'badge-meta';
        dBadge.style.marginInlineStart = 'var(--ad-space-1)';
        dBadge.textContent = '👥 Диаризация';
        tdStatus.appendChild(dBadge);
      } else if (s.diarization_status === 'failed') {
        const dBadge = document.createElement('span');
        dBadge.className = 'status-error-inline';
        dBadge.style.marginInlineStart = 'var(--ad-space-1)';
        dBadge.textContent = 'Диаризация: сбой';
        tdStatus.appendChild(dBadge);
      }

      // 6. Action column
      const tdActions = document.createElement('td');
      tdActions.className = 'action-col';

      const btnInspect = document.createElement('button');
      btnInspect.className = 'btn btn-quiet';
      btnInspect.type = 'button';
      btnInspect.textContent = 'Открыть';
      btnInspect.setAttribute('data-action', 'inspect');
      btnInspect.setAttribute('data-session-id', s.session_id);
      btnInspect.setAttribute('data-tooltip', 'Открыть расшифровку, таймкоды и конспект этой сессии в инспекторе ниже');
      btnInspect.setAttribute('aria-label', `Открыть сессию ${s.session_id}`);
      btnInspect.addEventListener('click', () => inspectSession(s.session_id, true));
      tdActions.appendChild(btnInspect);

      const inQueue = s.in_queue !== false;
      if (inQueue) {
        if (s.status === 'ready' || s.status === 'interrupted' || s.status === 'failed') {
          const btnProc = document.createElement('button');
          btnProc.className = 'btn btn-secondary';
          btnProc.type = 'button';
          const isReady = s.status === 'ready';
          btnProc.textContent = isReady ? 'Распознать' : 'Повторить';
          btnProc.setAttribute('data-action', isReady ? 'process' : 'retry');
          btnProc.setAttribute('data-session-id', s.session_id);
          if (isReady) {
            btnProc.setAttribute('data-tooltip', 'Запустить автономное распознавание аудиозаписи в текст через Whisper');
            btnProc.setAttribute('data-tooltip-disabled', 'Обработка недоступна: сервер оффлайн или выполняется другая операция');
            btnProc.setAttribute('aria-label', `Распознать запись сессии ${s.session_id}`);
          } else {
            btnProc.setAttribute('data-tooltip', 'Повторить попытку: сбросить сессию в статус ожидания для повторного распознавания');
            btnProc.setAttribute('data-tooltip-disabled', 'Повтор недоступен: сервер оффлайн или выполняется другая операция');
            btnProc.setAttribute('aria-label', `Повторить распознавание сессии ${s.session_id}`);
          }
          btnProc.addEventListener('click', async () => {
            try {
              const savedLang = currentSettingsData?.effective?.language;
              const langVal = savedLang || s.language || elements.languageSelect?.value || 'ru';
              if (s.status === 'ready') {
                await apiPost('/api/process', { session_id: s.session_id, lang: langVal });
              } else {
                await apiPost('/api/session/retry', { session_id: s.session_id, lang: langVal });
              }
              await pollStatus();
            } catch (e) {
              alert(`Ошибка: ${e.message}`);
            }
          });
          tdActions.appendChild(btnProc);

          // «Убрать из очереди»
          const btnRemove = document.createElement('button');
          btnRemove.className = 'btn btn-quiet btn-queue-remove';
          btnRemove.type = 'button';
          btnRemove.textContent = 'Убрать из очереди';
          btnRemove.setAttribute('data-action', 'queue-remove');
          btnRemove.setAttribute('data-session-id', s.session_id);
          btnRemove.setAttribute('data-tooltip', 'Исключить запись из активной очереди ожидания (файлы сохраняются на диске)');
          btnRemove.setAttribute('data-tooltip-disabled', 'Действие недоступно: выполняется операция или сервер оффлайн');
          btnRemove.setAttribute('aria-label', `Убрать сессию ${s.title || s.session_id} из очереди`);
          btnRemove.addEventListener('click', () => openQueueConfirmModal(s.session_id, btnRemove));
          tdActions.appendChild(btnRemove);
        }
      } else {
        // «Вернуть в очередь»
        const btnRestore = document.createElement('button');
        btnRestore.className = 'btn btn-secondary btn-queue-restore';
        btnRestore.type = 'button';
        btnRestore.textContent = 'Вернуть в очередь';
        btnRestore.setAttribute('data-action', 'queue-restore');
        btnRestore.setAttribute('data-session-id', s.session_id);
        btnRestore.setAttribute('data-tooltip', 'Вернуть запись в очередь для распознавания');
        btnRestore.setAttribute('data-tooltip-disabled', 'Действие недоступно: выполняется операция или сервер оффлайн');
        btnRestore.setAttribute('aria-label', `Вернуть сессию ${s.title || s.session_id} в очередь`);
        btnRestore.addEventListener('click', async () => {
          try {
            isRequestInProgress = true;
            updateActionStates();
            await apiPost('/api/session/queue/restore', { session_id: s.session_id });
            appendLocalLog(`Запись ${s.session_id} возвращена в очередь.`);
            await pollStatus();
          } catch (e) {
            alert(`Не удалось вернуть запись в очередь: ${e.message}`);
          } finally {
            isRequestInProgress = false;
            updateActionStates();
          }
        });
        tdActions.appendChild(btnRestore);
      }

      tr.appendChild(tdId);
      tr.appendChild(tdSource);
      tr.appendChild(tdDate);
      tr.appendChild(tdDur);
      tr.appendChild(tdStatus);
      tr.appendChild(tdActions);

      elements.sessionsTableBody.appendChild(tr);
    });

    // Restore focus if element was previously focused
    if (activeAction && activeSessionId) {
      const elToFocus = elements.sessionsTableBody.querySelector(
        `button[data-action="${activeAction}"][data-session-id="${activeSessionId}"]`
      );
      if (elToFocus) {
        elToFocus.focus({ preventScroll: true });
      }
    }

    updateActionStates();
  }

  // Inspect Session Details, Transcript & Summary (R07, R10)
  async function inspectSession(sessionId, scrollIntoView = false) {
    try {
      const res = await apiGet(`/api/session/${encodeURIComponent(sessionId)}`);
      inspectedSessionId = sessionId;
      inspectedSessionData = res; // Store in-memory cache for fast, offline search (R10)

      elements.inspectorSection.hidden = false;
      elements.inspectorSessionTitle.textContent = res.manifest?.title || sessionId;
      elements.inspectorWordCount.textContent = `${res.manifest?.word_count || 0} слов`;

      // Export links
      elements.exportTxtLink.href = `/files/${encodeURIComponent(sessionId)}/transcript.txt`;
      elements.exportMdLink.href = `/files/${encodeURIComponent(sessionId)}/transcript.md`;
      elements.exportSrtLink.href = `/files/${encodeURIComponent(sessionId)}/transcript.srt`;
      elements.exportVttLink.href = `/files/${encodeURIComponent(sessionId)}/transcript.vtt`;
      elements.exportJsonLink.href = `/files/${encodeURIComponent(sessionId)}/transcript.json`;

      // Render Speaker Legend and Transcript from cache
      renderSpeakerLegend(res.manifest, res.segments, res.diarization);
      renderTranscriptEntries(res.segments, res.transcript);

      // Render Summary
      if (res.summary) {
        elements.summaryView.textContent = res.summary;
      } else {
        elements.summaryView.innerHTML = '<p class="empty-cell">Конспект ещё не составлен. Нажмите «Сделать конспект».</p>';
      }

      // Render Error Banner if session has error
      if (res.manifest?.error_message) {
        if (elements.inspectorErrorBanner) elements.inspectorErrorBanner.hidden = false;
        if (elements.inspectorErrorText) elements.inspectorErrorText.textContent = res.manifest.error_message;
      } else {
        if (elements.inspectorErrorBanner) elements.inspectorErrorBanner.hidden = true;
        if (elements.inspectorErrorText) elements.inspectorErrorText.textContent = '';
      }

      // Scroll inspector into view only when explicitly triggered by user click (R07)
      if (scrollIntoView) {
        const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        elements.inspectorSection.scrollIntoView({
          behavior: prefersReducedMotion ? 'auto' : 'smooth',
          block: 'nearest',
        });
      }
    } catch (err) {
      alert(`Не удалось открыть сессию: ${err.message}`);
    }
  }

  // Render Speaker Legend with Inline Renaming Inputs (v1.1)
  function renderSpeakerLegend(manifest, segments, diarization) {
    if (!elements.speakerLegendContainer) return;

    // Collect speakers from manifest or diarization block
    const rawSpeakers = (manifest && manifest.speakers && Object.keys(manifest.speakers).length > 0)
      ? manifest.speakers
      : (diarization && diarization.speakers ? diarization.speakers : {});
    const speakerMap = {};
    for (const [k, v] of Object.entries(rawSpeakers)) {
      if (typeof v === 'object' && v !== null && v.display_name) {
        speakerMap[k] = v.display_name;
      } else if (typeof v === 'string') {
        speakerMap[k] = v;
      }
    }
    const seenSpeakers = new Set(Object.keys(speakerMap));

    if (segments && Array.isArray(segments)) {
      segments.forEach((seg) => {
        const spk = seg.speaker_id || seg.speaker;
        if (spk && spk !== 'speaker_unknown') seenSpeakers.add(spk);
      });
    }

    if (seenSpeakers.size === 0) {
      elements.speakerLegendContainer.hidden = true;
      elements.speakerLegendContainer.innerHTML = '';
      return;
    }

    elements.speakerLegendContainer.hidden = false;
    elements.speakerLegendContainer.innerHTML = '<span class="speaker-legend-title">Голоса:</span>';

    const sortedSpeakers = Array.from(seenSpeakers).sort();
    sortedSpeakers.forEach((spkId) => {
      let spkIdx = 1;
      const match = spkId.match(/speaker_0*(\d+)/);
      if (match) {
        const num = parseInt(match[1], 10);
        spkIdx = num === 0 ? 1 : ((num - 1) % 20) + 1;
      }

      const item = document.createElement('div');
      item.className = 'speaker-rename-item';

      const badge = document.createElement('span');
      badge.className = 'speaker-badge';
      badge.setAttribute('data-speaker-idx', spkIdx);
      badge.textContent = spkId;

      // Get source and evidence
      const spkObj = rawSpeakers[spkId] || {};
      const source = spkObj.name_source || 'default';
      const evidence = spkObj.name_evidence || '';

      let titleMsg = "Голос распознаётся локально. Имя можно задать вручную.";
      if (source === 'auto_intro') {
          titleMsg = `Имя определено автоматически: представился ("${evidence}")`;
      } else if (source === 'manual') {
          titleMsg = "Имя задано вручную";
      }

      const input = document.createElement('input');
      input.type = 'text';
      input.className = 'speaker-rename-input';
      input.value = speakerMap[spkId] || '';
      input.placeholder = `Говорящий ${spkIdx}`;
      input.setAttribute('aria-label', `Имя для ${spkId}`);
      input.title = titleMsg;

      const badgeIcon = document.createElement('span');
      badgeIcon.className = 'speaker-source-icon';
      badgeIcon.style.marginLeft = '4px';
      badgeIcon.style.fontSize = '0.8em';
      if (source === 'auto_intro') {
          badgeIcon.textContent = '✨';
          badgeIcon.title = titleMsg;
      } else if (source === 'manual') {
          badgeIcon.textContent = '✍️';
          badgeIcon.title = titleMsg;
      }

      let saveTimeout = null;
      input.addEventListener('input', () => {
        if (saveTimeout) clearTimeout(saveTimeout);
        saveTimeout = setTimeout(async () => {
          try {
            const newName = input.value.trim();
            speakerMap[spkId] = newName;
            const res = await apiPost('/api/session/speakers', {
              session_id: inspectedSessionId,
              speakers: { [spkId]: { display_name: newName, name_source: 'manual' } }
            });
            if (inspectedSessionData) {
              if (res.speakers) {
                if (inspectedSessionData.manifest) inspectedSessionData.manifest.speakers = res.speakers;
                if (inspectedSessionData.diarization) inspectedSessionData.diarization.speakers = res.speakers;
              } else if (inspectedSessionData.manifest) {
                inspectedSessionData.manifest.speakers = inspectedSessionData.manifest.speakers || {};
                inspectedSessionData.manifest.speakers[spkId] = { display_name: newName, name_source: 'manual' };
              }
            }
            renderTranscriptEntries(inspectedSessionData?.segments, inspectedSessionData?.transcript);
            renderSpeakerLegend(inspectedSessionData?.manifest, inspectedSessionData?.segments, inspectedSessionData?.diarization);
          } catch (err) {
            console.error('Failed to update speaker name:', err);
          }
        }, 400);
      });

      const btnReset = document.createElement('button');
      btnReset.textContent = 'Сбросить';
      btnReset.className = 'btn-secondary btn-sm';
      btnReset.style.marginLeft = '4px';
      btnReset.title = 'Сбросить имя';
      btnReset.onclick = async () => {
          try {
              const res = await apiPost('/api/session/speakers', {
                  session_id: inspectedSessionId,
                  speakers: { [spkId]: { display_name: '', name_source: 'default' } }
              });
              input.value = '';
              speakerMap[spkId] = '';
              if (inspectedSessionData) {
                  if (res.speakers) {
                      if (inspectedSessionData.manifest) inspectedSessionData.manifest.speakers = res.speakers;
                      if (inspectedSessionData.diarization) inspectedSessionData.diarization.speakers = res.speakers;
                  } else if (inspectedSessionData.manifest && inspectedSessionData.manifest.speakers) {
                      delete inspectedSessionData.manifest.speakers[spkId].display_name;
                      delete inspectedSessionData.manifest.speakers[spkId].name_evidence;
                      delete inspectedSessionData.manifest.speakers[spkId].name_confidence;
                      inspectedSessionData.manifest.speakers[spkId].name_source = 'default';
                  }
              }
              renderTranscriptEntries(inspectedSessionData?.segments, inspectedSessionData?.transcript);
              renderSpeakerLegend(inspectedSessionData?.manifest, inspectedSessionData?.segments, inspectedSessionData?.diarization);
          } catch (e) {
              console.error('Failed to reset speaker', e);
          }
      };

      // Set badge text to formatted name to visually distinguish duplicates!
      badge.textContent = getFormattedSpeakerName(spkId, rawSpeakers);

      item.appendChild(badge);
      item.appendChild(input);
      item.appendChild(badgeIcon);
      item.appendChild(btnReset);
      elements.speakerLegendContainer.appendChild(item);
    });
  }

  // Render Transcript Entries using in-memory cached data with speaker badges (v1.1)

  function getFormattedSpeakerName(spkId, speakersObj) {
    if (!spkId || spkId === 'speaker_unknown') return 'Неизвестный';

    let defaultName = spkId;
    let spkIdx = 1;
    const match = spkId.match(/speaker_0*(\d+)/);
    if (match) {
      const num = parseInt(match[1], 10);
      spkIdx = num === 0 ? 1 : num;
      defaultName = `Говорящий ${spkIdx}`;
    }

    if (speakersObj && speakersObj[spkId]) {
      const meta = speakersObj[spkId];
      let disp = '';
      if (typeof meta === 'object' && meta !== null && meta.display_name) {
        disp = String(meta.display_name).trim();
      } else if (typeof meta === 'string' && meta.trim()) {
        disp = meta.trim();
      }

      if (disp) {
        let isDuplicate = false;
        for (const [k, v] of Object.entries(speakersObj)) {
          if (k === spkId) continue;
          let otherDisp = '';
          if (typeof v === 'object' && v !== null && v.display_name) {
            otherDisp = String(v.display_name).trim();
          } else if (typeof v === 'string' && v.trim()) {
            otherDisp = v.trim();
          }
          if (otherDisp === disp) {
            isDuplicate = true;
            break;
          }
        }
        if (isDuplicate) {
          return `${disp} · ${defaultName}`;
        }
        return disp;
      }
    }
    return defaultName;
  }

  function renderTranscriptEntries(segments, fallbackText) {
    elements.transcriptView.innerHTML = '';
    const query = elements.transcriptSearchInput.value.trim().toLowerCase();
    const rawMap = inspectedSessionData?.manifest?.speakers || inspectedSessionData?.diarization?.speakers || {};
    const speakerMap = {};
    for (const [k, v] of Object.entries(rawMap)) {
      if (typeof v === 'object' && v !== null && v.display_name) {
        speakerMap[k] = v.display_name;
      } else if (typeof v === 'string') {
        speakerMap[k] = v;
      }
    }

    if (segments && segments.length > 0) {
      let matchCount = 0;
      segments.forEach((seg) => {
        const text = seg.text || '';
        if (query && !text.toLowerCase().includes(query)) return;

        matchCount++;
        const row = document.createElement('div');
        row.className = 'transcript-entry';

        const fromSec = (seg.from_sec !== undefined && seg.from_sec !== null)
          ? seg.from_sec
          : (seg.start !== undefined ? seg.start : 0);
        const timeSpan = document.createElement('span');
        timeSpan.className = 'transcript-time';
        timeSpan.textContent = `[${formatSec(fromSec)}]`;

        // Speaker badge
        const spkWrapper = document.createElement('span');
        const spk = seg.speaker_id || seg.speaker;
        if (spk && spk !== 'speaker_unknown') {
          const badge = document.createElement('span');
          badge.className = 'speaker-badge';
          let spkIdx = 1;
          const match = spk.match(/speaker_0*(\d+)/);
          if (match) {
            const num = parseInt(match[1], 10);
            spkIdx = num === 0 ? 1 : ((num - 1) % 20) + 1;
          }
          badge.setAttribute('data-speaker-idx', spkIdx);
          badge.textContent = getFormattedSpeakerName(spk, rawMap);
          spkWrapper.appendChild(badge);
        }

        if (seg.overlap) {
          const overlapSpan = document.createElement('span');
          overlapSpan.className = 'overlap-tag';
          overlapSpan.textContent = 'Наложение';
          spkWrapper.appendChild(overlapSpan);
        }

        const textSpan = document.createElement('span');
        textSpan.className = 'transcript-text';
        textSpan.textContent = text;

        row.appendChild(timeSpan);
        row.appendChild(spkWrapper);
        row.appendChild(textSpan);
        elements.transcriptView.appendChild(row);
      });

      if (matchCount === 0) {
        elements.transcriptView.innerHTML = '<p class="empty-cell">Фрагменты по запросу поиска не найдены.</p>';
      }
    } else if (fallbackText) {
      const lines = fallbackText.split('\n').filter(l => l.trim().length > 0);
      let matchCount = 0;
      lines.forEach((line) => {
        if (query && !line.toLowerCase().includes(query)) return;
        matchCount++;
        const p = document.createElement('p');
        p.className = 'transcript-text';
        p.textContent = line;
        elements.transcriptView.appendChild(p);
      });

      if (matchCount === 0) {
        elements.transcriptView.innerHTML = '<p class="empty-cell">Фрагменты по запросу поиска не найдены.</p>';
      }
    } else {
      elements.transcriptView.innerHTML = '<p class="empty-cell">Расшифровка пока отсутствует.</p>';
    }
  }

  // Safe Focus Return Function (R09, A11Y-007, M04)
  let isClosingSummaryModal = false;
  function closeSummaryModal() {
    if (isClosingSummaryModal) return;
    isClosingSummaryModal = true;
    try {
      if (elements.summaryModal && elements.summaryModal.open) {
        elements.summaryModal.close();
      }
      if (elements.btnRequestSummary && !elements.btnRequestSummary.disabled && !elements.btnRequestSummary.hidden) {
        elements.btnRequestSummary.focus();
      } else if (elements.btnExportMenu && !elements.btnExportMenu.disabled && !elements.btnExportMenu.hidden) {
        elements.btnExportMenu.focus();
      } else if (elements.tabSummary) {
        elements.tabSummary.focus();
      }
    } finally {
      isClosingSummaryModal = false;
    }
  }

  // Independent Watchdog for Server Freshness (DSH-006, M02)
  function checkFreshnessWatchdog() {
    if (!initialDataLoaded) return;
    const elapsedSinceSuccess = Date.now() - lastSuccessfulPollTime;
    if (elapsedSinceSuccess > 4000) {
      if (isOnline) {
        isOnline = false;
        elements.connectionBanner.hidden = false;
        elements.connectionBannerText.textContent = `Соединение с сервером прервано (${Math.round(elapsedSinceSuccess / 1000)}с назад). Проверьте локальный процесс ui.py.`;
        updateSystemStatus('stale', 'Нет связи с сервером');
        document.body.classList.add('is-offline');
        updateActionStates();
      }
    }
  }

  // Poll Status (every 2000 ms per DSH-006; single poll in flight R04)
  async function pollStatus() {
    checkFreshnessWatchdog();
    if (isPollInFlight) return;
    isPollInFlight = true;

    try {
      const data = await apiGet('/api/status');
      initialDataLoaded = true;
      lastSuccessfulPollTime = Date.now();
      isOnline = true;
      elements.connectionBanner.hidden = true;
      document.body.classList.remove('is-offline');

      // Display authoritative clock freshness (M02)
      if (elements.lastSyncTime) {
        const timeStr = new Date(lastSuccessfulPollTime).toLocaleTimeString('ru-RU');
        elements.lastSyncTime.textContent = `Обновлено: ${timeStr}`;
        elements.lastSyncTime.hidden = false;
      }

      // Update CSRF token if refreshed
      if (data.csrf_token) csrfToken = data.csrf_token;

      // Disk quota
      if (data.disk) {
        elements.diskFreeVal.textContent = formatBytes(data.disk.free_bytes);
        elements.diskHoursVal.textContent = `(~${data.disk.hours_remaining} ч записи)`;
      }

      // System status & active work (B01: robust adapter for both unified and legacy status contracts)
      let active = data.active;
      if (!active) {
        if (data.is_recording || data.state === 'recording' || (data.active_session && data.active_session !== '')) {
          active = {
            kind: 'recording',
            session_id: data.active_session || (typeof data.session === 'string' ? data.session : 'active_session'),
            start_time: data.start_time || data.recording_start_time || null,
          };
        } else if (data.state === 'transcribing' || (data.transcription && data.transcription.is_running)) {
          active = {
            kind: 'transcribing',
            session_id: (data.transcription && data.transcription.session_id) || data.active_session || 'transcribing',
            progress: data.transcription || null,
          };
        } else if (data.lock && data.lock.action === 'summary') {
          active = {
            kind: 'summary',
            session_id: data.lock.session_id,
            status: 'running',
          };
        }
      }

      if (active) {
        activeSessionId = active.session_id;
        activeOperationKind = active.kind;
        elements.activeWorkSection.hidden = false;
        elements.activeSessionId.textContent = active.session_id || '—';

        if (active.kind === 'recording') {
          updateSystemStatus('working', 'Идёт запись аудио');
          elements.btnPrimaryStart.hidden = true;
          elements.btnStopCapture.hidden = false;
          elements.btnCancelProcess.hidden = true;
          elements.activeProgressBlock.hidden = true;
          elements.activeEtaBlock.hidden = true;
          elements.activeProgressBar.setAttribute('aria-busy', 'false');
          if (elements.activeControlHelp) {
            elements.activeControlHelp.hidden = false;
            elements.activeControlHelp.textContent = 'При остановке все записанные аудиосегменты сохраняются на диске и готовы к распознаванию.';
          }

          if (active.start_time && typeof active.start_time === 'number') {
            recordingStartTime = active.start_time;
            const elapsed = Math.max(0, Math.floor(Date.now() / 1000 - recordingStartTime));
            elements.activeElapsed.textContent = formatSec(elapsed);
          } else if (active.elapsed_sec !== undefined && active.elapsed_sec !== null) {
            recordingStartTime = null;
            elements.activeElapsed.textContent = formatSec(active.elapsed_sec);
          } else {
            recordingStartTime = null;
            elements.activeElapsed.textContent = 'неизвестно';
          }

        } else if (active.kind === 'transcribing') {
          recordingStartTime = null;
          updateSystemStatus('working', 'Идёт распознавание');
          elements.btnPrimaryStart.hidden = false;
          elements.btnStopCapture.hidden = true;
          elements.btnCancelProcess.hidden = false;
          elements.activeProgressBlock.hidden = false;
          elements.activeProgressBar.setAttribute('aria-busy', 'true');
          if (elements.activeControlHelp) {
            elements.activeControlHelp.hidden = false;
            elements.activeControlHelp.textContent = 'При отмене распознавание прекращается, исходные аудиосегменты сохраняются на диске.';
          }

          const prog = active.progress;
          if (prog) {
            elements.activeProgressBar.value = prog.completed_chunks || 0;
            elements.activeProgressBar.max = prog.total_chunks || 1;
            elements.activeProgressLabel.textContent = `Обработано ${prog.completed_chunks}/${prog.total_chunks} кусков`;
            elements.activeElapsed.textContent = formatSec(prog.elapsed_sec);

            if (prog.eta_sec !== null && prog.eta_sec !== undefined) {
              elements.activeEtaBlock.hidden = false;
              elements.activeEta.textContent = `~${formatSec(prog.eta_sec)}`;
            } else {
              elements.activeEtaBlock.hidden = true;
            }
          }
        } else if (active.kind === 'summary') {
          recordingStartTime = null;
          updateSystemStatus('working', 'Идёт создание саммари');
          elements.btnPrimaryStart.hidden = false;
          elements.btnStopCapture.hidden = true;
          elements.btnCancelProcess.hidden = true;
          elements.activeProgressBlock.hidden = false;
          elements.activeEtaBlock.hidden = true;
          elements.activeProgressBar.removeAttribute('value');
          elements.activeProgressBar.setAttribute('aria-busy', 'true');
          elements.activeProgressLabel.textContent = 'Генерация саммари...';
          elements.activeElapsed.textContent = '—';
          if (elements.activeControlHelp) {
            elements.activeControlHelp.hidden = false;
            elements.activeControlHelp.textContent = 'Выполняется генерация саммари. Другие операции временно заблокированы.';
          }
        } else if (active.kind === 'diarizing') {
          recordingStartTime = null;
          updateSystemStatus('working', 'Идёт диаризация голосов');
          elements.btnPrimaryStart.hidden = false;
          elements.btnStopCapture.hidden = true;
          elements.btnCancelProcess.hidden = true;
          if (elements.btnCancelDiarization) elements.btnCancelDiarization.hidden = false;
          elements.activeProgressBlock.hidden = true;
          if (elements.activeDiarizationProgressBlock) elements.activeDiarizationProgressBlock.hidden = false;
          elements.activeProgressBar.setAttribute('aria-busy', 'false');
          if (elements.activeDiarizationProgressBar) {
            elements.activeDiarizationProgressBar.setAttribute('aria-busy', 'true');
            const dprog = data.diarization_progress || active.diarization_progress || active.progress;
            if (dprog && dprog.total_windows) {
              elements.activeDiarizationProgressBar.value = dprog.current_window || 0;
              elements.activeDiarizationProgressBar.max = dprog.total_windows || 1;
              if (elements.activeDiarizationProgressLabel) {
                elements.activeDiarizationProgressLabel.textContent = `Диаризация: окно ${dprog.current_window || 0} из ${dprog.total_windows}`;
              }
            } else if (elements.activeDiarizationProgressLabel) {
              elements.activeDiarizationProgressLabel.textContent = 'Диаризация речи...';
            }
          }
          elements.activeElapsed.textContent = '—';
          if (elements.activeControlHelp) {
            elements.activeControlHelp.hidden = false;
            elements.activeControlHelp.textContent = 'Выполняется разделение говорящих по окнам. Распознанный текст уже сохранён.';
          }
        } else if (active.kind === 'install') {
          recordingStartTime = null;
          updateSystemStatus('working', 'Идёт установка компонентов');
          elements.btnPrimaryStart.hidden = true;
          elements.btnStopCapture.hidden = true;
          elements.btnCancelProcess.hidden = true;
          if (elements.btnCancelDiarization) elements.btnCancelDiarization.hidden = true;
          elements.activeProgressBlock.hidden = false;
          elements.activeEtaBlock.hidden = true;
          elements.activeProgressBar.removeAttribute('value');
          elements.activeProgressBar.setAttribute('aria-busy', 'true');
          const inst = data.installer || active.installer;
          if (inst) {
            elements.activeProgressLabel.textContent = `${inst.phase || 'Установка'}: ${inst.message || ''} (${inst.progress || 0}%)`;
          } else {
            elements.activeProgressLabel.textContent = 'Установка компонентов Whisper...';
          }
          elements.activeElapsed.textContent = '—';
          if (elements.activeControlHelp) {
            elements.activeControlHelp.hidden = false;
            elements.activeControlHelp.textContent = 'Выполняется фоновая загрузка и настройка компонентов Whisper. Запись и распознавание временно заблокированы.';
          }
        }
      } else {
        elements.activeProgressBar.setAttribute('aria-busy', 'false');
        if (elements.activeDiarizationProgressBar) elements.activeDiarizationProgressBar.setAttribute('aria-busy', 'false');
        activeSessionId = null;
        activeOperationKind = null;
        recordingStartTime = null;
        updateSystemStatus('idle', 'Готов к работе');
        elements.activeWorkSection.hidden = true;
        elements.btnPrimaryStart.hidden = false;
        elements.btnStopCapture.hidden = true;
        elements.btnCancelProcess.hidden = true;
        if (elements.btnCancelDiarization) elements.btnCancelDiarization.hidden = true;
        if (elements.activeDiarizationProgressBlock) elements.activeDiarizationProgressBlock.hidden = true;
        if (elements.activeControlHelp) {
          elements.activeControlHelp.hidden = true;
        }
      }

      // Render unified Sessions table (R11, B01: adapt from status or separate /api/sessions)
      let sessionsList = data.sessions;
      if (!Array.isArray(sessionsList)) {
        if (Array.isArray(data.sessions_list)) {
          sessionsList = data.sessions_list;
        } else {
          try {
            const sessData = await apiGet('/api/sessions');
            if (sessData && Array.isArray(sessData.sessions)) {
              sessionsList = sessData.sessions;
            }
          } catch (_) {}
        }
      }
      if (Array.isArray(sessionsList)) {
        renderSessionsTable(sessionsList);
      }

      // Operational log update (B01: support both logs and recent_logs)
      const logsList = data.logs || data.recent_logs;
      if (Array.isArray(logsList) && logsList.length > 0) {
        const consoleEl = elements.logConsole;
        const wasScrolledToBottom = consoleEl.scrollHeight - consoleEl.clientHeight <= consoleEl.scrollTop + 20;
        consoleEl.textContent = logsList.join('\n');
        if (wasScrolledToBottom) {
          consoleEl.scrollTop = consoleEl.scrollHeight;
        }
      }

      // Update tools dependency status for import (FFmpeg & ffprobe) and transcription
      if (data.tools) {
        serverTools = data.tools;
        updateToolWarning();
        updateWhisperModelWarning();
      }

      // Update installer progress in Settings modal if open
      if (data.installer && typeof renderInstallerState === 'function') {
        renderInstallerState(data.installer);
      }
    } catch (err) {
      // Stale / Offline detection (DSH-006: warning after 2 expected refresh intervals)
      const elapsedSinceSuccess = Date.now() - lastSuccessfulPollTime;
      if (elapsedSinceSuccess > 4000) {
        isOnline = false;
        elements.connectionBanner.hidden = false;
        elements.connectionBannerText.textContent = `Соединение с сервером прервано (${Math.round(elapsedSinceSuccess / 1000)}с назад). Проверьте локальный процесс ui.py.`;
        updateSystemStatus('stale', 'Нет связи с сервером');
        document.body.classList.add('is-offline');
      }
    } finally {
      isPollInFlight = false;
      updateActionStates();
    }
  }

  function appendLocalLog(msg) {
    if (elements.logConsole) {
      const now = new Date().toLocaleTimeString();
      const line = `[${now}] ${msg}`;
      elements.logConsole.textContent = elements.logConsole.textConten
        ? `${elements.logConsole.textContent}\n${line}`
        : line;
      elements.logConsole.scrollTop = elements.logConsole.scrollHeight;
    }
  }

  function updateToolWarning() {
    if (!elements.uploadDependencyWarning) return;
    if (elements.sourceSelect && elements.sourceSelect.value === 'upload') {
      const missing = [];
      if (!serverTools || serverTools.ffmpeg !== true) missing.push('FFmpeg');
      if (!serverTools || serverTools.ffprobe !== true) missing.push('ffprobe');
      if (missing.length > 0) {
        elements.uploadDependencyWarning.hidden = false;
        if (elements.uploadDependencyMessage) {
          elements.uploadDependencyMessage.textContent = `Для импорта аудио/видео файлов требуются утилиты: ${missing.join(', ')}. Импорт файлов заблокирован.`;
        }
        if (elements.uploadDependencyRemediation) {
          elements.uploadDependencyRemediation.textContent = serverTools?.remediation ||
            'Установите FFmpeg (например, `brew install ffmpeg`) или поместите исполняемые файлы в каталог work/task_local_ffmpeg.';
        }
      } else {
        elements.uploadDependencyWarning.hidden = true;
      }
    } else {
      elements.uploadDependencyWarning.hidden = true;
    }
  }

  // Upload Handling with Progress, Diagnostics, and Timeou
  function handleFileUpload() {
    const file = elements.audioFileInput.files?.[0];
    if (!file) return;

    // Fail-closed guard: check all prerequisites (tools, online, no ongoing operation, initial data)
    const hasTools = hasUploadMediaTools();
    const isOperating = isAnyOperationActive();
    if (!initialDataLoaded || !isOnline || isOperating || !hasTools) {
      return;
    }

    if (elements.uploadError) {
      elements.uploadError.hidden = true;
      elements.uploadError.textContent = '';
    }
    if (elements.uploadRetryContainer) {
      elements.uploadRetryContainer.hidden = true;
    } else if (elements.btnUploadRetry) {
      elements.btnUploadRetry.hidden = true;
    }

    elements.uploadProgressContainer.hidden = false;
    elements.uploadProgressBar.value = 0;
    elements.uploadProgressText.textContent = 'Передача файла: 0%';
    isRequestInProgress = true;
    updateActionStates();

    const xhr = new XMLHttpRequest();
    xhr.timeout = 300000; // 300s timeout for large uploads
    const diarizeEn = Boolean(elements.diarizationEnabledCheck?.checked);
    const spkVal = elements.diarizationSpeakersSelect?.value || '0';
    let uploadUrl = `/api/upload?name=${encodeURIComponent(file.name)}`;
    if (diarizeEn) {
      uploadUrl += `&enable_diarize=1`;
      if (spkVal !== '0') uploadUrl += `&num_speakers=${encodeURIComponent(spkVal)}`;
    }
    xhr.open('POST', uploadUrl);
    xhr.setRequestHeader('X-CSRF-Token', csrfToken);

    xhr.upload.onprogress = (evt) => {
      if (evt.lengthComputable) {
        const pct = Math.round((evt.loaded / evt.total) * 100);
        elements.uploadProgressBar.value = pct;
        elements.uploadProgressText.textContent = pct < 100
          ? `Передача файла: ${pct}%`
          : 'Подготовка аудиофайла (нормализация 16 кГц и сегментация)...';
      }
    };

    xhr.onload = async () => {
      let isSuccess = false;
      let errorMsg = '';
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const res = JSON.parse(xhr.responseText);
          if (res && res.ok) {
            isSuccess = true;
          } else {
            errorMsg = res.error || 'Сбой импорта';
          }
        } catch {
          errorMsg = 'Некорректный ответ сервера';
        }
      } else {
        try {
          const res = JSON.parse(xhr.responseText);
          errorMsg = res.error || `HTTP ${xhr.status}`;
        } catch {
          errorMsg = `Ошибка сервера: HTTP ${xhr.status}`;
        }
      }

      if (isSuccess) {
        elements.uploadProgressBar.value = 100;
        elements.uploadProgressText.textContent = 'Файл успешно импортирован. Сессия готова к распознаванию.';
        if (elements.uploadError) elements.uploadError.hidden = true;
        if (elements.uploadRetryContainer) {
          elements.uploadRetryContainer.hidden = true;
        } else if (elements.btnUploadRetry) {
          elements.btnUploadRetry.hidden = true;
        }
        appendLocalLog(`[ИНФО] Файл ${file.name} успешно импортирован.`);
        elements.audioFileInput.value = '';
        setTimeout(() => {
          elements.uploadProgressContainer.hidden = true;
          elements.uploadContainer.hidden = true;
          elements.sourceSelect.value = 'zoom';
          loadPreflight(false);
        }, 1200);
        await pollStatus();
      } else {
        elements.uploadProgressBar.value = 0;
        elements.uploadProgressText.textContent = '';
        elements.uploadProgressContainer.hidden = true;

        // Human-friendly message per SLOP-011 / SLOP-014
        let userFriendlyMsg = 'Не удалось импортировать файл. Проверьте формат и целостность медиафайла.';
        if (xhr.status === 409) {
          if (errorMsg.includes('duration')) {
            userFriendlyMsg = 'Не удалось определить длительность аудиодорожки. Убедитесь, что файл содержит корректный звук.';
          } else if (errorMsg.includes('dependencies') || errorMsg.includes('FFmpeg') || errorMsg.includes('ffprobe') || errorMsg.includes('зависимости')) {
            userFriendlyMsg = 'Для импорта требуются установленные утилиты FFmpeg и ffprobe.';
          } else {
            userFriendlyMsg = 'Не удалось подготовить файл к обработке. Сервер отклонил запрос.';
          }
        } else if (xhr.status === 413 || errorMsg.includes('maximum allowed size') || errorMsg.includes('превышает допустимый размер')) {
          userFriendlyMsg = 'Размер файла превышает допустимый лимит (4 ГБ). Выберите файл меньшего размера.';
        } else if (errorMsg.includes('disk') || errorMsg.includes('space') || errorMsg.includes('диске') || errorMsg.includes('места')) {
          userFriendlyMsg = 'Недостаточно свободного места на диске для сохранения и обработки файла.';
        } else if (xhr.status >= 500) {
          userFriendlyMsg = 'Внутренняя ошибка сервера при обработке файла. Проверьте журнал или повторите попытку.';
        }

        const displayErr = userFriendlyMsg;
        if (elements.uploadError) {
          elements.uploadError.textContent = displayErr;
          elements.uploadError.hidden = false;
          elements.uploadError.focus();
        }
        if (elements.uploadRetryContainer) {
          elements.uploadRetryContainer.hidden = false;
        } else if (elements.btnUploadRetry) {
          elements.btnUploadRetry.hidden = false;
        }
        appendLocalLog(`[ОШИБКА] Импорт ${file.name} не удался: ${errorMsg}`);
      }
      isRequestInProgress = false;
      updateActionStates();
    };

    xhr.onerror = () => {
      const userFriendlyMsg = 'Ошибка сетевого соединения при загрузке файла. Проверьте подключение к серверу.';
      if (elements.uploadError) {
        elements.uploadError.textContent = userFriendlyMsg;
        elements.uploadError.hidden = false;
        elements.uploadError.focus();
      }
      if (elements.uploadRetryContainer) {
        elements.uploadRetryContainer.hidden = false;
      } else if (elements.btnUploadRetry) {
        elements.btnUploadRetry.hidden = false;
      }
      appendLocalLog(`[ОШИБКА] Сетевой сбой при отправке файла ${file.name}`);
      elements.uploadProgressBar.value = 0;
      elements.uploadProgressContainer.hidden = true;
      isRequestInProgress = false;
      updateActionStates();
    };

    xhr.ontimeout = () => {
      const userFriendlyMsg = 'Превышено время ожидания ответа сервера при загрузке файла.';
      if (elements.uploadError) {
        elements.uploadError.textContent = userFriendlyMsg;
        elements.uploadError.hidden = false;
        elements.uploadError.focus();
      }
      if (elements.uploadRetryContainer) {
        elements.uploadRetryContainer.hidden = false;
      } else if (elements.btnUploadRetry) {
        elements.btnUploadRetry.hidden = false;
      }
      appendLocalLog(`[ОШИБКА] Таймаут загрузки файла ${file.name}`);
      elements.uploadProgressBar.value = 0;
      elements.uploadProgressContainer.hidden = true;
      isRequestInProgress = false;
      updateActionStates();
    };

    xhr.onabort = () => {
      elements.uploadProgressContainer.hidden = true;
      if (elements.uploadRetryContainer) {
        elements.uploadRetryContainer.hidden = false;
      } else if (elements.btnUploadRetry) {
        elements.btnUploadRetry.hidden = false;
      }
      isRequestInProgress = false;
      updateActionStates();
    };

    xhr.send(file);
  }

  // Event Listeners
  elements.btnPrimaryStart.addEventListener('click', async () => {
    try {
      const kind = elements.sourceSelect.value;
      if (kind === 'upload') {
        alert('Выберите аудио- или видеофайл ниже и нажмите кнопку импорта.');
        return;
      }
      const devIdx = elements.deviceSelect.value ? parseInt(elements.deviceSelect.value, 10) : null;
      const titleVal = (elements.sessionTitleInput?.value || '').trim();
      const langVal = elements.languageSelect?.value || 'ru';
      const diarizeEn = Boolean(elements.diarizationEnabledCheck?.checked);
      const spkVal = elements.diarizationSpeakersSelect?.value || '0';
      const numSpk = spkVal === '0' ? null : parseInt(spkVal, 10);

      await apiPost('/api/record/start', {
        source_kind: kind,
        device_index: devIdx,
        title: titleVal || null,
        language: langVal,
        enable_diarize: diarizeEn,
        num_speakers: numSpk,
      });
      await pollStatus();
    } catch (e) {
      alert(`Не удалось начать запись: ${e.message}`);
    }
  });

  elements.btnStopCapture.addEventListener('click', async () => {
    try {
      elements.btnStopCapture.disabled = true;
      await apiPost('/api/record/stop', { session_id: activeSessionId });
      await pollStatus();
    } catch (e) {
      alert(`Ошибка остановки записи: ${e.message}`);
    }
  });

  elements.btnCancelProcess.addEventListener('click', async () => {
    try {
      await apiPost('/api/process/cancel');
      await pollStatus();
    } catch (e) {
      alert(`Ошибка отмены: ${e.message}`);
    }
  });

  if (elements.btnCancelDiarization) {
    elements.btnCancelDiarization.addEventListener('click', async () => {
      try {
        await apiPost('/api/session/diarize/cancel', { session_id: activeSessionId });
        await pollStatus();
      } catch (e) {
        alert(`Ошибка отмены диаризации: ${e.message}`);
      }
    });
  }

  if (elements.btnDiarizeSession) {
    elements.btnDiarizeSession.addEventListener('click', async () => {
      if (!inspectedSessionId) return;
      try {
        await apiPost('/api/session/diarize', { session_id: inspectedSessionId });
        await pollStatus();
      } catch (e) {
        alert(`Ошибка запуска диаризации: ${e.message}`);
      }
    });
  }

  if (elements.diarizationEnabledCheck) {
    elements.diarizationEnabledCheck.addEventListener('change', () => {
      if (elements.diarizationSpeakersSelect) {
        elements.diarizationSpeakersSelect.disabled = !elements.diarizationEnabledCheck.checked;
      }
    });
  }

  if (elements.btnStartDiarizationInstall) {
    elements.btnStartDiarizationInstall.addEventListener('click', async () => {
      try {
        elements.btnStartDiarizationInstall.disabled = true;
        if (elements.diarizationInstallerMessage) {
          elements.diarizationInstallerMessage.textContent = 'Запуск установки компонентов диаризации...';
        }
        await apiPost('/api/installer/diarize');
        await fetchSettings(true);
      } catch (e) {
        alert(`Ошибка установки: ${e.message}`);
      } finally {
        elements.btnStartDiarizationInstall.disabled = false;
      }
    });
  }

  elements.btnPreflightTest.addEventListener('click', () => loadPreflight(true));

  // Reset preflight status on device or source change with generation counter (R01, M01)
  elements.deviceSelect.addEventListener('change', () => {
    preflightGeneration++;
    lastPermissions = null; // Reset permission state so denied device A does not block device B
    lastPermissionsDevice = null;
    elements.preflightSymbol.textContent = '○';
    elements.preflightSymbol.className = 'status-symbol';
    const selectedText = elements.deviceSelect.options[elements.deviceSelect.selectedIndex]?.textContent || 'выбранное устройство';
    elements.preflightMessage.textContent = `Выбрано: ${selectedText}. Нажмите «Проверить уровень» для проверки звука.`;
    updateActionStates();
  });

  elements.sourceSelect.addEventListener('change', () => {
    preflightGeneration++;
    lastPermissions = null;
    lastPermissionsDevice = null;
    lastPreflightData = null;
    lastPreflightSourceKind = elements.sourceSelect.value;
    elements.preflightSymbol.textContent = '○';
    elements.preflightSymbol.className = 'status-symbol';
    elements.preflightMessage.textContent = 'Нажмите «Проверить уровень» для аппаратного теста звука.';
    elements.deviceSelect.innerHTML = '<option value="">Поиск устройств...</option>';
    elements.deviceSelect.value = '';
    updateActionStates();
    loadPreflight(false);
    updateToolWarning();
  });

  if (elements.btnQuickUpload) {
    elements.btnQuickUpload.addEventListener('click', () => {
      if (elements.sourceSelect) {
        elements.sourceSelect.value = 'upload';
        elements.sourceSelect.dispatchEvent(new Event('change'));
      }
      if (elements.audioFileInput) {
        elements.audioFileInput.focus();
      }
    });
  }

  elements.audioFileInput.addEventListener('change', () => {
    if (elements.uploadError) {
      elements.uploadError.hidden = true;
      elements.uploadError.textContent = '';
    }
    if (elements.uploadRetryContainer) {
      elements.uploadRetryContainer.hidden = true;
    } else if (elements.btnUploadRetry) {
      elements.btnUploadRetry.hidden = true;
    }
    updateActionStates();
  });
  elements.btnUploadSubmit.addEventListener('click', handleFileUpload);
  if (elements.btnUploadRetry) {
    elements.btnUploadRetry.addEventListener('click', handleFileUpload);
  }

  // Search & Filter listeners
  elements.sessionFilterInput.addEventListener('input', () => {
    currentPage = 1;
    renderSessionsTable(cachedSessions);
  });
  elements.statusFilterSelect.addEventListener('change', () => {
    currentPage = 1;
    renderSessionsTable(cachedSessions);
  });

  // Local in-memory transcript search without network re-fetching (R10)
  elements.transcriptSearchInput.addEventListener('input', () => {
    if (inspectedSessionData) {
      renderTranscriptEntries(inspectedSessionData.segments, inspectedSessionData.transcript);
    }
  });

  // Pagination navigation listeners
  elements.btnPrevPage.addEventListener('click', () => {
    if (currentPage > 1) {
      currentPage--;
      renderSessionsTable(cachedSessions);
    }
  });
  elements.btnNextPage.addEventListener('click', () => {
    currentPage++;
    renderSessionsTable(cachedSessions);
  });

  // Export dropdown toggle and accessible keyboard menu navigation
  const exportItems = [
    elements.exportTxtLink,
    elements.exportMdLink,
    elements.exportSrtLink,
    elements.exportVttLink,
    elements.exportJsonLink,
  ];

  elements.btnExportMenu.addEventListener('click', (evt) => {
    evt.stopPropagation();
    const isHidden = elements.exportDropdown.hidden;
    elements.exportDropdown.hidden = !isHidden;
    elements.btnExportMenu.setAttribute('aria-expanded', String(isHidden));
    if (isHidden) {
      exportItems[0]?.focus();
    }
  });

  elements.btnExportMenu.addEventListener('keydown', (evt) => {
    if (evt.key === 'ArrowDown' || evt.key === 'Enter' || evt.key === ' ') {
      evt.preventDefault();
      elements.exportDropdown.hidden = false;
      elements.btnExportMenu.setAttribute('aria-expanded', 'true');
      exportItems[0]?.focus();
    }
  });

  elements.exportDropdown.addEventListener('keydown', (evt) => {
    const activeIndex = exportItems.indexOf(document.activeElement);
    if (evt.key === 'ArrowDown') {
      evt.preventDefault();
      const nextIndex = activeIndex >= 0 ? (activeIndex + 1) % exportItems.length : 0;
      exportItems[nextIndex]?.focus();
    } else if (evt.key === 'ArrowUp') {
      evt.preventDefault();
      const prevIndex = activeIndex >= 0 ? (activeIndex - 1 + exportItems.length) % exportItems.length : exportItems.length - 1;
      exportItems[prevIndex]?.focus();
    } else if (evt.key === 'Home') {
      evt.preventDefault();
      exportItems[0]?.focus();
    } else if (evt.key === 'End') {
      evt.preventDefault();
      exportItems[exportItems.length - 1]?.focus();
    } else if (evt.key === 'Escape') {
      evt.preventDefault();
      elements.exportDropdown.hidden = true;
      elements.btnExportMenu.setAttribute('aria-expanded', 'false');
      elements.btnExportMenu.focus();
    }
  });

  document.addEventListener('click', (evt) => {
    if (elements.dropdownWrapper && !elements.dropdownWrapper.contains(evt.target)) {
      elements.exportDropdown.hidden = true;
      elements.btnExportMenu.setAttribute('aria-expanded', 'false');
    }
  });

  // Inspector tabs
  elements.tabTranscript.addEventListener('click', () => {
    elements.tabTranscript.classList.add('active');
    elements.tabTranscript.setAttribute('aria-selected', 'true');
    elements.tabSummary.classList.remove('active');
    elements.tabSummary.setAttribute('aria-selected', 'false');
    elements.panelTranscript.hidden = false;
    elements.panelSummary.hidden = true;
  });

  elements.tabSummary.addEventListener('click', () => {
    elements.tabSummary.classList.add('active');
    elements.tabSummary.setAttribute('aria-selected', 'true');
    elements.tabTranscript.classList.remove('active');
    elements.tabTranscript.setAttribute('aria-selected', 'false');
    elements.panelTranscript.hidden = true;
    elements.panelSummary.hidden = false;
  });

  // Summary Dialog (A11Y-007, CMP-030, M02, M04, M07)
  function updateSummaryModalFormState() {
    const isLocal = elements.summaryProviderSelect.value === 'none';
    const warningBox = elements.summaryModal.querySelector('.callout-warning');
    const checkboxContainer = elements.summaryModal.querySelector('.form-checkbox-container');
    if (warningBox) warningBox.hidden = isLocal;
    if (checkboxContainer) checkboxContainer.hidden = isLocal;
    updateActionStates();
  }

  elements.summaryProviderSelect.addEventListener('change', updateSummaryModalFormState);

  elements.btnRequestSummary.addEventListener('click', () => {
    elements.summaryOptInCheck.checked = false;
    updateSummaryModalFormState();
    elements.summaryModal.showModal();
  });

  elements.btnCancelSummary.addEventListener('click', () => {
    closeSummaryModal();
  });

  elements.summaryModal.addEventListener('cancel', (evt) => {
    evt.preventDefault();
    closeSummaryModal();
  });

  elements.summaryModal.addEventListener('close', () => {
    closeSummaryModal();
  });

  // Gated modal checkbox (R03: does not bypass updateActionStates)
  elements.summaryOptInCheck.addEventListener('change', () => {
    updateActionStates();
  });

  elements.summaryForm.addEventListener('submit', async (evt) => {
    evt.preventDefault();
    const tmpl = elements.summaryTemplateSelect.value;
    const prov = elements.summaryProviderSelect.value;
    const optIn = elements.summaryOptInCheck.checked === true;
    const targetSessionId = inspectedSessionId;

    closeSummaryModal();

    isRequestInProgress = true;
    updateActionStates();

    if (inspectedSessionId === targetSessionId) {
      elements.summaryView.innerHTML = '<p class="empty-cell" aria-busy="true">Генерация конспекта... Пожалуйста, подождите.</p>';
      elements.tabSummary.click();
    }

    try {
      // Bounded 180s timeout for external summary generation (R04)
      const res = await apiPost('/api/summary', {
        session_id: targetSessionId,
        provider: prov,
        template: tmpl,
        opt_in: prov === 'none' ? true : optIn,
      }, 180000);

      if (res && res.summary) {
        // M02: verify session identity before updating current inspector view
        if (inspectedSessionId === targetSessionId) {
          if (inspectedSessionData) inspectedSessionData.summary = res.summary;
          elements.summaryView.textContent = res.summary;
        }
        const sRecord = cachedSessions.find(s => s.session_id === targetSessionId);
        if (sRecord) {
          sRecord.summary = res.summary;
          sRecord.has_summary = true;
          sRecord.summary_status = 'completed';
        }
      } else if (prov === 'none') {
        if (inspectedSessionId === targetSessionId) {
          elements.summaryView.innerHTML = '<p class="empty-cell">Локальный режим: внешний ИИ-конспект отключён для сохранения конфиденциальности.</p>';
        }
      }
      await pollStatus();
    } catch (e) {
      const isTimeout = e.name === 'AbortError' || e.message?.includes('таймаут') || e.message?.includes('Timeout');
      if (inspectedSessionId === targetSessionId) {
        if (isTimeout) {
          elements.summaryView.innerHTML = '<p class="empty-cell">Запрос отправлен. Генерация выполняется на сервере (исход уточняется). Нажмите «Обновить» или откройте сессию позже.</p>';
        } else {
          elements.summaryView.innerHTML = `<p class="empty-cell error-text">Ошибка генерации: ${escapeHtml(e.message)}</p>`;
        }
      }
    } finally {
      isRequestInProgress = false;
      updateActionStates();
    }
  });

  // Queue Confirmation Modal
  let pendingQueueRemoveSessionId = null;
  let pendingQueueRemoveTriggerButton = null;
  let isClosingQueueModal = false;

  function openQueueConfirmModal(sessionId, triggerBtn) {
    pendingQueueRemoveSessionId = sessionId;
    pendingQueueRemoveTriggerButton = triggerBtn || null;
    const s = cachedSessions.find((x) => x.session_id === sessionId);
    if (elements.queueConfirmSessionName) {
      elements.queueConfirmSessionName.textContent = (s && (s.title || s.session_id)) ? (s.title || s.session_id) : sessionId;
    }
    if (elements.queueConfirmModal) {
      elements.queueConfirmModal.showModal();
      elements.btnCancelQueueRemove?.focus();
    }
  }

  function closeQueueConfirmModal() {
    if (isClosingQueueModal) return;
    isClosingQueueModal = true;
    try {
      if (elements.queueConfirmModal && elements.queueConfirmModal.open) {
        elements.queueConfirmModal.close();
      }
      if (pendingQueueRemoveTriggerButton && document.body.contains(pendingQueueRemoveTriggerButton)) {
        pendingQueueRemoveTriggerButton.focus();
      }
    } finally {
      pendingQueueRemoveSessionId = null;
      pendingQueueRemoveTriggerButton = null;
      isClosingQueueModal = false;
    }
  }

  if (elements.btnCancelQueueRemove) {
    elements.btnCancelQueueRemove.addEventListener('click', () => {
      closeQueueConfirmModal();
    });
  }

  if (elements.queueConfirmModal) {
    elements.queueConfirmModal.addEventListener('cancel', (evt) => {
      evt.preventDefault();
      closeQueueConfirmModal();
    });
    elements.queueConfirmModal.addEventListener('close', () => {
      closeQueueConfirmModal();
    });
  }

  if (elements.queueConfirmForm) {
    elements.queueConfirmForm.addEventListener('submit', async (evt) => {
      evt.preventDefault();
      const targetSessionId = pendingQueueRemoveSessionId;
      closeQueueConfirmModal();
      if (!targetSessionId) return;

      try {
        isRequestInProgress = true;
        updateActionStates();
        await apiPost('/api/session/queue/remove', { session_id: targetSessionId });
        appendLocalLog(`Запись ${targetSessionId} исключена из очереди (файлы сохранены).`);
        await pollStatus();
      } catch (e) {
        alert(`Не удалось убрать запись из очереди: ${e.message}`);
      } finally {
        isRequestInProgress = false;
        updateActionStates();
      }
    });
  }

  // Settings & Scenario Readiness Modal
  let pendingSettingsTriggerBtn = null;
  let isClosingSettingsModal = false;
  let isSettingsLoading = false;
  let currentSettingsData = null;

  function showSettingsAlert(message, type = 'info') {
    if (!elements.settingsAlert || !elements.settingsAlertText) return;
    elements.settingsAlert.className = `banner banner-${type}`;
    elements.settingsAlertText.textContent = message;
    elements.settingsAlert.hidden = false;
  }

  function hideSettingsAlert() {
    if (elements.settingsAlert) elements.settingsAlert.hidden = true;
  }

  async function openSettingsModal(triggerBtn) {
    pendingSettingsTriggerBtn = triggerBtn || elements.btnOpenSettings;
    if (elements.settingsModal) {
      hideSettingsAlert();
      elements.settingsModal.showModal();
      elements.btnCloseSettingsModal?.focus();
      await fetchSettings(false);
    }
  }

  function closeSettingsModal() {
    if (isClosingSettingsModal) return;
    isClosingSettingsModal = true;
    try {
      if (elements.settingsModal && elements.settingsModal.open) {
        elements.settingsModal.close();
      }
      if (pendingSettingsTriggerBtn && document.body.contains(pendingSettingsTriggerBtn)) {
        pendingSettingsTriggerBtn.focus();
      }
    } finally {
      pendingSettingsTriggerBtn = null;
      isClosingSettingsModal = false;
    }
  }

  function renderScenarioReadiness(data, pfData) {
    const tools = data.tools || {};
    const engine = data.engine_status || {};
    const model = data.model_status || {};

    // 1. Import scenario
    const importReady = tools.ffmpeg === true && tools.ffprobe === true;
    if (elements.badgeScenarioImport) {
      elements.badgeScenarioImport.textContent = importReady ? '✓ Готов' : '✕ Недоступен';
      elements.badgeScenarioImport.className = `readiness-badge ${importReady ? 'readiness-badge-ready' : 'readiness-badge-error'}`;
    }
    if (elements.descScenarioImport) {
      elements.descScenarioImport.textContent = importReady
        ? 'FFmpeg и ffprobe обнаружены. Импорт аудио и видео файлов готов к работе.'
        : 'Отсутствуют FFmpeg и/или ffprobe. Поместите их в work/task_local_ffmpeg или установите.';
    }

    // 2. Microphone scenario
    let micCount = 0;
    const devices = (pfData && pfData.devices) || (lastPreflightData && lastPreflightData.devices) || [];
    for (const d of devices) {
      if (isMicDevice(d)) micCount++;
    }
    const hasMic = micCount > 0;
    const micVerified = pfData?.test_result?.has_audio === true;
    if (elements.badgeScenarioMic) {
      if (micVerified) {
        elements.badgeScenarioMic.textContent = `✓ Готов (${micCount})`;
        elements.badgeScenarioMic.className = 'readiness-badge readiness-badge-ready';
      } else if (hasMic) {
        elements.badgeScenarioMic.textContent = 'Найден, требуется проверка звука';
        elements.badgeScenarioMic.className = 'readiness-badge readiness-badge-warning';
      } else {
        elements.badgeScenarioMic.textContent = '✕ Не найден';
        elements.badgeScenarioMic.className = 'readiness-badge readiness-badge-error';
      }
    }
    if (elements.descScenarioMic) {
      elements.descScenarioMic.textContent = hasMic
        ? `Обнаружено микрофонов: ${micCount}. Проверьте уровень звука кнопкой «Проверить звук» на стартовом экране.`
        : 'Устройства ввода звука не обнаружены. Подключите микрофон или проверьте разрешения macOS.';
    }

    // 3. System sound / Zoom scenario
    let hasBlackHole = false;
    for (const d of devices) {
      if (isBlackHoleDevice(d)) {
        hasBlackHole = true;
        break;
      }
    }
    const systemVerified = pfData?.kind === 'blackhole' && pfData?.test_result?.has_audio === true;
    if (elements.badgeScenarioSystem) {
      if (systemVerified) {
        elements.badgeScenarioSystem.textContent = '✓ Готов';
        elements.badgeScenarioSystem.className = 'readiness-badge readiness-badge-ready';
      } else if (hasBlackHole) {
        elements.badgeScenarioSystem.textContent = 'Найден, требуется проверка звука';
        elements.badgeScenarioSystem.className = 'readiness-badge readiness-badge-warning';
      } else {
        elements.badgeScenarioSystem.textContent = '⚠ Требуется BlackHole';
        elements.badgeScenarioSystem.className = 'readiness-badge readiness-badge-warning';
      }
    }
    if (elements.descScenarioSystem) {
      elements.descScenarioSystem.textContent = hasBlackHole
        ? 'BlackHole 2ch обнаружен. Убедитесь, что настроен Multi-Output Device и проверьте захват звука кнопкой «Проверить звук».'
        : 'Устройство BlackHole 2ch не найдено. Для записи вебинаров без микрофонного эха установите BlackHole вручную.';
    }

    // 4. Transcription scenario (requires whisper-cli + model + ffmpeg + ffprobe)
    const hasWhisperAndModel = tools.whisper === true && tools.model === true;
    const hasMediaTools = tools.ffmpeg === true && tools.ffprobe === true;
    const transcribeReady = hasWhisperAndModel && hasMediaTools;
    if (elements.badgeScenarioTranscribe) {
      elements.badgeScenarioTranscribe.textContent = transcribeReady ? '✓ Готов' : '✕ Недоступен';
      elements.badgeScenarioTranscribe.className = `readiness-badge ${transcribeReady ? 'readiness-badge-ready' : 'readiness-badge-error'}`;
    }
    if (elements.descScenarioTranscribe) {
      if (transcribeReady) {
        elements.descScenarioTranscribe.textContent = `whisper-cli (${engine.version || 'v1.9.3'}) + модель ${model.state === 'verified' ? 'large-v3-turbo' : (model.path || 'готово')} + FFmpeg активны.`;
      } else if (hasWhisperAndModel && !hasMediaTools) {
        elements.descScenarioTranscribe.textContent = 'whisper-cli и модель готовы, но требуются FFmpeg и ffprobe для конвертации аудио.';
      } else {
        elements.descScenarioTranscribe.textContent = 'Требуется whisper-cli и модель large-v3-turbo (~1.6 ГБ). Нажмите «Установить компоненты» ниже.';
      }
    }
  }

  function renderInstallerState(inst) {
    if (!elements.installerBox) return;
    if (!inst) {
      elements.installerBox.hidden = true;
      return;
    }

    const isRunning = Boolean(inst.is_running || inst.status === 'running');
    const isCompleted = Boolean(inst.completed || inst.status === 'completed');
    const stage = inst.phase || inst.stage || 'установка';
    const progress = Math.round(inst.progress ?? inst.progress_percent ?? 0);
    const message = inst.message || '';
    const error = inst.error;

    if (isRunning) {
      elements.installerBox.hidden = false;
      if (elements.installerMessage) elements.installerMessage.textContent = `[${stage}] ${message || 'Выполняется операция...'}`;
      if (elements.installerProgressContainer) elements.installerProgressContainer.hidden = false;
      if (elements.installerProgressBar) elements.installerProgressBar.style.width = `${progress}%`;
      if (elements.installerProgressPercent) elements.installerProgressPercent.textContent = `${progress}%`;
      if (elements.btnStartInstall) {
        elements.btnStartInstall.disabled = true;
        elements.btnStartInstall.textContent = 'Идёт установка...';
      }
      if (elements.btnSaveSettings) elements.btnSaveSettings.disabled = true;
    } else {
      if (elements.installerProgressContainer) elements.installerProgressContainer.hidden = true;
      if (elements.btnStartInstall) {
        elements.btnStartInstall.disabled = isAnyOperationActive();
        const hasTools = serverTools && serverTools.whisper && serverTools.model;
        elements.btnStartInstall.textContent = hasTools ? 'Переустановить компоненты (~1.6 ГБ)' : 'Установить компоненты (~1.6 ГБ)';
      }
      if (elements.btnSaveSettings) elements.btnSaveSettings.disabled = isAnyOperationActive();

      if (error) {
        elements.installerBox.hidden = false;
        if (elements.installerMessage) elements.installerMessage.textContent = `Ошибка установки: ${error}`;
      } else if (isCompleted) {
        elements.installerBox.hidden = false;
        if (elements.installerMessage) elements.installerMessage.textContent = message || 'Установка компонентов успешно завершена.';
      } else {
        const hasTools = serverTools && serverTools.whisper && serverTools.model;
        elements.installerBox.hidden = hasTools;
        if (elements.installerMessage) elements.installerMessage.textContent = 'Для автономного распознавания требуется whisper.cpp и модель large-v3-turbo (~1.6 ГБ).';
      }
    }
  }

  async function fetchSettings(forceRecheck = false) {
    if (isSettingsLoading) return;
    isSettingsLoading = true;
    if (elements.btnRecheckSettings) elements.btnRecheckSettings.disabled = true;

    try {
      const url = forceRecheck ? '/api/settings?force_recheck=1' : '/api/settings';
      const data = await apiGet(url);
      currentSettingsData = data;

      // Update serverTools reference
      if (data.tools) {
        serverTools = data.tools;
        updateToolWarning();
        updateWhisperModelWarning();
      }

      // Preflight data for devices
      let pf = lastPreflightData;
      if (!pf) {
        try {
          const pfRes = await apiGet('/api/preflight?kind=mic');
          if (pfRes && pfRes.preflight) {
            pf = pfRes.preflight;
            lastPreflightData = pf;
          }
        } catch (_) {}
      }

      // Render scenarios
      renderScenarioReadiness(data, pf);

      // Render Engine & Model Details
      const eng = data.engine_status || {};
      const mod = data.model_status || {};
      const disk = data.disk || {};

      if (elements.infoWhisperVersion) elements.infoWhisperVersion.textContent = eng.version || (eng.valid ? 'Доступен' : (eng.error || 'Не найден'));
      if (elements.infoWhisperPath) elements.infoWhisperPath.textContent = eng.path || '—';
      if (elements.infoModelPath) elements.infoModelPath.textContent = mod.path || '—';
      if (elements.infoModelSize) elements.infoModelSize.textContent = mod.size_str || '0 МБ';
      if (elements.infoModelStatus) {
        let statusText = 'Не найдена';
        if (mod.state === 'verified') statusText = '✓ Проверена (официальный large-v3-turbo SHA-256)';
        else if (mod.state === 'unverified') statusText = '⚠ Не проверена (требуется проверочный запуск)';
        else if (mod.state === 'corrupt') statusText = '✕ Повреждена (неверный размер или контрольная сумма)';
        else if (mod.state === 'missing') statusText = '✕ Отсутствует';
        elements.infoModelStatus.textContent = statusText;
      }
      if (elements.infoDiskSpace) {
        elements.infoDiskSpace.textContent = `${disk.free_gb || 0} ГБ` + (disk.is_low ? ' ⚠️ Мало места на диске!' : '');
      }

      // Populate Form Fields
      const cfg = data.configured || {};
      const eff = data.effective || {};
      const ovr = data.overrides || {};

      if (elements.inputWhisperBin) elements.inputWhisperBin.value = cfg.whisper_bin || '';
      if (elements.inputModelPath) elements.inputModelPath.value = cfg.whisper_model_path || '';
      if (elements.selectLanguage) elements.selectLanguage.value = eff.language || 'ru';
      if (elements.languageSelect) elements.languageSelect.value = eff.language || 'ru';
      if (elements.checkGpuEnabled) elements.checkGpuEnabled.checked = !eff.no_gpu;
      if (elements.inputGpuThreads) elements.inputGpuThreads.value = eff.threads ?? 4;
      if (elements.inputCpuThreads) elements.inputCpuThreads.value = eff.cpu_threads ?? 2;
      if (document.getElementById('checkAutoIntro')) {
        document.getElementById('checkAutoIntro').checked = eff.enable_auto_intro !== false;
      }

      // Env Overrides Badges
      if (elements.envBadgeWhisperBin) {
        const o = ovr.whisper_bin;
        elements.envBadgeWhisperBin.hidden = !(o && o.active);
        if (o && o.active) elements.envBadgeWhisperBin.textContent = `переопределено ${o.env_var}=${o.value}`;
      }
      if (elements.envBadgeModelPath) {
        const o = ovr.whisper_model_path;
        elements.envBadgeModelPath.hidden = !(o && o.active);
        if (o && o.active) elements.envBadgeModelPath.textContent = `переопределено ${o.env_var}=${o.value}`;
      }
      if (elements.envBadgeLanguage) {
        const o = ovr.language;
        elements.envBadgeLanguage.hidden = !(o && o.active);
        if (o && o.active) elements.envBadgeLanguage.textContent = `переопределено ${o.env_var}=${o.value}`;
      }
      if (elements.envBadgeNoGpu) {
        const o = ovr.no_gpu;
        elements.envBadgeNoGpu.hidden = !(o && o.active);
        if (o && o.active) elements.envBadgeNoGpu.textContent = `переопределено ${o.env_var}=${o.value}`;
      }
      if (elements.envBadgeThreads) {
        const o = ovr.threads;
        elements.envBadgeThreads.hidden = !(o && o.active);
        if (o && o.active) elements.envBadgeThreads.textContent = `переопределено ${o.env_var}=${o.value}`;
      }
      if (elements.envBadgeCpuThreads) {
        const o = ovr.cpu_threads;
        elements.envBadgeCpuThreads.hidden = !(o && o.active);
        if (o && o.active) elements.envBadgeCpuThreads.textContent = `переопределено ${o.env_var}=${o.value}`;
      }

      // Check installer status
      try {
        const instData = await apiGet('/api/install/status');
        renderInstallerState(instData);
      } catch (_) {}

      // Check diarization status (v1.1)
      try {
        const diarData = await apiGet('/api/diarization/status');
        const dReady = diarData && diarData.ready;
        if (elements.badgeScenarioDiarize) {
          elements.badgeScenarioDiarize.textContent = dReady ? '✓ Готов' : '✕ Не установлен';
          elements.badgeScenarioDiarize.className = `readiness-badge ${dReady ? 'readiness-badge-ready' : 'readiness-badge-error'}`;
        }
        if (elements.descScenarioDiarize) {
          elements.descScenarioDiarize.textContent = dReady
            ? 'Компоненты sherpa-onnx, pyannote и eres2net готовы к локальной диаризации.'
            : 'Отсутствуют модели или библиотеки sherpa-onnx. Нажмите «Установить компоненты диаризации».';
        }
        if (elements.diarizationInstallerMessage) {
          elements.diarizationInstallerMessage.textContent = dReady
            ? 'Все компоненты диаризации (библиотека C-API, бинарник, модели сегментации и эмбеддингов) установлены.'
            : (diarData.missing?.length ? `Отсутствуют компоненты: ${diarData.missing.join(', ')}.` : 'Компоненты не установлены.');
        }
        if (elements.btnStartDiarizationInstall) {
          elements.btnStartDiarizationInstall.textContent = dReady
            ? 'Переустановить компоненты диаризации'
            : 'Установить компоненты диаризации';
        }
      } catch (_) {}

    } catch (err) {
      showSettingsAlert(`Не удалось загрузить настройки: ${err.message}`, 'error');
    } finally {
      isSettingsLoading = false;
      if (elements.btnRecheckSettings) elements.btnRecheckSettings.disabled = false;
    }
  }

  async function saveSettingsForm(evt) {
    if (evt) evt.preventDefault();
    if (isAnyOperationActive()) {
      showSettingsAlert('Невозможно сохранить настройки во время активной операции.', 'warning');
      return;
    }

    hideSettingsAlert();
    const gpuThreads = parseInt(elements.inputGpuThreads?.value || '4', 10);
    const cpuThreads = parseInt(elements.inputCpuThreads?.value || '2', 10);

    if (isNaN(gpuThreads) || gpuThreads < 1 || gpuThreads > 32) {
      showSettingsAlert('Количество потоков GPU должно быть от 1 до 32.', 'warning');
      return;
    }
    if (isNaN(cpuThreads) || cpuThreads < 1 || cpuThreads > 32) {
      showSettingsAlert('Количество потоков CPU должно быть от 1 до 32.', 'warning');
      return;
    }

    const payload = {
      whisper_bin: elements.inputWhisperBin?.value.trim() || null,
      whisper_model_path: elements.inputModelPath?.value.trim() || null,
      language: elements.selectLanguage?.value || 'ru',
      threads: gpuThreads,
      cpu_threads: cpuThreads,
      no_gpu: elements.checkGpuEnabled ? !elements.checkGpuEnabled.checked : false,
      enable_auto_intro: document.getElementById('checkAutoIntro') ? document.getElementById('checkAutoIntro').checked : true,
    };

    try {
      if (elements.btnSaveSettings) elements.btnSaveSettings.disabled = true;
      isRequestInProgress = true;
      updateActionStates();

      const res = await apiPost('/api/settings', payload);
      appendLocalLog('Настройки приложения сохранены на диске.');
      showSettingsAlert('✓ Настройки успешно сохранены!', 'success');
      setTimeout(hideSettingsAlert, 3500);

      // Re-fetch to update all status cards and view with verified paths
      await fetchSettings(true);
      await pollStatus();
    } catch (err) {
      showSettingsAlert(`Ошибка сохранения настроек: ${err.message}`, 'error');
    } finally {
      isRequestInProgress = false;
      updateActionStates();
      if (elements.btnSaveSettings) elements.btnSaveSettings.disabled = false;
    }
  }

  async function startComponentInstall() {
    if (isAnyOperationActive()) {
      showSettingsAlert('Невозможно запустить установку во время активной операции.', 'warning');
      return;
    }

    hideSettingsAlert();
    try {
      if (elements.btnStartInstall) elements.btnStartInstall.disabled = true;
      isRequestInProgress = true;
      updateActionStates();

      const res = await apiPost('/api/install/start', { force: false });
      appendLocalLog('Запущена фоновая установка компонентов Whisper...');
      showSettingsAlert('Установка компонентов запущена в фоновом режиме.', 'info');
      if (res.installer) {
        renderInstallerState(res.installer);
      }
      await pollStatus();
    } catch (err) {
      showSettingsAlert(`Не удалось запустить установку: ${err.message}`, 'error');
    } finally {
      isRequestInProgress = false;
      updateActionStates();
    }
  }

  if (elements.btnOpenSettings) {
    elements.btnOpenSettings.addEventListener('click', () => openSettingsModal(elements.btnOpenSettings));
  }
  if (elements.btnCloseSettingsModal) {
    elements.btnCloseSettingsModal.addEventListener('click', closeSettingsModal);
  }
  if (elements.btnCancelSettings) {
    elements.btnCancelSettings.addEventListener('click', closeSettingsModal);
  }
  if (elements.settingsModal) {
    elements.settingsModal.addEventListener('cancel', (evt) => {
      evt.preventDefault();
      closeSettingsModal();
    });
    elements.settingsModal.addEventListener('close', closeSettingsModal);
  }
  if (elements.settingsForm) {
    elements.settingsForm.addEventListener('submit', saveSettingsForm);
  }
  if (elements.btnRecheckSettings) {
    elements.btnRecheckSettings.addEventListener('click', () => fetchSettings(true));
  }
  if (elements.btnStartInstall) {
    elements.btnStartInstall.addEventListener('click', startComponentInstall);
  }

  // Table Sort Header Listeners with Synchronized Arrows and ARIA (R05, CMP-020)
  function updateSortHeaderIndicators() {
    document.querySelectorAll('.th-sortable').forEach((th) => {
      const btn = th.querySelector('.th-sort-btn');
      const indicator = th.querySelector('.sort-indicator');
      if (!btn || !indicator) return;

      const field = btn.getAttribute('data-sort');
      if (field === currentSortField) {
        th.setAttribute('aria-sort', currentSortDirection === 'asc' ? 'ascending' : 'descending');
        indicator.textContent = currentSortDirection === 'asc' ? '↑' : '↓';
      } else {
        th.setAttribute('aria-sort', 'none');
        indicator.textContent = '↕';
      }
    });
  }

  document.querySelectorAll('.th-sort-btn').forEach((btn) => {
    btn.addEventListener('click', (evt) => {
      evt.preventDefault();
      const field = btn.getAttribute('data-sort');
      if (currentSortField === field) {
        currentSortDirection = currentSortDirection === 'asc' ? 'desc' : 'asc';
      } else {
        currentSortField = field;
        currentSortDirection = 'desc';
      }
      currentPage = 1;
      updateSortHeaderIndicators();
      renderSessionsTable(cachedSessions);
    });
  });

  elements.btnClearLog.addEventListener('click', () => {
    elements.logConsole.textContent = 'Журнал очищен локально.';
  });

  // Safe Markdown Document Renderer for embedded manual (no innerHTML execution)
  window.renderMarkdownSafely = renderMarkdownSafely;
  function renderMarkdownSafely(rawMd, container) {
    container.innerHTML = '';
    if (!rawMd) return;

    function formatInline(text) {
      const fragment = document.createDocumentFragment();
      const tokenRegex = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^)]+\))/g;
      let lastIndex = 0;
      let match;
      while ((match = tokenRegex.exec(text)) !== null) {
        if (match.index > lastIndex) {
          fragment.appendChild(document.createTextNode(text.slice(lastIndex, match.index)));
        }
        const token = match[0];
        if (token.startsWith('**') && token.endsWith('**')) {
          const strong = document.createElement('strong');
          strong.textContent = token.slice(2, -2);
          fragment.appendChild(strong);
        } else if (token.startsWith('`') && token.endsWith('`')) {
          const code = document.createElement('code');
          code.textContent = token.slice(1, -1);
          fragment.appendChild(code);
        } else if (token.startsWith('[')) {
          const m = token.match(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/);
          if (m) {
            const a = document.createElement('a');
            a.textContent = m[1];
            a.href = m[2];
            a.target = "_blank";
            a.rel = "noopener noreferrer";
            fragment.appendChild(a);
          } else {
            fragment.appendChild(document.createTextNode(token));
          }
        }
        lastIndex = tokenRegex.lastIndex;
      }
      if (lastIndex < text.length) {
        fragment.appendChild(document.createTextNode(text.slice(lastIndex)));
      }
      return fragment;
    }

    const lines = rawMd.split('\n');
    let inCodeBlock = false;
    let codeLines = [];
    let currentList = null;

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const trimmed = line.trim();

      if (trimmed.startsWith('```')) {
        if (inCodeBlock) {
          const pre = document.createElement('pre');
          const code = document.createElement('code');
          code.textContent = codeLines.join('\n');
          pre.appendChild(code);
          container.appendChild(pre);
          codeLines = [];
          inCodeBlock = false;
        } else {
          inCodeBlock = true;
          codeLines = [];
          currentList = null;
        }
        continue;
      }

      if (inCodeBlock) {
        codeLines.push(line);
        continue;
      }

      if (!trimmed) {
        currentList = null;
        continue;
      }

      if (trimmed === '---' || trimmed === '***') {
        currentList = null;
        container.appendChild(document.createElement('hr'));
        continue;
      }

      if (trimmed.startsWith('### ')) {
        currentList = null;
        const h4 = document.createElement('h4');
        h4.appendChild(formatInline(trimmed.slice(4).trim()));
        container.appendChild(h4);
        continue;
      }
      if (trimmed.startsWith('## ')) {
        currentList = null;
        const h3 = document.createElement('h3');
        h3.appendChild(formatInline(trimmed.slice(3).trim()));
        container.appendChild(h3);
        continue;
      }
      if (trimmed.startsWith('# ')) {
        currentList = null;
        const h2 = document.createElement('h2');
        h2.appendChild(formatInline(trimmed.slice(2).trim()));
        container.appendChild(h2);
        continue;
      }

      if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
        if (!currentList) {
          currentList = document.createElement('ul');
          container.appendChild(currentList);
        }
        const li = document.createElement('li');
        li.appendChild(formatInline(trimmed.slice(2).trim()));
        currentList.appendChild(li);
        continue;
      }

      currentList = null;
      const p = document.createElement('p');
      p.appendChild(formatInline(trimmed));
      container.appendChild(p);
    }
  }

  function addAriaDescribedBy(targetEl, id) {
    if (!targetEl || !id) return;
    const current = targetEl.getAttribute('aria-describedby') || '';
    const tokens = current.split(/\s+/).filter(Boolean);
    if (!tokens.includes(id)) {
      tokens.push(id);
      targetEl.setAttribute('aria-describedby', tokens.join(' '));
    }
  }

  function removeAriaDescribedBy(targetEl, id) {
    if (!targetEl || !id) return;
    const current = targetEl.getAttribute('aria-describedby') || '';
    const tokens = current.split(/\s+/).filter(t => t && t !== id);
    if (tokens.length > 0) {
      targetEl.setAttribute('aria-describedby', tokens.join(' '));
    } else {
      targetEl.removeAttribute('aria-describedby');
    }
  }

  let helpDocLoaded = false;
  let helpDocLoadGeneration = 0;
  let helpDocActiveController = null;
  async function loadHelpContent() {
    if (helpDocLoaded) return;
    if (!elements.helpDocContainer) return;

    if (elements.helpDocLoading) elements.helpDocLoading.hidden = false;
    if (elements.helpDocContent) elements.helpDocContent.hidden = true;
    if (elements.helpDocError) elements.helpDocError.hidden = true;

    if (helpDocActiveController) {
      try { helpDocActiveController.abort(); } catch (_) {}
      helpDocActiveController = null;
    }

    const currentGen = ++helpDocLoadGeneration;
    const controller = new AbortController();
    helpDocActiveController = controller;

    let timeoutId = null;
    try {
      timeoutId = setTimeout(() => {
        controller.abort();
      }, 6000);

      const resp = await fetch('/docs/USER_GUIDE_RU.md', { signal: controller.signal });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const rawMd = await resp.text();

      // Guard against stale response if another retry started
      if (currentGen !== helpDocLoadGeneration) return;

      renderMarkdownSafely(rawMd, elements.helpDocContent);
      if (elements.helpDocLoading) elements.helpDocLoading.hidden = true;
      if (elements.helpDocContent) elements.helpDocContent.hidden = false;
      helpDocLoaded = true;
    } catch (err) {
      if (currentGen !== helpDocLoadGeneration) return;

      if (elements.helpDocLoading) elements.helpDocLoading.hidden = true;
      if (elements.helpDocError) {
        const msg = err.name === 'AbortError' ? 'Превышено время ожидания 6 сек' : err.message;
        if (elements.helpDocErrorText) {
          elements.helpDocErrorText.textContent = `Не удалось загрузить руководство (${msg}). Вы можете нажать «Повторить загрузку» или открыть USER_GUIDE_RU.md по ссылкам в шапке панели.`;
        } else {
          elements.helpDocError.textContent = `Не удалось загрузить руководство (${msg}). Вы можете открыть или скачать USER_GUIDE_RU.md по ссылкам в шапке панели.`;
        }
        elements.helpDocError.hidden = false;
      }
    } finally {
      if (timeoutId) {
        clearTimeout(timeoutId);
      }
      if (helpDocActiveController === controller) {
        helpDocActiveController = null;
      }
    }
  }

  if (elements.btnRetryHelpDoc) {
    elements.btnRetryHelpDoc.addEventListener('click', () => {
      helpDocLoaded = false;
      loadHelpContent();
    });
  }

  // Accessible Floating Tooltip System
  let hideTooltipGlobal = null;
  function initTooltipSystem() {
    let activeTarget = null;
    let activeTooltip = null;
    let hideTimer = null;

    function getTargetTooltipEl(target) {
      const openDialog = target ? target.closest('dialog[open]') : null;
      if (openDialog) {
        const dTip = openDialog.querySelector('.app-floating-tooltip');
        if (dTip) return dTip;
      }
      return elements.appGlobalTooltip;
    }

    function getTooltipData(target) {
      if (!target || !(target instanceof Element)) return null;
      const el = target.closest('button, [role="button"], a[download], a[target], .th-sort-btn, input[type="button"], input[type="submit"], [data-tooltip]');
      if (!el) return null;

      const isDisabled = Boolean(el.disabled || el.getAttribute('aria-disabled') === 'true');
      const actionDesc = el.getAttribute('data-tooltip') || el.getAttribute('aria-label') || '';
      const disabledReason = el.getAttribute('data-tooltip-disabled') || el.getAttribute('title') || '';

      if (!actionDesc && !disabledReason) return null;
      return { el, isDisabled, actionDesc, disabledReason };
    }

    function showTooltip(target) {
      clearTimeout(hideTimer);
      const data = getTooltipData(target);
      if (!data) {
        hideTooltip();
        return;
      }

      const { el, isDisabled, actionDesc, disabledReason } = data;
      const tooltip = getTargetTooltipEl(el);
      if (!tooltip) return;

      let html = '';
      if (isDisabled) {
        if (disabledReason) {
          html += `<span class="tooltip-reason">🔒 Блокировка: ${escapeHtml(disabledReason)}</span>`;
        }
        if (actionDesc) {
          html += `<span class="tooltip-action">Действие: ${escapeHtml(actionDesc)}</span>`;
        }
      } else {
        html = `<span class="tooltip-action">${escapeHtml(actionDesc || disabledReason)}</span>`;
      }

      if (!html) {
        hideTooltip();
        return;
      }

      if (activeTooltip && activeTooltip !== tooltip) {
        activeTooltip.classList.remove('is-visible');
        activeTooltip.setAttribute('aria-hidden', 'true');
      }
      if (activeTarget && activeTarget !== el && activeTooltip && activeTooltip.id) {
        removeAriaDescribedBy(activeTarget, activeTooltip.id);
      }

      tooltip.innerHTML = html;
      tooltip.setAttribute('aria-hidden', 'false');
      tooltip.classList.add('is-visible');

      activeTarget = el;
      activeTooltip = tooltip;
      if (tooltip.id) {
        addAriaDescribedBy(el, tooltip.id);
      }

      const rect = el.getBoundingClientRect();
      const tooltipRect = tooltip.getBoundingClientRect();

      let top = rect.top - tooltipRect.height - 8;
      if (top < 8) {
        top = rect.bottom + 8;
      }
      if (top + tooltipRect.height > window.innerHeight - 8) {
        top = Math.max(8, window.innerHeight - tooltipRect.height - 8);
      }

      let left = rect.left + (rect.width - tooltipRect.width) / 2;
      left = Math.max(8, Math.min(window.innerWidth - tooltipRect.width - 8, left));

      tooltip.style.top = `${Math.round(top)}px`;
      tooltip.style.left = `${Math.round(left)}px`;
    }

    function hideTooltip() {
      if (activeTooltip) {
        activeTooltip.classList.remove('is-visible');
        activeTooltip.setAttribute('aria-hidden', 'true');
      }
      if (elements.appGlobalTooltip) {
        elements.appGlobalTooltip.classList.remove('is-visible');
        elements.appGlobalTooltip.setAttribute('aria-hidden', 'true');
      }
      if (elements.dialogTooltip) {
        elements.dialogTooltip.classList.remove('is-visible');
        elements.dialogTooltip.setAttribute('aria-hidden', 'true');
      }
      if (activeTarget && activeTooltip && activeTooltip.id) {
        removeAriaDescribedBy(activeTarget, activeTooltip.id);
      }
      activeTarget = null;
      activeTooltip = null;
    }
    hideTooltipGlobal = hideTooltip;

    const handleEnter = (e) => {
      const data = getTooltipData(e.target);
      if (!data) return;
      if (activeTarget === data.el && activeTooltip && activeTooltip.classList.contains('is-visible')) {
        return;
      }
      showTooltip(data.el);
    };

    const handleLeave = (e) => {
      if (!activeTarget) return;
      if (e.relatedTarget && (activeTarget === e.relatedTarget || activeTarget.contains(e.relatedTarget))) {
        return;
      }
      if (e.target === activeTarget || activeTarget.contains(e.target)) {
        hideTooltip();
      }
    };

    document.addEventListener('pointerenter', handleEnter, true);
    document.addEventListener('pointerover', handleEnter, true);

    document.addEventListener('pointerleave', handleLeave, true);
    document.addEventListener('pointerout', handleLeave, true);

    document.addEventListener('focusin', (e) => {
      showTooltip(e.target);
    }, true);

    document.addEventListener('focusout', (e) => {
      if (!activeTarget) return;
      if (e.relatedTarget && (activeTarget === e.relatedTarget || activeTarget.contains(e.relatedTarget))) {
        return;
      }
      if (e.target === activeTarget || activeTarget.contains(e.target)) {
        hideTooltip();
      }
    }, true);

    document.addEventListener('click', (e) => {
      if (document.body.classList.contains('show-button-hints')) {
        if (elements.btnToggleHintsMode && (e.target === elements.btnToggleHintsMode || elements.btnToggleHintsMode.contains(e.target))) {
          return;
        }
        const data = getTooltipData(e.target);
        if (data) {
          e.preventDefault();
          e.stopPropagation();
          e.stopImmediatePropagation();
          showTooltip(data.el);
          clearTimeout(hideTimer);
          hideTimer = setTimeout(hideTooltip, 4000);
          return;
        }
      }
    }, true);

    window.addEventListener('scroll', () => {
      if (activeTooltip && activeTooltip.classList.contains('is-visible') && activeTarget) {
        const rect = activeTarget.getBoundingClientRect();
        if (rect.bottom < 0 || rect.top > window.innerHeight) {
          hideTooltip();
        } else {
          const tooltipRect = activeTooltip.getBoundingClientRect();
          let top = rect.top - tooltipRect.height - 8;
          if (top < 8) top = rect.bottom + 8;
          if (top + tooltipRect.height > window.innerHeight - 8) {
            top = Math.max(8, window.innerHeight - tooltipRect.height - 8);
          }
          let left = rect.left + (rect.width - tooltipRect.width) / 2;
          left = Math.max(8, Math.min(window.innerWidth - tooltipRect.width - 8, left));
          activeTooltip.style.top = `${Math.round(top)}px`;
          activeTooltip.style.left = `${Math.round(left)}px`;
        }
      }
    }, { passive: true });
  }

  // Help Panel & Features Document Toggle
  if (elements.btnToggleHelp) {
    elements.btnToggleHelp.addEventListener('click', () => {
      const isHidden = elements.helpPanel.hidden;
      elements.helpPanel.hidden = !isHidden;
      elements.btnToggleHelp.setAttribute('aria-expanded', String(isHidden));
      if (isHidden) {
        loadHelpContent();
        elements.btnCloseHelpPanel?.focus();
      }
    });
  }

  if (elements.btnCloseHelpPanel) {
    elements.btnCloseHelpPanel.addEventListener('click', () => {
      elements.helpPanel.hidden = true;
      elements.btnToggleHelp.setAttribute('aria-expanded', 'false');
      elements.btnToggleHelp.focus();
    });
  }

  // Button Hints Mode Toggle
  if (elements.btnToggleHintsMode) {
    elements.btnToggleHintsMode.addEventListener('click', () => {
      document.body.classList.toggle('show-button-hints');
      const isHints = document.body.classList.contains('show-button-hints');
      elements.btnToggleHintsMode.setAttribute('aria-pressed', String(isHints));
      if (elements.btnToggleHintsModeText) {
        elements.btnToggleHintsModeText.textContent = isHints
          ? 'Режим подсказок: вкл (безопасный просмотр)'
          : 'Режим подсказок: выкл';
      }
    });
  }

  // Close Inspector Button
  if (elements.btnCloseInspector) {
    elements.btnCloseInspector.addEventListener('click', () => {
      elements.inspectorSection.hidden = true;
      if (elements.inspectorErrorBanner) elements.inspectorErrorBanner.hidden = true;
      if (elements.inspectorErrorText) elements.inspectorErrorText.textContent = '';
      inspectedSessionData = null;
      inspectedSessionId = null;
      elements.sessionFilterInput?.focus();
    });
  }


  // Implementation of btnConnectionRetry
  const btnConnectionRetry = document.getElementById('btnConnectionRetry');
  if (btnConnectionRetry) {
    btnConnectionRetry.addEventListener('click', () => {
      if (elements.connectionBannerText) {
        elements.connectionBannerText.textContent = 'Подключение...';
      }
      pollStatus();
    });
  }

  // Implementation of btnHeaderUpload
  if (elements.btnHeaderUpload) {
    elements.btnHeaderUpload.addEventListener('click', () => {
      elements.sourceSelect.value = 'upload';
      elements.sourceSelect.dispatchEvent(new Event('change'));
    });
  }

  // Implementation of Bug Report Modal
  if (elements.btnBugReportOpen && elements.bugReportModal) {
    elements.btnBugReportOpen.addEventListener('click', () => {
      const template = `**⚠️ ВНИМАНИЕ: Конфиденциальность данных**
Перед отправкой убедитесь, что вы удалили все личные или конфиденциальные данные (записи, расшифровки, логи). НЕ прикрепляйте аудио или текстовые файлы, содержащие чувствительную информацию.

**Версия macOS и приложения:**
(укажите)

**Источник ввода / Тип файла:**
${elements.sourceSelect ? elements.sourceSelect.value : 'неизвестно'}

**Шаги для воспроизведения:**
1.
2.
3.

**Ожидаемый результат:**


**Фактический результат и точная ошибка:**


**Состояние микрофона / BlackHole:**
Микрофон: ${lastPermissions}, Устройство: ${lastPermissionsDevice}

**Логи или экспорт:**
`;
      elements.bugReportTemplateContent.value = template;
      elements.bugReportModal.showModal();
      elements.btnBugReportCopy.focus();
    });

    elements.btnBugReportClose.addEventListener('click', () => {
      elements.bugReportModal.close();
    });

    elements.bugReportModal.addEventListener('close', () => {
      elements.btnBugReportOpen.focus();
    });

    elements.btnBugReportCopy.addEventListener('click', () => {
      navigator.clipboard.writeText(elements.bugReportTemplateContent.value).then(() => {
        elements.bugReportStatusFeedback.textContent = 'Шаблон скопирован!';
        setTimeout(() => { elements.bugReportStatusFeedback.textContent = ''; }, 3000);
      });
    });
  }

  // Implementation of Onboarding Checklist
  if (elements.onboardingChecklist) {
    const isHidden = localStorage.getItem('rech_hide_onboarding') === 'true';
    elements.onboardingChecklist.style.display = isHidden ? 'none' : 'block';

    if (elements.btnToggleOnboarding) {
        elements.btnToggleOnboarding.setAttribute('aria-pressed', String(!isHidden));
        elements.btnToggleOnboardingText.textContent = isHidden ? 'Показать подсказки' : 'Скрыть подсказки';

        elements.btnToggleOnboarding.addEventListener('click', () => {
            const currentlyHidden = elements.onboardingChecklist.style.display === 'none';
            elements.onboardingChecklist.style.display = currentlyHidden ? 'block' : 'none';
            localStorage.setItem('rech_hide_onboarding', String(!currentlyHidden));
            elements.btnToggleOnboarding.setAttribute('aria-pressed', String(currentlyHidden));
            elements.btnToggleOnboardingText.textContent = currentlyHidden ? 'Скрыть подсказки' : 'Показать подсказки';
        });
    }

    if (elements.btnDismissOnboarding) {
        elements.btnDismissOnboarding.addEventListener('click', () => {
            elements.onboardingChecklist.style.display = 'none';
            localStorage.setItem('rech_hide_onboarding', 'true');
            if (elements.btnToggleOnboarding) {
                elements.btnToggleOnboarding.setAttribute('aria-pressed', 'false');
                elements.btnToggleOnboardingText.textContent = 'Показать подсказки';
            }
        });
    }
  }

// Keyboard shortcut: Escape closes modal/dropdown/tooltip/help (A11Y-007, M04)
  document.addEventListener('keydown', (evt) => {
    if (evt.key === 'Escape') {
      if (hideTooltipGlobal) {
        hideTooltipGlobal();
      }
      if (!elements.exportDropdown.hidden) {
        elements.exportDropdown.hidden = true;
        elements.btnExportMenu.setAttribute('aria-expanded', 'false');
        elements.btnExportMenu.focus();
      } else if (elements.summaryModal && elements.summaryModal.open) {
        evt.preventDefault();
        closeSummaryModal();
      } else if (elements.queueConfirmModal && elements.queueConfirmModal.open) {
        evt.preventDefault();
        closeQueueConfirmModal();
      } else if (elements.settingsModal && elements.settingsModal.open) {
        evt.preventDefault();
        closeSettingsModal();
      } else if (elements.helpPanel && !elements.helpPanel.hidden) {
        elements.helpPanel.hidden = true;
        elements.btnToggleHelp.setAttribute('aria-expanded', 'false');
        elements.btnToggleHelp.focus();
      }
    }
  });

  // Periodic elapsed timer for live recording & freshness watchdog (GAP-PROGRESS, DSH-006, M02)
  setInterval(() => {
    if (activeOperationKind === 'recording' && recordingStartTime) {
      const elapsed = Math.max(0, Math.floor(Date.now() / 1000 - recordingStartTime));
      elements.activeElapsed.textContent = formatSec(elapsed);
    }
    checkFreshnessWatchdog();
  }, 1000);

  // Initialization
  initTooltipSystem();
  updateSortHeaderIndicators();
  loadPreflight(false);
  fetchSettings(false);
  pollStatus();
  pollIntervalId = setInterval(pollStatus, 2000);
})();
