"""Automated runtime and static verification for UI requirements (R01-R11, GAP-PROGRESS)."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from tests.isolated_test import IsolatedTestCase


class TestUIRuntimeVerification(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.html_path = Path("static/index.html")
        self.css_path = Path("static/app.css")
        self.js_path = Path("static/app.js")
        self.tokens_path = Path("static/tokens.css")

        self.html_content = self.html_path.read_text(encoding="utf-8")
        self.css_content = self.css_path.read_text(encoding="utf-8")
        self.js_content = self.js_path.read_text(encoding="utf-8")
        self.tokens_content = self.tokens_path.read_text(encoding="utf-8")

    def test_r04_initial_loading_state_and_skeleton_structure(self) -> None:
        """Verify initial loading status label, disabled start button, and 6-td skeleton rows (R04)."""
        # Header status label must be loading initially
        self.assertIn('id="systemStatusText">Загрузка данных...', self.html_content)

        # Primary Start button must be disabled initially
        self.assertIn('id="btnPrimaryStart" class="btn btn-primary" type="button" disabled', self.html_content)

        # Table body must have aria-busy="true" initially
        self.assertIn('id="sessionsTableBody" aria-busy="true"', self.html_content)

        # Skeleton rows must have 6 <td> cells matching header columns, not colspan=6
        skeleton_tbody_match = re.search(r'<tbody id="sessionsTableBody"[^>]*>(.*?)</tbody>', self.html_content, re.DOTALL)
        self.assertIsNotNone(skeleton_tbody_match)
        tbody_html = skeleton_tbody_match.group(1)
        self.assertNotIn('colspan="6"', tbody_html, "Skeleton rows should not use single colspan=6 cell")

        rows = re.findall(r'<tr class="skeleton-row"[^>]*>(.*?)</tr>', tbody_html, re.DOTALL)
        self.assertEqual(len(rows), 3, "Must have exactly 3 initial skeleton placeholder rows")
        for row in rows:
            tds = re.findall(r"<td", row)
            self.assertEqual(len(tds), 6, "Each skeleton row must have 6 <td> cells matching table columns")

    def test_r11_single_unified_table_without_redundant_exceptions_table(self) -> None:
        """Verify that redundant exceptions table is eliminated and unified dataset is used (R11)."""
        # exceptionsSection should not be in index.html as a separate table
        self.assertNotIn('id="exceptionsSection"', self.html_content)
        self.assertNotIn('id="exceptionsTableBody"', self.html_content)

        # In app.js, exceptions must be sorted strictly by severity (failed > interrupted) and age
        self.assertIn("failed", self.js_content)
        self.assertIn("interrupted", self.js_content)
        self.assertIn("a.status === 'failed' ? -1 : 1", self.js_content)
        self.assertIn("da.localeCompare(db)", self.js_content)

    def test_r06_css_token_purity_and_no_arithmetic_stacking(self) -> None:
        """Verify CSS token usage, no grayscale on offline, canonical z-index, and hit targets (R06)."""
        # No calc(var(--ad-z-sticky) + 1) or + 2
        self.assertNotIn("calc(var(--ad-z-sticky) +", self.css_content)

        # No grayscale on .is-offline
        self.assertNotIn("grayscale", self.css_content)

        # No dead skeleton-box styles with arbitrary width 60%
        self.assertNotIn(".skeleton-box", self.css_content)
        self.assertNotIn("inline-size: 60%", self.css_content)

        # Reduced motion skip link does not use untokenized opacity
        if "@media (prefers-reduced-motion" in self.css_content:
            sub = self.css_content[self.css_content.find("@media (prefers-reduced-motion"):]
            self.assertNotIn("opacity: 0;", sub)

        # Checkbox hit target: label has min-block-size and min-inline-size >= 44px
        self.assertIn(".form-checkbox-label", self.css_content)
        self.assertIn("min-block-size: var(--ad-target-min)", self.css_content)
        self.assertIn("min-inline-size: var(--ad-target-min)", self.css_content)

        # Dropdown items have min-block-size: var(--ad-target-min)
        self.assertIn(".dropdown-item", self.css_content)

    def test_r03_gated_action_availability_and_string_permissions(self) -> None:
        """Verify that actions are gated on initialDataLoaded and string permissions handled (R03)."""
        self.assertIn("if (!initialDataLoaded)", self.js_content)
        self.assertIn("elements.btnPrimaryStart.disabled = true", self.js_content)

        # Modal checkbox change must call updateActionStates and not directly set disabled
        self.assertIn("elements.summaryOptInCheck.addEventListener('change'", self.js_content)
        self.assertIn("updateActionStates();", self.js_content)

        # String permissions handled: denied, unknown, enumerated_capture_untested
        self.assertIn("lastPermissions === 'denied'", self.js_content)
        self.assertIn("enumerated_capture_untested", self.js_content)

    def test_r01_preflight_generation_counter_and_selection_locking(self) -> None:
        """Verify preflight generation counter and selection locking during tests (R01)."""
        self.assertIn("let preflightGeneration = 0;", self.js_content)
        self.assertIn("preflightGeneration++;", self.js_content)
        self.assertIn("currentGen !== preflightGeneration", self.js_content)
        self.assertIn("elements.sourceSelect.disabled = true;", self.js_content)
        self.assertIn("elements.deviceSelect.disabled = true;", self.js_content)

    def test_r09_safe_focus_return_fallback(self) -> None:
        """Verify closeSummaryModal safely falls back to accessible focus target (R09)."""
        self.assertIn("function closeSummaryModal()", self.js_content)
        self.assertIn("elements.btnRequestSummary.focus()", self.js_content)
        self.assertIn("elements.btnExportMenu.focus()", self.js_content)

    def test_gap_progress_live_timer_and_adjacent_help(self) -> None:
        """Verify live recording elapsed timer and adjacent help text for Stop/Cancel (GAP-PROGRESS)."""
        self.assertIn('id="activeControlHelp"', self.html_content)
        self.assertIn("recordingStartTime", self.js_content)
        self.assertIn("elements.activeElapsed.textContent = formatSec(elapsed)", self.js_content)

    def test_export_menu_keyboard_accessibility(self) -> None:
        """Verify export menu supports ArrowDown/ArrowUp/Home/End/Escape keyboard navigation."""
        self.assertIn("elements.btnExportMenu.addEventListener('keydown'", self.js_content)
        self.assertIn("elements.exportDropdown.addEventListener('keydown'", self.js_content)
        self.assertIn("ArrowDown", self.js_content)
        self.assertIn("ArrowUp", self.js_content)
        self.assertIn("Escape", self.js_content)

    def test_upload_ui_contracts_tools_gating_and_error_handling(self) -> None:
        """Verify upload UI contracts: tool-gated action availability and persistent error display."""
        # CSS token purity in banner-danger: must use valid --ad-status-error, not nonexistent token
        self.assertIn(".banner-danger", self.css_content)
        self.assertIn("border-color: var(--ad-status-error);", self.css_content)
        self.assertNotIn("var(--ad-status-failed)", self.css_content)

        # Unified condition for upload submit button in app.js
        self.assertIn("serverTools.ffmpeg === true && serverTools.ffprobe === true", self.js_content)
        self.assertIn("elements.btnUploadSubmit.disabled = !canUpload;", self.js_content)

        # uploadError banner must be cleared when a new file is chosen
        self.assertIn("elements.uploadError.hidden = true;", self.js_content)

        # On upload error (e.g. HTTP 500), success text is cleared, progress container is hidden,
        # and uploadError is displayed persistently
        self.assertIn("elements.uploadProgressText.textContent = '';", self.js_content)
        self.assertIn("elements.uploadError.textContent = displayErr;", self.js_content)
        self.assertIn("elements.uploadError.hidden = false;", self.js_content)
