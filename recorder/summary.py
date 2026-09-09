"""Summary generation with strict Claude bare isolation, API key check, and bounded process reaping."""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .capture import is_group_alive, stop_and_reap_process_group
from .constants import (
    SUMMARY_PROVIDER_AUTO,
    SUMMARY_PROVIDER_CLAUDE,
    SUMMARY_PROVIDER_CODEX,
    SUMMARY_PROVIDER_NONE,
    TEMPLATE_ACTION_ITEMS,
    TEMPLATE_INTERVIEW,
    TEMPLATE_LECTURE,
    TEMPLATE_MEETING,
)
from .guardian import run_guarded_command
from .lock import GLOBAL_LOCK
from .session import SessionManifest, load_session, save_session
from .storage import (
    OUT_DIR,
    StorageError,
    atomic_write_text,
    get_session_dir,
    is_safe_regular_file,
    safe_read_text,
)

TEMPLATES: dict[str, str] = {
    TEMPLATE_MEETING: """Ты профессиональный секретарь и редактор. Ниже — автоматическая расшифровка вебинара/встречи.
Сделай структурированный конспект встречи на русском языке в Markdown:

1. **Краткое резюме** (3-5 предложений: цель встречи, ключевой результат).
2. **Основные темы обсуждения** (по разделам с ключевыми тезисами).
3. **Принятые решения**.
4. **Action items** (задачи, ответственные, сроки).
5. **Открытые и нерешённые вопросы**.

Игнорируй речевой шум, оговорки и повторы. Не выдумывай фактов, которых нет в тексте.

=== РАСШИФРОВКА ===
{transcript}
""",
    TEMPLATE_LECTURE: """Ты научный редактор и методист. Ниже — автоматическая расшифровка учебной лекции.
Сделай подробный академический конспект на русском языке в Markdown:

1. **Тема и цель лекции**.
2. **Ключевые понятия и термины** (с чёткими определениями).
3. **Основные тезисы и логика изложения** (разбитые по смысловым блокам).
4. **Примеры, формулы, законы или кейсы**, рассмотренные лектором.
5. **Практические выводы и резюме**.
6. **Контрольные вопросы для самопроверки**.

=== РАСШИФРОВКА ===
{transcript}
""",
    TEMPLATE_INTERVIEW: """Ты журналист и редактор. Ниже — автоматическая расшифровка интервью/подкаста.
Сделай структурированное резюме диалога на русском языке в Markdown:

1. **Гость и тема беседы**.
2. **Главные тезисы и инсайты** (ключевые цитаты и позиции сторон).
3. **Обсуждаемые кейсы, опыт и рекомендации**.
4. **Ключевой вывод интервью**.

=== РАСШИФРОВКА ===
{transcript}
""",
    TEMPLATE_ACTION_ITEMS: """Ты проектный менеджер. Ниже — автоматическая расшифровка рабочей встречи.
Извлеки только конкретные договорённости и действия на русском языке в Markdown:

1. **Таблица Action Items**:
   | Задача | Ответственный | Срок / Контекст |
2. **Зафиксированные решения**.
3. **Блокеры и зависимости**.

=== РАСШИФРОВКА ===
{transcript}
""",
}


def build_summary_prompt(transcript_text: str, template_name: str = TEMPLATE_MEETING) -> str:
    template = TEMPLATES.get(template_name, TEMPLATES[TEMPLATE_MEETING])
    return template.format(transcript=transcript_text)


