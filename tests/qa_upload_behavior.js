/**
 * Targeted Behavioral UI Test:
 * 1. Fail-closed: requires ephemeral UI_PORT (!= 8787) and UI_IDENTITY matching /api/identity.
 * 2. Missing/unknown tools strictly disable submit button and block POST; banner has calm role="status".
 * 3. HTTP 500 displays persistent human-friendly uploadError without raw server JSON and without success text.
 * 4. Retry duplicate safety explanation is visible alongside retry button.
 * 5. Behavioral regression: after error, switching state to blocked/offline disables Retry and blocks POST; restoring re-enables Retry.
 * 6. Keyboard navigation: Tab traversal onto Retry with visible focus ring, activated via keyboard Enter.
 * 7. Viewports (1280, 760, 320px): explicitly opened and visible uploadContainer, zero page overflow, screenshot captures.
 * 8. Reduced Motion: verifies computed styles have zero or disabled animation/transition durations.
 */
const { chromium } = require("playwright");
const path = require("path");
const fs = require("fs");

const CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

// Strict fail-closed verification: never default to port 8787 or run without identity
const portStr = process.env.UI_PORT;
if (!portStr) {
  console.error("FAIL-CLOSED: UI_PORT environment variable is required. Refusing to default to any port.");
  process.exit(2);
}
const PORT = parseInt(portStr, 10);
if (isNaN(PORT) || PORT <= 1024 || PORT === 8787) {
  console.error(`FAIL-CLOSED: Invalid or forbidden UI_PORT="${portStr}". Must be an ephemeral non-user port != 8787.`);
  process.exit(2);
}

const EXPECTED_IDENTITY = process.env.UI_IDENTITY;
if (!EXPECTED_IDENTITY) {
  console.error("FAIL-CLOSED: UI_IDENTITY environment variable is required to verify target test server.");
  process.exit(2);
}

const BASE_URL = `http://127.0.0.1:${PORT}`;

