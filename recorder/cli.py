"""Command-line interface for webinar recording and local processing."""
from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from .capture import CAPTURE_MANAGER
from .constants import (
    SOURCE_MIC,
    SOURCE_ZOOM,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_INTERRUPTED,
    STATE_READY,
    STATE_RECORDING,
    SUMMARY_PROVIDER_NONE,
)
from .device import find_device, list_audio_devices, run_volume_check
from .export import generate_all_exports
from .normalize import import_media_file as import_user_media_file
from .session import (
    create_session,
    get_latest_session,
    load_session,
    save_session,
    validate_session_id,
)
from .storage import StorageError
from .summary import generate_summary
from .transcribe import TRANSCRIBE_MANAGER


def cli_record(args: argparse.Namespace) -> int:
    kind = getattr(args, "kind", "zoom")
    device_index = getattr(args, "mic", None)
    if device_index is None and os.environ.get("MIC"):
        try:
            device_index = int(os.environ["MIC"])
        except ValueError:
            pass

    if getattr(args, "list", False):
        devs, _ = list_audio_devices()
        print("Available audio input devices:")
        for d in devs:
            print(f"  [{d['index']}] {d['name']} ({d['kind']})")
        return 0

    if getattr(args, "test", False):
        dev_kind = "blackhole" if kind == "zoom" else kind
        target_dev = find_device(kind=dev_kind, index=device_index)
        if not target_dev:
            if kind == "zoom":
                print("Error: BlackHole device not found for zoom capture. Install BlackHole or select a device with --mic.", file=sys.stderr)
            else:
                print(f"Error: No audio input device found for kind '{kind}'", file=sys.stderr)
            return 1
        dev_idx = target_dev["index"]
        dev_name = target_dev["name"]
        print(f"Testing audio level (3s) on device [{dev_idx}] '{dev_name}' ({target_dev['kind']})...")
        res = run_volume_check(dev_idx, duration_sec=3.0)
        print(f"Mean volume: {res.get('mean_volume_db')} dB, Max volume: {res.get('max_volume_db')} dB")
        if res.get("status") == "silence":
            print("Silence: no audio signal detected. Check microphone permissions and device selection.")
            return 1
        elif res.get("status") == "error":
            print(f"Error checking volume: {res.get('message')}", file=sys.stderr)
            return 1
        else:
            print(f"Audio signal detected successfully (peak: {res.get('max_volume_db')} dB). Ready to record.")
            return 0

    # Determine session name (CLI-01: support both positional and --session flag)
    session_flag = getattr(args, "session_flag", None)
    session_pos = getattr(args, "session", None)
    if session_flag and session_pos and session_flag != session_pos:
        print(f"Error: Conflicting session arguments: '{session_pos}' vs '--session {session_flag}'", file=sys.stderr)
        return 2

    raw_session = session_flag or session_pos or os.environ.get("SESSION")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    prefix = "webinar" if kind == SOURCE_ZOOM else "lecture"
    session_id = raw_session or f"{prefix}_{stamp}"

    try:
        session_id = validate_session_id(session_id)
    except Exception as e:
        print(f"Error: Invalid session ID '{session_id}': {e}", file=sys.stderr)
        return 2

    print(f"=== Starting Webinar Recorder ===")
    print(f"Session: {session_id}")
    print(f"Source: {kind}")
    print(f"Press Ctrl+C to stop recording safely.\n")

    stop_requested = False

    def handle_signal(sig, frame):
        nonlocal stop_requested
        stop_requested = True

    prev_sigint = signal.signal(signal.SIGINT, handle_signal)
    prev_sigterm = signal.signal(signal.SIGTERM, handle_signal)

    try:
        CAPTURE_MANAGER.start_capture(
            session_id=session_id,
            source_kind=kind,
            device_index=device_index,
        )

        while not stop_requested:
            if not CAPTURE_MANAGER.is_active(session_id):
                # Process terminated on its own
                break
            time.sleep(0.2)

        if stop_requested:
            print("\nStopping recording...")
            manifest = CAPTURE_MANAGER.stop_capture(session_id)
        else:
            # Wait for watcher thread to finish indexing and persist terminal status (R08)
            CAPTURE_MANAGER.wait_for_completion(session_id, timeout=5.0)
            manifest = load_session(session_id)

        if not manifest:
            print("Recording ended with missing manifest", file=sys.stderr)
            return 1

        if manifest.status == STATE_READY or manifest.status == STATE_COMPLETED:
            print(f"Recording stopped. Saved {len(manifest.raw_chunks)} raw chunks in out/{session_id}/raw/")
            print(f"Run `./process.sh {session_id}` to transcribe.")
            return 0
        elif manifest.status == STATE_FAILED:
            print(f"Recording failed: {manifest.error_message or 'unknown error'}", file=sys.stderr)
            return 1
        elif manifest.status == STATE_INTERRUPTED:
            print(f"Recording interrupted: {manifest.error_message or 'session incomplete'}", file=sys.stderr)
            return 2
        else:
            print(f"Recording ended with non-terminal status: {manifest.status}", file=sys.stderr)
            return 1

    except Exception as exc:
        print(f"Recording error: {exc}", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGINT, prev_sigint)
        signal.signal(signal.SIGTERM, prev_sigterm)


