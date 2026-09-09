"""Unit tests for summary isolation, opt-in verification, and process termination."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from recorder.constants import (
    SUMMARY_PROVIDER_CLAUDE,
    SUMMARY_PROVIDER_CODEX,
    SUMMARY_PROVIDER_NONE,
)
from recorder.session import create_session
from recorder.storage import atomic_write_text, get_session_dir
from recorder.summary import generate_summary, run_claude_summary
from tests.isolated_test import IsolatedTestCase


class TestSummaryIsolation(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.session_id = "test_summary_session"
        self.manifest = create_session(self.session_id, title="Test Summary")
        self.sess_dir = get_session_dir(self.session_id)
        atomic_write_text(self.sess_dir / "transcript.txt", "Это тестовая расшифровка лекции о надежности систем.")

    def test_summary_none_preserves_privacy(self) -> None:
        res = generate_summary(self.session_id, provider=SUMMARY_PROVIDER_NONE, user_opt_in=False)
        self.assertIsNone(res)
        self.assertFalse((self.sess_dir / "summary.md").exists())

    def test_strict_opt_in_boolean_identity(self) -> None:
        invalid_opt_ins = [False, None, "true", "True", 1, 0, []]
        for opt in invalid_opt_ins:
            with self.assertRaises(ValueError):
                generate_summary(self.session_id, provider=SUMMARY_PROVIDER_CLAUDE, user_opt_in=opt)

    def test_codex_summary_is_disabled(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            generate_summary(self.session_id, provider=SUMMARY_PROVIDER_CODEX, user_opt_in=True)
        self.assertIn("Codex CLI summary is disabled", str(ctx.exception))

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""})
    def test_claude_bare_requires_api_key(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            with self.assertRaises(RuntimeError) as ctx:
                run_claude_summary("Test prompt")
            self.assertIn("requires ANTHROPIC_API_KEY", str(ctx.exception))

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-testkey"})
    @patch("recorder.summary.run_guarded_command")
    @patch("shutil.which", return_value="/usr/local/bin/claude")
    def test_claude_command_flags_and_isolated_cwd(self, mock_which, mock_guarded) -> None:
        mock_guarded.return_value = (0, "# Конспект\nУспешно", "")

        result = run_claude_summary("Prompt text")
        self.assertEqual(result, "# Конспект\nУспешно")

        called_cmd = mock_guarded.call_args[0][0]
        self.assertIn("--bare", called_cmd)
        self.assertIn("--setting-sources", called_cmd)
        self.assertIn("--tools", called_cmd)
        self.assertIn("", called_cmd)
        self.assertIn("--strict-mcp-config", called_cmd)
        self.assertIn("--no-session-persistence", called_cmd)

        called_cwd = str(mock_guarded.call_args[1]["cwd"])
        self.assertTrue("tmp" in called_cwd or "var" in called_cwd)
        self.assertNotEqual(called_cwd, str(Path.cwd()))


if __name__ == "__main__":
    unittest.main()
