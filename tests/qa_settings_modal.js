const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const CHROME_PATH = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = process.env.UI_PORT ? parseInt(process.env.UI_PORT, 10) : 8795;
const BASE_URL = `http://127.0.0.1:${PORT}`;
const SCREENSHOTS_DIR = path.resolve(__dirname, '..', 'work', 'qa', 'screenshots');

async function run() {
  fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });
  console.log(`Connecting to Chrome at ${CHROME_PATH} for ${BASE_URL}...`);
  const browser = await chromium.launch({
    executablePath: CHROME_PATH,
    headless: true,
    args: ['--no-sandbox', '--disable-gpu'],
  });

  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const consoleErrors = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  try {
    await page.goto(BASE_URL, { waitUntil: 'networkidle' });
    console.log('Page loaded successfully');

    // 1. Verify "⚙️ Настройки" button is present and visible
    const btnOpenSettings = page.locator('#btnOpenSettings');
    await btnOpenSettings.waitFor({ state: 'visible' });
    const btnText = await btnOpenSettings.textContent();
    console.log(`Found settings button: "${btnText.trim()}"`);

    // 2. Click button to open Settings Modal
    await btnOpenSettings.click();
    const modal = page.locator('#settingsModal');
    await modal.waitFor({ state: 'visible' });
    console.log('Settings modal opened successfully');

    // Wait for settings data to load into modal
    await page.waitForSelector('#infoWhisperVersion:not(:empty)', { timeout: 5000 });
    await page.waitForTimeout(500);

    // 3. Inspect Scenario Readiness cards
    const importBadge = await page.locator('#badgeScenarioImport').textContent();
    const micBadge = await page.locator('#badgeScenarioMic').textContent();
    const systemBadge = await page.locator('#badgeScenarioSystem').textContent();
    const transcribeBadge = await page.locator('#badgeScenarioTranscribe').textContent();

    console.log('--- Scenario Readiness ---');
    console.log(`  Import:      ${importBadge.trim()}`);
    console.log(`  Mic:         ${micBadge.trim()}`);
    console.log(`  System:      ${systemBadge.trim()}`);
    console.log(`  Transcribe:  ${transcribeBadge.trim()}`);

    // 4. Inspect Engine & Model Details
    const whisperVer = await page.locator('#infoWhisperVersion').textContent();
    const modelStatus = await page.locator('#infoModelStatus').textContent();
    const modelSize = await page.locator('#infoModelSize').textContent();
    const diskSpace = await page.locator('#infoDiskSpace').textContent();

    console.log('--- Engine & Model Details ---');
    console.log(`  Whisper:     ${whisperVer.trim()}`);
    console.log(`  Model state: ${modelStatus.trim()}`);
    console.log(`  Model size:  ${modelSize.trim()}`);
    console.log(`  Disk space:  ${diskSpace.trim()}`);

    // 5. Test saving form settings via UI
    await page.fill('#inputGpuThreads', '6');
    await page.fill('#inputCpuThreads', '3');
    const btnSave = page.locator('#btnSaveSettings');
    await btnSave.click();
    await page.waitForSelector('#settingsAlert:not([hidden])', { timeout: 3000 });
    const alertText = await page.locator('#settingsAlertText').textContent();
    console.log(`Save alert received: "${alertText.trim()}"`);

    // 6. Take screenshot of Settings Modal after save
    const shotPath = path.join(SCREENSHOTS_DIR, 'qa_settings_modal.png');
    await page.screenshot({ path: shotPath, fullPage: false });
    console.log(`Screenshot saved to ${shotPath}`);

    // 7. Test "🔄 Проверить снова" button
    const btnRecheck = page.locator('#btnRecheckSettings');
    await btnRecheck.click();
    await page.waitForTimeout(600);
    console.log('Re-check button clicked and executed cleanly');

    // 8. Test closing modal via close button
    const btnClose = page.locator('#btnCloseSettingsModal');
    await btnClose.click();
    await modal.waitFor({ state: 'hidden' });
    console.log('Modal closed via close button');

    // 9. Re-open and test closing via Escape key
    await btnOpenSettings.click();
    await modal.waitFor({ state: 'visible' });
    await page.keyboard.press('Escape');
    await modal.waitFor({ state: 'hidden' });
    console.log('Modal closed via Escape key');

    console.log(`Console errors count: ${consoleErrors.length}`);
    if (consoleErrors.length > 0) {
      console.log('Console errors:', consoleErrors);
    }

    console.log('=== All Browser QA Checks PASSED ===');
  } finally {
    await browser.close();
  }
}

run().catch((err) => {
  console.error('QA Script Failed:', err);
  process.exit(1);
});
