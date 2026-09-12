import os
import sys
import time
import json
import subprocess
import unittest
import re
from pathlib import Path

from tests.isolated_test import IsolatedTestCase
from tests.test_browser_e2e import SERVER_SCRIPT
from playwright.sync_api import sync_playwright

class TestOnboardingUX(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=True)
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 720})
        self.page = self.context.new_page()

        server_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
            "UI_PORT": "0",
            "WEBINAR_OUT_DIR": str(self.data_root),
        }
        self.server_proc = subprocess.Popen(
            [sys.executable, str(SERVER_SCRIPT), "0"],
            env=server_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        self.add_process_cleanup(self.server_proc)

        info_file = Path(self.data_root) / ".fake_server_info.json"
        deadline = time.time() + 8.0
        server_info = {}
        while time.time() < deadline:
            if info_file.exists():
                try:
                    server_info = json.loads(info_file.read_text(encoding="utf-8"))
                    if server_info.get("port"):
                        break
                except Exception:
                    pass
            time.sleep(0.1)

        self.server_port = server_info.get("port")
        self.server_url = f"http://127.0.0.1:{self.server_port}/"

        self.context.add_cookies([{
            "name": "test_auth",
            "value": "1",
            "url": self.server_url
        }])

    def tearDown(self):
        self.page.close()
        self.context.close()
        self.browser.close()
        self.playwright.stop()
        super().tearDown()

    def test_onboarding_checklist_persistence(self):
        self.page.goto(self.server_url)
        self.page.wait_for_load_state("networkidle")
        self.page.wait_for_timeout(500)
        checklist = self.page.locator("#onboardingChecklist")
        self.assertTrue(checklist.is_visible())
        self.page.click("#btnDismissOnboarding")
        self.assertFalse(checklist.is_visible())

        # Reload and check persistence
        self.page.reload()
        self.page.wait_for_load_state("networkidle")
        self.page.wait_for_timeout(500)
        self.assertFalse(checklist.is_visible())

        # Toggle restores it
        self.page.click("#btnToggleOnboarding")
        self.assertTrue(checklist.is_visible())
        self.page.reload()
        self.page.wait_for_load_state("networkidle")
        self.page.wait_for_timeout(500)
        self.assertTrue(checklist.is_visible())

    def test_header_upload_button(self):
        self.page.goto(self.server_url)
        self.page.wait_for_load_state("networkidle")
        self.page.wait_for_timeout(500)
        self.page.click("#btnHeaderUpload")

        # Verify mode switched to tabUpload
        # Verify source select changed to upload
        upload_container = self.page.locator("#uploadContainer")
        self.assertTrue(upload_container.is_visible())

    def test_bug_report_modal(self):
        self.page.goto(self.server_url)
        self.page.wait_for_load_state("networkidle")
        self.page.wait_for_timeout(500)
        self.page.click("#btnBugReportOpen")
        modal = self.page.locator("#bugReportModal")
        self.assertTrue(modal.is_visible())

        # Verify GitHub href
        href = self.page.locator("#btnBugReportOpenGitHub").get_attribute("href")
        self.assertEqual(href, "https://github.com/Big888Boss/rech-v-tekst/issues/new?template=bug_report.yml")

        # Verify template fields
        content = self.page.locator("#bugReportTemplateContent").input_value()
        self.assertIn("Версия macOS", content)
        self.assertIn("Конфиденциальность", content)

        # Verify Escape restores focus
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(500)
        self.assertFalse(modal.is_visible())

        # Playwright focuses the element
        is_focused = self.page.evaluate("document.activeElement.id === 'btnBugReportOpen'")
        self.assertTrue(is_focused)

    def test_safe_markdown_and_docs_traversal(self):
        self.page.goto(self.server_url)
        self.page.wait_for_load_state("networkidle")
        self.page.wait_for_timeout(500)

        # Traversal check using raw http.client to avoid client-side normalization
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.server_port)
        conn.request("GET", "/docs/../config.py")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 403)
        conn.close()
        # Markdown escaping check - unit level helper via page.evaluate
        malicious_markdown = "<h1>Hello</h1> [evil](javascript:alert(1)) [good](https://example.com) <script>alert(2)</script>"
        rendered = self.page.evaluate('''() => {
            const div = document.createElement("div");
            window.renderMarkdownSafely("<h1>Hello</h1> [evil](javascript:alert(1)) [good](https://example.com) <script>alert(2)</script>", div);
            return div.innerHTML;
        }''')
        self.assertNotIn("<h1>", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;h1&gt;", rendered)
        self.assertIn('href="https://example.com"', rendered)
        self.assertNotIn("href=\"javascript:", rendered)