def cli_process(args: argparse.Namespace) -> int:
    target = args.target or os.environ.get("SESSION")
    session_id: str | None = None

    if target:
        target_path = Path(target)
        if target_path.is_file():
            # Imported media file (CLI-02: safe session ID preserving title)
            safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", target_path.stem)[:30].strip("_") or "media"
            stamp = time.strftime("%Y%m%d_%H%M%S")
            session_id = f"import_{safe_stem}_{stamp}_{uuid.uuid4().hex[:6]}"
            original_title = target_path.name
            print(f"Importing media file: {target_path} -> session {session_id}")
            cancel_ev = threading.Event()
            active_import_proc = None

            def handle_import_sig(sig, frame):
                cancel_ev.set()
                if active_import_proc and active_import_proc.poll() is None:
                    from .capture import stop_and_reap_process_group
                    stop_and_reap_process_group(active_import_proc, timeout_sec=1.5)

            def on_import_proc(p: subprocess.Popen[str]) -> None:
                nonlocal active_import_proc
                active_import_proc = p

            old_sigint = signal.signal(signal.SIGINT, handle_import_sig)
            old_sigterm = signal.signal(signal.SIGTERM, handle_import_sig)
            try:
                manifest = import_user_media_file(
                    target_path,
                    session_id=session_id,
                    title=original_title,
                    cancel_event=cancel_ev,
                    on_proc_start=on_import_proc,
                )
            except (Exception, KeyboardInterrupt) as exc:
                print(f"Media import error: {exc}", file=sys.stderr)
                return 1
            finally:
                signal.signal(signal.SIGINT, old_sigint)
                signal.signal(signal.SIGTERM, old_sigterm)
        elif (Path("out") / target).is_dir() or target_path.is_dir():
            session_id = target_path.name if target_path.is_dir() else target
        else:
            session_id = target
    else:
        latest = get_latest_session()
        if not latest:
            print("Error: No sessions found in out/. Specify: ./process.sh <session_name|audio_file>", file=sys.stderr)
            return 1
        session_id = latest.session_id

    try:
        session_id = validate_session_id(session_id)
    except Exception as e:
        print(f"Error: Invalid session ID: {e}", file=sys.stderr)
        return 1

    manifest = load_session(session_id)
    if not manifest:
        print(f"Error: Session {session_id} not found in out/", file=sys.stderr)
        return 1

    # CLI-04: Support WHISPER_LANG and saved settings language
    from .config import get_effective_settings
    eff_cfg = get_effective_settings()
    eff_lang = eff_cfg.get("effective", {}).get("language")
    lang = args.lang or eff_lang or "ru"
    summary_provider = args.summary_provider or os.environ.get("SUMMARY_PROVIDER", SUMMARY_PROVIDER_NONE)

    print(f"Processing session: {session_id} (lang={lang}, summary_provider={summary_provider})")

    # Step 1: Transcribe
    try:
        manifest = TRANSCRIBE_MANAGER.transcribe_session(session_id, lang=lang)
    except Exception as exc:
        print(f"Transcription failed: {exc}", file=sys.stderr)
        return 1

    # Step 2: Export formats
    exports = generate_all_exports(session_id)
    print(f"Transcript generated: {exports['txt']} ({manifest.word_count} words)")
    print(f"Generated exports: SRT, VTT, Markdown, JSON.")

    # Step 3: Summary (opt-in)
    if summary_provider != SUMMARY_PROVIDER_NONE:
        print(f"Generating summary with provider: {summary_provider}...")
        try:
            summary = generate_summary(
                session_id=session_id,
                provider=summary_provider,
                template_name=args.template or "meeting",
                user_opt_in=True,
            )
            if summary:
                print(f"Summary generated: out/{session_id}/summary.md")
        except Exception as exc:
            print(f"Summary generation warning: {exc}", file=sys.stderr)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Webinar Recorder CLI")
    subparsers = parser.add_subparsers(dest="command")

    # record
    rec_parser = subparsers.add_parser("record", help="Start audio capture")
    rec_parser.add_argument("session", nargs="?", default=None, help="Session ID (positional)")
    rec_parser.add_argument("--kind", choices=["zoom", "mic"], default="zoom")
    rec_parser.add_argument("--session", "-s", dest="session_flag", help="Session ID (flag)")
    rec_parser.add_argument("--mic", type=int, help="Device index")
    rec_parser.add_argument("--list", action="store_true", help="List audio devices")
    rec_parser.add_argument("--test", action="store_true", help="Test volume levels")

    # process
    proc_parser = subparsers.add_parser("process", help="Transcribe session")
    proc_parser.add_argument("target", nargs="?", help="Session ID or audio file path")
    proc_parser.add_argument("--lang", "-l", default=None, help="Spoken language (defaults to ru or WHISPER_LANG)")
    proc_parser.add_argument("--summary-provider", "-p", default=None, help="Summary provider (none, claude)")
    proc_parser.add_argument("--template", "-t", default="meeting", help="Summary template")

    args = parser.parse_args()
    if args.command == "record":
        return cli_record(args)
    elif args.command == "process":
        return cli_process(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