def run_claude_summary(
    prompt: str,
    timeout_sec: float = 60.0,
    claude_bin: str = "claude",
    cancel_event: threading.Event | None = None,
    on_proc_start: Callable[[subprocess.Popen[str]], None] | None = None,
    session_id: str | None = None,
) -> str:
    """Run isolated Claude CLI subprocess in bare zero-tool mode with temporary CWD.
    Guarantees entire process group is reaped even if leader process exits early (R2 / SEC-05).
    """
    if not shutil.which(claude_bin):
        raise RuntimeError(
            f"Claude CLI executable '{claude_bin}' not found in PATH. "
            f"Install Claude Code or configure provider."
        )

    cmd = [
        claude_bin,
        "-p",
        "--model",
        "claude-sonnet-4-6",
        "--bare",
        "--setting-sources",
        "",
        "--tools",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--no-session-persistence",
        "--system-prompt",
        "Ты аккуратный редактор, делающий конспекты. Отвечай только готовым текстом конспекта в Markdown.",
    ]

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Claude summary requires ANTHROPIC_API_KEY environment variable to be set.")

    with tempfile.TemporaryDirectory() as isolated_cwd:
        minimal_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": isolated_cwd,
            "ANTHROPIC_API_KEY": api_key,
        }
        lock_fd = GLOBAL_LOCK.prepare_inheritable_fd()
        # Supervised execution under guardian (F01)
        token = uuid.uuid4().hex[:8]
        retcode, stdout, stderr = run_guarded_command(
            cmd,
            action="summary",
            session_id=session_id or "summary",
            token=token,
            cwd=isolated_cwd,
            timeout_sec=timeout_sec,
            cancel_event=cancel_event,
            on_proc_start=on_proc_start,
            lock_fd=lock_fd,
            stdin_text=prompt,
            env=minimal_env,
        )
        if retcode != 0:
            raise RuntimeError(f"Claude CLI summary failed (code {retcode}): {stderr}")

        result = stdout.strip()
        if not result:
            raise RuntimeError("Claude CLI returned empty summary")
        return result


def generate_summary(
    session_id: str,
    provider: str = SUMMARY_PROVIDER_NONE,
    template_name: str = TEMPLATE_MEETING,
    user_opt_in: bool = False,
    log_fn: Callable[[str], None] | None = None,
    claude_bin: str = "claude",
    cancel_event: threading.Event | None = None,
    on_proc_start: Callable[[subprocess.Popen[str]], None] | None = None,
) -> str | None:
    """Generate summary for a session transcript according to selected provider and template."""
    session_dir = get_session_dir(session_id)
    manifest = load_session(session_id)
    if not manifest:
        raise StorageError(f"Session {session_id} not found")

    transcript_path = session_dir / "transcript.txt"
    if not is_safe_regular_file(transcript_path):
        raise StorageError(f"No transcript.txt found for session {session_id}")

    transcript_text = safe_read_text(transcript_path, root=OUT_DIR).strip()
    if not transcript_text:
        raise StorageError("Transcript is empty, cannot generate summary")

    summary_path = session_dir / "summary.md"

    if provider == SUMMARY_PROVIDER_NONE:
        if log_fn:
            log_fn("Summary provider is 'none'. Preserving local privacy (no external transmission).")
        return None

    # Strict boolean identity check: prevents bool('false') truthiness
    if user_opt_in is not True:
        raise ValueError("External summary requires explicit user opt-in confirmation before sending transcript.")

    if provider == SUMMARY_PROVIDER_CODEX:
        raise ValueError(
            "Codex CLI summary is disabled: read-only sandbox permits reading arbitrary local files under prompt injection. "
            "Requires proven zero-tool isolation."
        )

    resolved_provider = provider
    if provider == SUMMARY_PROVIDER_AUTO:
        if shutil.which(claude_bin):
            resolved_provider = SUMMARY_PROVIDER_CLAUDE
        else:
            raise RuntimeError("No available isolated summary provider installed (Claude CLI required).")

    if resolved_provider != SUMMARY_PROVIDER_CLAUDE:
        raise ValueError(f"Unsupported summary provider: {resolved_provider}")

    GLOBAL_LOCK.acquire("summary", session_id)
    try:
        current_manifest = load_session(session_id) or manifest
        current_manifest.summary_status = "running"
        save_session(current_manifest)

        prompt = build_summary_prompt(transcript_text, template_name=template_name)
        if log_fn:
            log_fn(f"Generating summary with Claude CLI (template: {template_name})...")

        try:
            summary_text = run_claude_summary(
                prompt,
                claude_bin=claude_bin,
                cancel_event=cancel_event,
                on_proc_start=on_proc_start,
                session_id=session_id,
            )
            atomic_write_text(summary_path, summary_text)

            # Reload latest manifest to prevent overwriting intermediate changes
            current_manifest = load_session(session_id) or manifest
            current_manifest.summary_status = "completed"
            current_manifest.has_summary = True
            save_session(current_manifest)

            if log_fn:
                log_fn(f"Summary generated successfully: {len(summary_text.split())} words.")

            return summary_text
        except InterruptedError:
            current_manifest = load_session(session_id) or manifest
            current_manifest.summary_status = "interrupted"
            save_session(current_manifest)
            raise
        except Exception:
            current_manifest = load_session(session_id) or manifest
            current_manifest.summary_status = "failed"
            save_session(current_manifest)
            raise
    finally:
        GLOBAL_LOCK.release()