async function run() {
  console.log(`=== Launching Google Chrome against ${BASE_URL} (Identity: ${EXPECTED_IDENTITY}) ===`);
  let browserServer = null;
  let browser = null;
  const consoleErrors = [];
  let expectNetworkError = false;

  const qaDir = path.resolve(__dirname, "../work/qa");
  if (!fs.existsSync(qaDir)) {
    fs.mkdirSync(qaDir, { recursive: true });
  }

  try {
    browserServer = await chromium.launchServer({
      executablePath: CHROME_PATH,
      headless: true,
      args: ["--no-sandbox", "--disable-gpu"],
    });

    const browserProc = browserServer.process ? browserServer.process() : null;
    const browserPid = browserProc ? browserProc.pid : null;
    const registryFile = process.env.QA_REGISTRY_FILE;
    if (registryFile && browserPid) {
      try {
        fs.writeFileSync(registryFile, JSON.stringify({
          node_pid: process.pid,
          node_pgid: process.pid,
          browser_pid: browserPid,
          browser_pgid: browserPid,
        }), "utf-8");
      } catch (_) {}
    }

    browser = await chromium.connect({ wsEndpoint: browserServer.wsEndpoint() });
    const context = await browser.newContext({
      viewport: { width: 1280, height: 800 },
    });
    const page = await context.newPage();

    page.on("console", (msg) => {
      console.log(`[PAGE CONSOLE ${msg.type()}]`, msg.text());
      if (msg.type() === "error") {
        const txt = msg.text();
        if (txt.includes("favicon.ico")) return;
        if (expectNetworkError && (txt.includes("ERR_FAILED") || txt.includes("500") || txt.includes("Internal server error"))) {
          return;
        }
        consoleErrors.push(txt);
      }
    });

    page.on("pageerror", (err) => {
      consoleErrors.push(err.message);
    });

    // 0. Fail-closed: verify server identity matches EXPECTED_IDENTITY
    const identResp = await page.request.get(`${BASE_URL}/api/identity`);
    if (!identResp.ok()) {
      throw new Error(`Failed to query /api/identity on port ${PORT}: HTTP ${identResp.status()}`);
    }
    const identData = await identResp.json();
    if (!identData || identData.identity !== EXPECTED_IDENTITY) {
      throw new Error(`FAIL-CLOSED: Server identity mismatch on port ${PORT}. Expected "${EXPECTED_IDENTITY}", got "${identData ? identData.identity : null}". Refusing to run.`);
    }
    console.log(`[PASS] Server identity verified: "${identData.identity}" on port ${PORT}`);

    let postUploadAttempts = 0;
    let mockTools = { ffmpeg: false, ffprobe: false, ready: false };
    let uploadResponseStatus = 500;
    let abortStatusRequests = false;

    // Intercept and control /api/status tools reporting
    await page.route("**/api/status", async (route) => {
      if (abortStatusRequests) {
        await route.abort();
        return;
      }
      const request = route.request();
      if (request.method() === "GET") {
        const response = await route.fetch();
        const json = await response.json();
        json.tools = mockTools;
        await route.fulfill({
          status: response.status(),
          headers: {
            ...response.headers(),
            "content-type": "application/json; charset=utf-8",
          },
          body: JSON.stringify(json),
        });
      } else {
        await route.continue();
      }
    });

    // Track and intercept /api/upload
    await page.route("**/api/upload*", async (route) => {
      console.log(`[QA ROUTE /api/upload] URL: ${route.request().url()}, status will be: ${uploadResponseStatus}`);
      postUploadAttempts++;
      if (uploadResponseStatus >= 400) {
        await route.fulfill({
          status: uploadResponseStatus,
          contentType: "application/json; charset=utf-8",
          body: JSON.stringify({ ok: false, error: "Internal server error 500" }),
        });
      } else {
        await route.fulfill({
          status: 200,
          contentType: "application/json; charset=utf-8",
          body: JSON.stringify({ ok: true, session_id: "mock_upload_sess" }),
        });
      }
    });

    // 1. Initial navigation with missing tools (ffmpeg: false, ffprobe: false)
    await page.goto(BASE_URL);
    await page.waitForSelector("#sessionsTableBody:not([aria-busy=\"true\"])", { timeout: 10000 });

    // Switch to upload source
    await page.selectOption("#sourceSelect", "upload");

    // Verify dependency warning is visible and has calm role="status" (SLOP-014)
    const warnRole = await page.$eval("#uploadDependencyWarning", (el) => el.getAttribute("role"));
    if (warnRole !== "status") {
      throw new Error(`Expected #uploadDependencyWarning to have role="status", got "${warnRole}"`);
    }

    const isWarnHidden = await page.$eval("#uploadDependencyWarning", (el) => el.hidden);
    if (isWarnHidden) {
      throw new Error("Expected #uploadDependencyWarning to be visible when tools are missing");
    }

    // Select a file
    await page.setInputFiles("#audioFileInput", {
      name: "test_video.mp4",
      mimeType: "video/mp4",
      buffer: Buffer.from("fake mp4 video content"),
    });

    // Verify submit button is disabled
    const isSubmitDisabled = await page.$eval("#btnUploadSubmit", (el) => el.disabled);
    if (!isSubmitDisabled) {
      throw new Error("Expected #btnUploadSubmit to be disabled when tools are missing");
    }

    // Attempt to invoke upload directly
    postUploadAttempts = 0;
    await page.evaluate(() => {
      const btn = document.getElementById("btnUploadSubmit");
      btn.click();
    });

    if (postUploadAttempts > 0) {
      throw new Error(`Expected missing tools to block POST, but ${postUploadAttempts} POST request(s) were sent`);
    }
    console.log("[PASSED] Missing tools strictly blocked POST");

    // 2. HTTP 500 error display, human-friendly message, and retry note
    mockTools = { ffmpeg: true, ffprobe: true, ready: true };
    await page.waitForTimeout(2200);

    const isWarnHiddenNow = await page.$eval("#uploadDependencyWarning", (el) => el.hidden);
    if (!isWarnHiddenNow) {
      throw new Error("Expected #uploadDependencyWarning to be hidden when tools are available");
    }

    const debugState = await page.evaluate(() => {
      const btn = document.getElementById("btnUploadSubmit");
      const file = document.getElementById("audioFileInput");
      const sync = document.getElementById("lastSyncTime");
      const badge = document.getElementById("systemStatusBadge");
      const tbody = document.getElementById("sessionsTableBody");
      return {
        btnDisabled: btn ? btn.disabled : null,
        btnTitle: btn ? btn.title : null,
        hasFiles: file && file.files ? file.files.length : null,
        syncTimeText: sync ? sync.textContent : null,
        syncHidden: sync ? sync.hidden : null,
        badgeState: badge ? badge.getAttribute("data-state") : null,
        tbodyAriaBusy: tbody ? tbody.getAttribute("aria-busy") : null,
      };
    });
    console.log("DEBUG STATE:", JSON.stringify(debugState));

    const isSubmitEnabled = await page.$eval("#btnUploadSubmit", (el) => !el.disabled);
    if (!isSubmitEnabled) {
      throw new Error(`Expected #btnUploadSubmit to be enabled when tools are available and file is selected, got: ${JSON.stringify(debugState)}`);
    }

    uploadResponseStatus = 500;
    expectNetworkError = true;
    await page.click("#btnUploadSubmit");

    // Wait for uploadError banner
    await page.waitForSelector("#uploadError:not([hidden])", { timeout: 5000 });

    // Check error text is human-friendly and does NOT show raw server error string (SLOP-011)
    const errorText = await page.$eval("#uploadError", (el) => el.textContent);
    if (errorText.includes("Internal server error 500") || errorText.includes('{"ok"')) {
      throw new Error(`Expected human-friendly text in #uploadError, but got raw server string: "${errorText}"`);
    }
    if (!errorText.includes("Внутренняя ошибка сервера") && !errorText.includes("Не удалось импортировать")) {
      throw new Error(`Expected friendly error description, got: "${errorText}"`);
    }

    // Verify progress text does NOT contain success text
    const progressText = await page.$eval("#uploadProgressText", (el) => el.textContent);
    if (progressText.includes("успешно") || progressText.includes("Сессия готова")) {
      throw new Error(`Expected no success text on HTTP 500 failure, got: "${progressText}"`);
    }

    // Verify retry container and explanation note are visible
    const isRetryContainerVisible = await page.$eval("#uploadRetryContainer", (el) => !el.hidden);
    if (!isRetryContainerVisible) {
      throw new Error("Expected #uploadRetryContainer to be visible on error");
    }
    const retryNoteText = await page.$eval("#uploadRetryNote", (el) => el.textContent);
    if (!retryNoteText.includes("Перед повтором проверьте") || !retryNoteText.includes("копию")) {
      throw new Error(`Expected honest duplicate note in #uploadRetryNote, got: "${retryNoteText}"`);
    }

    // Verify error banner is persistent (does not auto-hide after 1.5s)
    await page.waitForTimeout(1500);
    const isErrorStillVisible = await page.$eval("#uploadError", (el) => !el.hidden);
    if (!isErrorStillVisible) {
      throw new Error("#uploadError was prematurely hidden; must remain persistently visible");
    }
    expectNetworkError = false;
    console.log("[PASSED] HTTP 500 showed persistent uploadError without success text");

    // 3. Explicit Viewport Verification (1280px, 760px, 320px) during Error & Preconditions state
    for (const vp of [{ width: 1280, height: 800 }, { width: 760, height: 600 }, { width: 320, height: 568 }]) {
      await page.setViewportSize(vp);

      const stateInViewport = await page.evaluate(() => {
        const c = document.getElementById("uploadContainer");
        const err = document.getElementById("uploadError");
        const retryCont = document.getElementById("uploadRetryContainer");
        const doc = document.documentElement;
        return {
          containerVisible: c && !c.hidden && c.offsetHeight > 0 && c.offsetWidth > 0,
          errorVisible: err && !err.hidden && err.offsetHeight > 0,
          retryVisible: retryCont && !retryCont.hidden && retryCont.offsetHeight > 0,
          docScrollWidth: doc.scrollWidth,
          innerWidth: window.innerWidth,
          hasPageOverflow: doc.scrollWidth > window.innerWidth,
        };
      });

      if (!stateInViewport.containerVisible) {
        throw new Error(`Upload container was not visible at viewport width ${vp.width}px`);
      }
      if (!stateInViewport.errorVisible) {
        throw new Error(`Upload error banner was not visible at viewport width ${vp.width}px`);
      }
      if (!stateInViewport.retryVisible) {
        throw new Error(`Upload retry container was not visible at viewport width ${vp.width}px`);
      }
      if (stateInViewport.hasPageOverflow) {
        throw new Error(`Horizontal page overflow detected at viewport ${vp.width}px: scrollWidth ${stateInViewport.docScrollWidth} > innerWidth ${vp.width}`);
      }

      // Scroll to error banner so it is clearly framed, and capture fullPage screenshot
      await page.evaluate(() => {
        const el = document.getElementById("uploadError");
        if (el) el.scrollIntoView({ behavior: "instant", block: "center" });
      });

      // Capture screenshot for audit evidence showing error banner and retry note
      const screenshotPath = path.join(qaDir, `upload_viewport_${vp.width}.png`);
      await page.screenshot({ path: screenshotPath, fullPage: true });
      if (!fs.existsSync(screenshotPath) || fs.statSync(screenshotPath).size === 0) {
        throw new Error(`Failed to capture valid screenshot at ${screenshotPath}`);
      }
      const evidenceDest = path.resolve(__dirname, "../../outputs/antigravity/refactor-16", `upload_viewport_${vp.width}.png`);
      try {
        fs.mkdirSync(path.dirname(evidenceDest), { recursive: true });
        fs.copyFileSync(screenshotPath, evidenceDest);
      } catch (_) {}
      console.log(`[PASSED] Viewport ${vp.width}px: error banner & retry visible, no page overflow (screenshot: ${path.basename(screenshotPath)})`);
    }

    // Reset viewport back to desktop for subsequent interactions
    await page.setViewportSize({ width: 1280, height: 800 });

    // 4. Behavioral Regression: Server drop / offline state strictly disables Retry and prevents POST
    const isRetryEnabledInit = await page.$eval("#btnUploadRetry", (el) => !el.disabled);
    if (!isRetryEnabledInit) {
      throw new Error("Expected #btnUploadRetry to be enabled initially after error");
    }

    // Abort status requests to simulate real server drop / network loss
    expectNetworkError = true;
    abortStatusRequests = true;
    // Wait for watchdog or pollStatus error handler to mark page as offline
    await page.waitForSelector("body.is-offline", { timeout: 8000 });

    const isRetryDisabledOffline = await page.$eval("#btnUploadRetry", (el) => el.disabled);
    if (!isRetryDisabledOffline) {
      throw new Error("Expected #btnUploadRetry to be disabled in offline state");
    }

    // Verify click while offline produces 0 POST attempts
    postUploadAttempts = 0;
    await page.evaluate(() => {
      const btn = document.getElementById("btnUploadRetry");
      btn.click();
    });
    if (postUploadAttempts > 0) {
      throw new Error(`Expected offline Retry to strictly block POST, but ${postUploadAttempts} request(s) sent`);
    }

    // Restore status requests
    abortStatusRequests = false;
    await page.waitForSelector("body:not(.is-offline)", { timeout: 8000 });
    expectNetworkError = false;

    const isRetryReenabled = await page.$eval("#btnUploadRetry", (el) => !el.disabled);
    if (!isRetryReenabled) {
      throw new Error("Expected #btnUploadRetry to be re-enabled after restoring online connection");
    }
    console.log("[PASSED] Blocked/offline state strictly disabled Retry and prevented POST");

    // 5. Keyboard Navigation & Visible Focus traversal
    // Focus file input and Tab to Retry button
    await page.focus("#audioFileInput");
    await page.keyboard.press("Tab"); // onto btnUploadSubmit
    await page.keyboard.press("Tab"); // onto btnUploadRetry

    const focusedId = await page.evaluate(() => document.activeElement ? document.activeElement.id : null);
    if (focusedId !== "btnUploadRetry") {
      throw new Error(`Expected keyboard focus on #btnUploadRetry, got "${focusedId}"`);
    }

    // Verify visible focus styling on #btnUploadRetry
    const hasVisibleFocus = await page.evaluate(() => {
      const btn = document.getElementById("btnUploadRetry");
      const cs = window.getComputedStyle(btn);
      const hasOutline = cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0;
      const hasBoxShadow = cs.boxShadow !== "none" && cs.boxShadow !== "";
      return hasOutline || hasBoxShadow;
    });
    if (!hasVisibleFocus) {
      throw new Error("Expected visible focus outline or box-shadow on #btnUploadRetry when focused via keyboard");
    }

    // Trigger retry via keyboard Enter
    uploadResponseStatus = 200;
    await page.keyboard.press("Enter");
    await page.waitForTimeout(600);

    const isErrorHiddenAfterRetry = await page.$eval("#uploadError", (el) => el.hidden);
    const isRetryHiddenAfterRetry = await page.$eval("#uploadRetryContainer", (el) => el.hidden);
    if (!isErrorHiddenAfterRetry || !isRetryHiddenAfterRetry) {
      throw new Error("Expected #uploadError and #uploadRetryContainer to hide after successful retry");
    }
    console.log("[PASSED] Keyboard navigation and visible focus triggered successful retry");

    // 6. Reduced Motion Computed CSS Verification with strict assertion
    await page.emulateMedia({ reducedMotion: "reduce" });
    const motionAudit = await page.evaluate(() => {
      const matchesMedia = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      // Inspect computed styles of interactive/animated elements
      const elementsToCheck = [
        document.getElementById("btnUploadSubmit"),
        document.getElementById("uploadDependencyWarning"),
        document.getElementById("sourceSelect"),
      ].filter(Boolean);

      const computedResults = elementsToCheck.map((el) => {
        const cs = window.getComputedStyle(el);
        const transDur = cs.transitionDuration;
        const animDur = cs.animationDuration;
        const animName = cs.animationName;
        return {
          id: el.id,
          transDur,
          animDur,
          animName,
        };
      });

      return { matchesMedia, computedResults };
    });

    if (!motionAudit.matchesMedia) {
      throw new Error("Expected prefers-reduced-motion: reduce to be active");
    }

    for (const res of motionAudit.computedResults) {
      const tdSec = parseFloat(res.transDur) || 0;
      const adSec = parseFloat(res.animDur) || 0;
      if (tdSec > 0.01) {
        throw new Error(`Reduced motion failure on #${res.id}: transition-duration (${res.transDur}) > 0.01s`);
      }
      if (adSec > 0.01 && res.animName !== "none") {
        throw new Error(`Reduced motion failure on #${res.id}: animation-duration (${res.animDur}) > 0.01s with animation "${res.animName}"`);
      }
    }
    console.log(`[PASSED] Reduced motion strictly verified on computed elements: ${JSON.stringify(motionAudit.computedResults)}`);

    if (consoleErrors.length > 0) {
      throw new Error(`Unexpected browser console errors encountered (${consoleErrors.length}):\n${consoleErrors.join("\n")}`);
    }

    console.log("=== All upload UI behavioral checks passed! ===");
  } finally {
    if (browser) {
      try { await browser.close(); } catch (_) {}
    }
    if (browserServer) {
      try { await browserServer.close(); } catch (_) {}
    }
  }
}

run().catch((err) => {
  console.error("[FAILED]:", err);
  process.exit(1);
});
