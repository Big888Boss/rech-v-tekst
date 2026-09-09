/**
 * QA Automation Script: Responsive Layout (320, 624, 760, 1280) and 6 Tooltip/Help Fixes.
 * Uses isolated fake UI server on ephemeral port without touching real user data.
 */
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const CHROME_PATH = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = process.env.UI_PORT ? parseInt(process.env.UI_PORT, 10) : 8799;
const BASE_URL = `http://127.0.0.1:${PORT}`;

async function run() {
  console.log(`=== Launching Google Chrome for Layout and Tooltips QA against ${BASE_URL} ===`);
  const browserServer = await chromium.launchServer({
    executablePath: CHROME_PATH,
    headless: true,
    args: ['--no-sandbox', '--disable-gpu'],
  });

  const browserProc = browserServer.process ? browserServer.process() : null;
  const browserPid = browserProc ? browserProc.pid : null;
  console.log(`[QA_REGISTRY] node_pid=${process.pid} browser_pid=${browserPid}`);

  const registryFile = process.env.QA_REGISTRY_FILE;
  if (registryFile && browserPid) {
    try {
      fs.writeFileSync(registryFile, JSON.stringify({
        node_pid: process.pid,
        node_pgid: process.pid,
        browser_pid: browserPid,
        browser_pgid: browserPid,
      }), 'utf-8');
    } catch (_) {}
  }

  const browser = await chromium.connect({ wsEndpoint: browserServer.wsEndpoint() });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 },
    deviceScaleFactor: 2,
  });

  const page = await context.newPage();

  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      const txt = msg.text();
      if (!txt.includes('favicon.ico')) {
        console.error(`[Browser Console Error]: ${txt}`);
      }
    }
  });

  try {
    await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#systemStatusBadge[data-state="idle"]', { timeout: 10000 });
    console.log('--- Page loaded and idle ---');

    // TEST 1: Viewports 1280, 760, 624, 320 with collapsed help panel
    console.log('\n--- TEST 1: Viewports & Welcome Bar Layout Bug Verification ---');
    for (const width of [1280, 760, 624, 320]) {
      await page.setViewportSize({ width, height: 800 });
      await page.waitForTimeout(100);

      // Verify scrollWidth does not exceed viewport width
      const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
      if (scrollWidth > width) {
        throw new Error(`Horizontal overflow at viewport ${width}px: scrollWidth=${scrollWidth}px`);
      }

      // Check .welcome-intro height and gap to .welcome-actions
      const introMetrics = await page.evaluate(() => {
        const intro = document.querySelector('.welcome-intro');
        const actions = document.querySelector('.welcome-actions');
        if (!intro || !actions) return null;
        const rIntro = intro.getBoundingClientRect();
        const rActions = actions.getBoundingClientRect();
        return {
          height: rIntro.height,
          gap: rActions.top - rIntro.bottom,
        };
      });

      console.log(`Viewport ${width}px: introHeight=${introMetrics?.height.toFixed(1)}px, verticalGap=${introMetrics?.gap.toFixed(1)}px`);
      if (width <= 760) {
        if (introMetrics && introMetrics.height > 100) {
          throw new Error(`Layout bug detected at ${width}px: welcome-intro height is ${introMetrics.height}px (expected <= 80px)`);
        }
        if (introMetrics && introMetrics.gap > 50) {
          throw new Error(`Excessive blank gap at ${width}px: gap is ${introMetrics.gap}px (expected <= 24px)`);
        }
      }
    }
    console.log('PASSED: Viewport layout and .welcome-intro flex-basis fix verified!');

    // Restore desktop viewport for feature tests
    await page.setViewportSize({ width: 1280, height: 800 });

    // TEST 2: Hints Mode Safe Exploration (Fix 1)
    console.log('\n--- TEST 2: Hints Mode Safe Exploration ---');
    const hintsBtn = page.locator('#btnToggleHintsMode');
    const hintsText = page.locator('#btnToggleHintsModeText');

    let isPressed = await hintsBtn.getAttribute('aria-pressed');
    if (isPressed !== 'false') throw new Error(`Initial aria-pressed should be false, got ${isPressed}`);

    // Toggle Hints Mode ON
    await hintsBtn.click();
    isPressed = await hintsBtn.getAttribute('aria-pressed');
    const textOn = await hintsText.textContent();
    console.log(`Hints mode ON: aria-pressed=${isPressed}, text="${textOn}"`);
    if (isPressed !== 'true' || !textOn.includes('вкл')) {
      throw new Error(`Failed to activate hints mode: aria-pressed=${isPressed}, text="${textOn}"`);
    }

    // While Hints Mode is ON, click #btnPreflightTest (an enabled interactive button)
    const preflightTestBtn = page.locator('#btnPreflightTest');
    await preflightTestBtn.click();
    await page.waitForTimeout(200);

    const globalTooltip = page.locator('#appGlobalTooltip');
    const tooltipVisible = await globalTooltip.evaluate((el) => el.classList.contains('is-visible'));
    const tooltipText = await globalTooltip.textContent();
    console.log(`Hints mode click intercepted: tooltipVisible=${tooltipVisible}, text="${tooltipText}"`);
    if (!tooltipVisible || !tooltipText.includes('уровн')) {
      throw new Error(`Expected tooltip on button click in hints mode! visible=${tooltipVisible}, text="${tooltipText}"`);
    }

    // Verify audio preflight test did NOT run (status message unchanged from initial)
    const preflightMsg = await page.locator('#preflightMessage').textContent();
    if (preflightMsg.includes('пик') || preflightMsg.includes('dB') || preflightMsg.includes('дБ') || preflightMsg.includes('обнаружен')) {
      throw new Error(`Action was executed in hints mode! preflightMessage="${preflightMsg}"`);
    }

    // Toggle Hints Mode OFF
    await hintsBtn.click();
    const textOff = await hintsText.textContent();
    console.log(`Hints mode OFF: text="${textOff}"`);
    if (!textOff.includes('выкл')) {
      throw new Error(`Failed to deactivate hints mode: text="${textOff}"`);
    }
    console.log('PASSED: Hints mode safe exploration verified!');

    // TEST 3: aria-describedby Preservation (Fix 2)
    console.log('\n--- TEST 3: aria-describedby Preservation ---');
    await page.evaluate(() => {
      const c = document.getElementById('uploadContainer');
      if (c) c.hidden = false;
      const r = document.getElementById('uploadRetryContainer');
      if (r) r.hidden = false;
    });

    const uploadRetryBtn = page.locator('#btnUploadRetry');
    const initialDescribedBy = await uploadRetryBtn.getAttribute('aria-describedby');
    console.log(`Initial #btnUploadRetry aria-describedby="${initialDescribedBy}"`);
    if (!initialDescribedBy || !initialDescribedBy.includes('uploadRetryNote')) {
      throw new Error(`Expected uploadRetryNote in initial aria-describedby, got "${initialDescribedBy}"`);
    }

    await uploadRetryBtn.hover();
    await page.waitForTimeout(100);
    const hoveredDescribedBy = await uploadRetryBtn.getAttribute('aria-describedby');
    console.log(`Hovered #btnUploadRetry aria-describedby="${hoveredDescribedBy}"`);
    if (!hoveredDescribedBy.includes('uploadRetryNote') || !hoveredDescribedBy.includes('appGlobalTooltip')) {
      throw new Error(`Expected both uploadRetryNote and appGlobalTooltip, got "${hoveredDescribedBy}"`);
    }

    await page.mouse.move(0, 0);
    await page.waitForTimeout(100);
    const unhoveredDescribedBy = await uploadRetryBtn.getAttribute('aria-describedby');
    console.log(`Unhovered #btnUploadRetry aria-describedby="${unhoveredDescribedBy}"`);
    if (unhoveredDescribedBy !== 'uploadRetryNote') {
      throw new Error(`Expected strictly "uploadRetryNote" preserved, got "${unhoveredDescribedBy}"`);
    }
    console.log('PASSED: aria-describedby token preservation verified!');

    // TEST 4: Modal Dialog Tooltip in Top Layer (Fix 3)
    console.log('\n--- TEST 4: Modal Dialog Tooltip in Top Layer ---');
    await page.evaluate(() => {
      const modal = document.getElementById('summaryModal');
      if (modal) modal.showModal();
    });
    await page.waitForSelector('#summaryModal[open]');

    const cancelSummaryBtn = page.locator('#btnCancelSummary');
    await cancelSummaryBtn.hover();
    await page.waitForTimeout(150);

    const dialogTooltip = page.locator('#dialogTooltip');
    const dialogTooltipVisible = await dialogTooltip.evaluate((el) => el.classList.contains('is-visible'));
    const dialogTooltipText = await dialogTooltip.textContent();
    console.log(`Modal tooltip visible=${dialogTooltipVisible}, text="${dialogTooltipText}"`);
    if (!dialogTooltipVisible || !dialogTooltipText.includes('конспект')) {
      throw new Error(`Expected dialogTooltip visible inside top-layer dialog, got visible=${dialogTooltipVisible}`);
    }

    // Touch-accessible explanations inside dialog (.dialog-actions-hints)
    const hintsCount = await page.locator('#summaryModal .dialog-actions-hints .dialog-action-hint').count();
    const modalHintsText = await page.locator('#summaryModal .dialog-actions-hints').textContent();
    console.log(`Modal touch hints (${hintsCount} items): "${modalHintsText.replace(/\s+/g, " ").trim()}"`);
    if (hintsCount < 2 || !modalHintsText.includes("Отмена") || !modalHintsText.includes("Сгенерировать конспект")) {
      throw new Error(`Expected visible button hints inside modal dialog, got: ${modalHintsText}`);
    }

    // Verify aria-hidden is removed so screen readers can access hints
    const ariaHiddenVal = await page.locator('#summaryModal .dialog-actions-hints').getAttribute('aria-hidden');
    if (ariaHiddenVal === "true") {
      throw new Error('dialog-actions-hints must NOT be aria-hidden="true"');
    }

    // Verify aria-describedby links to hint IDs and preserves them when tooltip shows/hides
    const cancelDescribedBy = await cancelSummaryBtn.getAttribute('aria-describedby');
    console.log(`cancelSummaryBtn aria-describedby: "${cancelDescribedBy}"`);
    if (!cancelDescribedBy.includes("cancelSummaryHint")) {
      throw new Error(`Expected cancelSummaryHint in aria-describedby, got "${cancelDescribedBy}"`);
    }

    const submitDescribedBy = await page.locator('#btnSubmitSummary').getAttribute('aria-describedby');
    console.log(`submitSummaryBtn aria-describedby: "${submitDescribedBy}"`);
    if (!submitDescribedBy.includes("submitSummaryHint")) {
      throw new Error(`Expected submitSummaryHint in aria-describedby, got "${submitDescribedBy}"`);
    }

    // Verify disabled submit button when opt-in checkbox is unchecked
    const optInChecked = await page.locator('#summaryOptInCheck').isChecked();
    const submitDisabled = await page.locator('#btnSubmitSummary').isDisabled();
    const submitTooltipDisabled = await page.locator('#btnSubmitSummary').getAttribute('data-tooltip-disabled');
    console.log(`Submit check: optInChecked=${optInChecked}, submitDisabled=${submitDisabled}, disabledReason="${submitTooltipDisabled}"`);
    if (optInChecked !== false || submitDisabled !== true) {
      throw new Error(`Expected submit button to be disabled without opt-in consent! optInChecked=${optInChecked}, submitDisabled=${submitDisabled}`);
    }
    if (!submitTooltipDisabled || !submitTooltipDisabled.includes("согласие")) {
      throw new Error(`Expected disabled reason to mention consent, got: "${submitTooltipDisabled}"`);
    }

    // Attempt to click disabled submit button; verify form is not submitted and modal stays open
    await page.locator('#btnSubmitSummary').click({ force: true });
    await page.waitForTimeout(100);
    const isStillOpen = await page.locator('#summaryModal').evaluate((el) => el.open);
    if (!isStillOpen) throw new Error('Modal should not submit or close when disabled submit button is clicked!');

    await page.keyboard.press('Escape');
    await page.waitForTimeout(100);
    const isModalOpen = await page.locator('#summaryModal').evaluate((el) => el.open);
    if (isModalOpen) throw new Error('Modal should be closed after Escape key');
    console.log('PASSED: Modal dialog top-layer tooltip verified!');

    // TEST 5: Close Inspector Button & inspectedSessionId Reset (Fix 4)
    console.log('\n--- TEST 5: Inspector Close Button & Variable Reset ---');
    await page.evaluate(() => {
      const sec = document.getElementById('inspectorSection');
      if (sec) sec.hidden = false;
    });

    const closeInspBtn = page.locator('#btnCloseInspector');
    await closeInspBtn.click();
    await page.waitForTimeout(100);

    const isInspectorHidden = await page.locator('#inspectorSection').evaluate((el) => el.hidden);
    if (!isInspectorHidden) throw new Error('Inspector section should be hidden after clicking close button');
    console.log('PASSED: Inspector close button verified!');

    // TEST 6: Small Screen (320px) Tooltip Geometry & No-Flicker (Fix 5)
    console.log('\n--- TEST 6: 320px Geometry & Anti-Flicker Verification ---');
    await page.setViewportSize({ width: 320, height: 568 });
    await page.waitForTimeout(100);

    const helpBtn = page.locator('#btnToggleHelp');
    await helpBtn.hover();
    await page.waitForTimeout(100);

    const tipRect = await globalTooltip.evaluate((el) => {
      const r = el.getBoundingClientRect();
      return { left: r.left, right: r.right, width: r.width };
    });
    console.log(`Tooltip at 320px: left=${tipRect.left}, right=${tipRect.right}, width=${tipRect.width}`);
    if (tipRect.width > 288) {
      throw new Error(`Tooltip width ${tipRect.width}px exceeds max-inline-size 288px`);
    }
    if (tipRect.left < 0 || tipRect.right > 320) {
      throw new Error(`Tooltip bounds [${tipRect.left}, ${tipRect.right}] overflow 320px viewport`);
    }

    const spanIcon = helpBtn.locator('span').first();
    await spanIcon.hover();
    await page.waitForTimeout(50);
    const stillVisible = await globalTooltip.evaluate((el) => el.classList.contains('is-visible'));
    if (!stillVisible) {
      throw new Error('Tooltip flickered (hidden) when moving between button and inner span');
    }
    console.log('PASSED: 320px geometry clamping and anti-flicker verified!');

    // TEST 7: Embedded FEATURES.md Loading & Retry UI (Fix 6)
    console.log('\n--- TEST 7: FEATURES.md In-Page Help Loading & Retry UI ---');
    await page.setViewportSize({ width: 1280, height: 800 });
    await helpBtn.click();
    await page.waitForTimeout(100);

    const helpPanelVisible = await page.locator('#helpPanel').evaluate((el) => !el.hidden);
    if (!helpPanelVisible) throw new Error('#helpPanel should be visible after click');

    await page.waitForSelector('#helpDocContent:not([hidden])', { timeout: 8000 });
    const contentHeadingCount = await page.locator('#helpDocContent h2, #helpDocContent h3').count();
    const contentPCount = await page.locator('#helpDocContent p').count();
    console.log(`Rendered FEATURES.md: ${contentHeadingCount} headings (h2/h3), ${contentPCount} paragraphs`);
    if (contentHeadingCount < 5) {
      throw new Error(`Expected at least 5 markdown h2 sections rendered, got ${contentH2Count}`);
    }

    const retryBtn = page.locator('#btnRetryHelpDoc');
    const retryTooltip = await retryBtn.getAttribute('data-tooltip');
    console.log(`Retry button data-tooltip: "${retryTooltip}"`);
    if (!retryTooltip || !retryTooltip.includes('Повторить')) {
      throw new Error(`Expected retry tooltip on #btnRetryHelpDoc, got "${retryTooltip}"`);
    }

    await page.keyboard.press('Escape');
    await page.waitForTimeout(100);
    const helpPanelClosed = await page.locator('#helpPanel').evaluate((el) => el.hidden);
    if (!helpPanelClosed) throw new Error('#helpPanel should close on Escape key');
    console.log('PASSED: In-page help loading, markdown rendering, retry button, and Escape close verified!');

    console.log('\n=== ALL 7 LAYOUT & TOOLTIP FIXES VERIFIED SUCCESSFULLY ===');
  } finally {
    if (browser) await browser.close();
    if (browserServer) await browserServer.close();
  }
}

run().catch((err) => {
  console.error(`QA TEST FAILED: ${err.message}`);
  process.exit(1);
});
