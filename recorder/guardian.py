"""Guardian process and worker monitoring for capture and background tasks (R02).
Holds the kernel flock across parent termination and cleanly reaps child processes
and indexes closed audio segments on parent death or explicit stop.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .constants import (
    OUT_DIR,
    STATE_FAILED,
    STATE_INTERRUPTED,
    STATE_PROCESSING,
    STATE_READY,
    STATE_RECORDING,
)


def is_group_still_alive(pgid: int, pid: int) -> bool:
    """Check whether process or any member of its process group is still alive."""
    if pgid <= 0 and pid <= 0:
        return False
    if pid > 0:
        try:
            reaped, _ = os.waitpid(pid, os.WNOHANG)
            if reaped == pid:
                pass
        except (ChildProcessError, OSError):
            pass
    if pgid > 0:
        try:
            os.killpg(pgid, 0)
            return True
        except ProcessLookupError:
            pass
        except PermissionError:
            pass
        except OSError:
            pass
    if pid > 0:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, OSError):
            pass
    return False


def stop_child_group(pgid: int, pid: int, timeout_sec: float = 3.0) -> bool:
    """Stop child process group with SIGINT -> SIGTERM -> SIGKILL escalation.
    Returns True if entire group is confirmed dead, False if rogue processes remain."""
    if pgid <= 0 and pid <= 0:
        return True

    # 1. SIGINT
    if pgid > 0:
        try:
            os.killpg(pgid, signal.SIGINT)
        except OSError:
            pass
    if pid > 0:
        try:
            os.kill(pid, signal.SIGINT)
        except OSError:
            pass

    t_end = time.time() + min(1.5, timeout_sec)
    while time.time() < t_end:
        if not is_group_still_alive(pgid, pid):
            return True
        time.sleep(0.05)

    # 2. SIGTERM
    if pgid > 0:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except OSError:
            pass
    if pid > 0:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    t_end = time.time() + 1.0
    while time.time() < t_end:
        if not is_group_still_alive(pgid, pid):
            return True
        time.sleep(0.05)

    # 3. SIGKILL
    if pgid > 0:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass
    if pid > 0:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    t_end = time.time() + 1.5
    while time.time() < t_end:
        if not is_group_still_alive(pgid, pid):
            return True
        time.sleep(0.05)

    return not is_group_still_alive(pgid, pid)


def launch_guarded_process(
    cmd: list[str],
    *,
    action: str = "record",
    session_id: str,
    token: str,
    cwd: str | Path,
    log_path: str | Path | None = None,
    session_dir: str | Path | None = None,
    stdout_path: str | Path | None = None,
    stderr_path: str | Path | None = None,
    lock_fd: int | None = None,
    stdin_path: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.Popen[str]:
    """Spawn a guardian subprocess that holds lock and supervises the child worker."""
    guardian_cmd = [
        sys.executable,
        "-m",
        "recorder.guardian",
        "--action",
        action,
        "--session-id",
        session_id,
        "--token",
        token,
        "--cmd-json",
        json.dumps(cmd),
        "--cwd",
        str(cwd),
    ]
    pass_fds: list[int] = []
    if lock_fd is not None and lock_fd >= 0:
        guardian_cmd.extend(["--lock-fd", str(lock_fd)])
        pass_fds.append(lock_fd)
    if log_path:
        guardian_cmd.extend(["--log-path", str(log_path)])
    if session_dir:
        guardian_cmd.extend(["--session-dir", str(session_dir)])
    if stdout_path:
        guardian_cmd.extend(["--stdout-path", str(stdout_path)])
    if stderr_path:
        guardian_cmd.extend(["--stderr-path", str(stderr_path)])
    if stdin_path:
        guardian_cmd.extend(["--stdin-path", str(stdin_path)])

    proc = subprocess.Popen(
        guardian_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        pass_fds=pass_fds,
        start_new_session=True,
        text=True,
        env=env,
    )
    return proc


def run_guarded_command(
    cmd: list[str],
    *,
    action: str = "exec",
    session_id: str,
    token: str,
    cwd: str | Path,
    timeout_sec: float = 180.0,
    cancel_event: threading.Event | None = None,
    on_proc_start: Callable[[subprocess.Popen[str]], None] | None = None,
    lock_fd: int | None = None,
    stdin_text: str | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Execute command synchronously under guardian supervision with pipe deadlock prevention."""
    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as out_f, \
         tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as err_f:
        out_path = Path(out_f.name)
        err_path = Path(err_f.name)

    stdin_path: Path | None = None
    if stdin_text is not None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as in_f:
            in_f.write(stdin_text)
            stdin_path = Path(in_f.name)

    try:
        proc = launch_guarded_process(
            cmd,
            action=action,
            session_id=session_id,
            token=token,
            cwd=cwd,
            stdout_path=out_path,
            stderr_path=err_path,
            lock_fd=lock_fd,
            stdin_path=stdin_path,
            env=env,
        )
        if on_proc_start:
            try:
                on_proc_start(proc)
            except Exception:
                pass

        start_time = time.monotonic()
        while True:
            ret = proc.poll()
            if ret is not None:
                break

            if cancel_event and cancel_event.is_set():
                if proc.stdin and not proc.stdin.closed:
                    try:
                        proc.stdin.write("cancel\n")
                        proc.stdin.flush()
                        proc.stdin.close()
                    except (BrokenPipeError, OSError):
                        pass
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    stop_child_group(proc.pid, proc.pid, timeout_sec=2.0)
                raise InterruptedError(f"Operation {action} cancelled by operator")

            if time.monotonic() - start_time > timeout_sec:
                if proc.stdin and not proc.stdin.closed:
                    try:
                        proc.stdin.write("cancel\n")
                        proc.stdin.flush()
                        proc.stdin.close()
                    except (BrokenPipeError, OSError):
                        pass
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    stop_child_group(proc.pid, proc.pid, timeout_sec=2.0)
                raise TimeoutError(f"Command timed out after {timeout_sec}s: {cmd[0]}")

            time.sleep(0.05)

        stdout_text = out_path.read_text(encoding="utf-8", errors="replace") if out_path.exists() else ""
        stderr_text = err_path.read_text(encoding="utf-8", errors="replace") if err_path.exists() else ""
        return proc.returncode, stdout_text, stderr_text
    finally:
        out_path.unlink(missing_ok=True)
        err_path.unlink(missing_ok=True)
        if stdin_path:
            stdin_path.unlink(missing_ok=True)


def guardian_main() -> None:
    """Main entrypoint for guardian process."""
    import select

    parser = argparse.ArgumentParser(description="Guardian process for audio recording and tasks")
    parser.add_argument("--action", default="record")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--lock-fd", type=int, default=-1)
    parser.add_argument("--cmd-json", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--log-path", default="")
    parser.add_argument("--session-dir", default="")
    parser.add_argument("--stdout-path", default="")
    parser.add_argument("--stderr-path", default="")
    parser.add_argument("--stdin-path", default="")
    parser.add_argument("--segment-list", default=None)

    args = parser.parse_args()

    cmd: list[str] = json.loads(args.cmd_json)
    lock_fd = args.lock_fd
    session_id = args.session_id
    token = args.token
    cwd = args.cwd
    log_path = Path(args.log_path) if args.log_path else None
    session_dir = Path(args.session_dir) if args.session_dir else None
    stdout_path = Path(args.stdout_path) if args.stdout_path else None
    stderr_path = Path(args.stderr_path) if args.stderr_path else None
    stdin_path = Path(args.stdin_path) if args.stdin_path else None

    # 1. Update lock file metadata with guardian information
    if lock_fd >= 0:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            pass
        try:
            os.lseek(lock_fd, 0, os.SEEK_SET)
            os.ftruncate(lock_fd, 0)
            meta = {
                "pid": os.getpid(),
                "pgid": os.getpgrp(),
                "action": args.action,
                "session_id": session_id,
                "token": token,
                "guardian": True,
                "acquired_at": time.time(),
            }
            os.write(lock_fd, json.dumps(meta).encode("utf-8"))
            os.fsync(lock_fd)
        except OSError:
            pass

    # 2. Setup stdout/stderr and spawn child worker (Fix G03: separate child stdin from control stdin)
    out_files_to_close = []
    if log_path:
        log_file = open(log_path, "a", encoding="utf-8")
        out_files_to_close.append(log_file)
        child_stdout = log_file
        child_stderr = log_file
    elif stdout_path and stderr_path:
        stdout_file = open(stdout_path, "w", encoding="utf-8")
        stderr_file = open(stderr_path, "w", encoding="utf-8")
        out_files_to_close.extend([stdout_file, stderr_file])
        child_stdout = stdout_file
        child_stderr = stderr_file
    else:
        child_stdout = subprocess.DEVNULL
        child_stderr = subprocess.DEVNULL

    if stdin_path and stdin_path.exists():
        stdin_file = open(stdin_path, "r", encoding="utf-8")
        out_files_to_close.append(stdin_file)
        child_stdin = stdin_file
    else:
        child_stdin = subprocess.DEVNULL

    child = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=child_stdin,
        stdout=child_stdout,
        stderr=child_stderr,
        start_new_session=True,
    )
    try:
        child_pgid = os.getpgid(child.pid)
    except OSError:
        child_pgid = child.pid

    # Write active marker
    active_marker = None
    if args.action == "record":
        active_marker = OUT_DIR / ".active_capture.json"
    elif args.action == "transcribe":
        active_marker = OUT_DIR / ".active_processing.json"

    if active_marker:
        try:
            marker_data = {
                "session_id": session_id,
                "pid": child.pid,
                "guardian_pid": os.getpid(),
                "token": token,
                "action": args.action,
                "start_time": time.time(),
            }
            active_marker.write_text(json.dumps(marker_data, indent=2), encoding="utf-8")
        except OSError:
            pass

    # 3. Setup signal handlers to translate SIGINT / SIGTERM into graceful child group stop
    sig_received = [False]
    def _sig_handler(signum: int, frame: Any) -> None:
        sig_received[0] = True

    try:
        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)
    except (ValueError, OSError):
        pass

    stop_reason = "normal"
    child_exit_code = 0
    stopped_clean = False
    try:
        while True:
            if sig_received[0]:
                stop_reason = "cancel"
                break

            poll_res = child.poll()
            if poll_res is not None:
                stop_reason = "child_exit"
                child_exit_code = poll_res
                break

            rlist, _, _ = select.select([sys.stdin], [], [], 0.05)
            if rlist:
                line = sys.stdin.readline()
                if not line:
                    stop_reason = "parent_death"
                    break
                cmd_text = line.strip().lower()
                if cmd_text in ("stop", "cancel", "quit"):
                    stop_reason = cmd_text
                    break
    except BaseException:
        stop_reason = "cancel"
    finally:
        # GUARANTEED child group cleanup: supervisor never exits before child group is dead!
        if stop_reason == "child_exit":
            stopped_clean = stop_child_group(child_pgid, child.pid, timeout_sec=1.5)
        else:
            stopped_clean = stop_child_group(child_pgid, child.pid, timeout_sec=2.5)
            child_exit_code = 0 if stop_reason == "stop" else (130 if stop_reason in ("cancel", "sigint", "sigterm") else 1)

        if not stopped_clean:
            stop_child_group(child_pgid, child.pid, timeout_sec=2.0)
            while is_group_still_alive(child_pgid, child.pid):
                time.sleep(0.05)
            stopped_clean = True

        try:
            child.wait(timeout=1.0)
        except Exception:
            pass

        for f in out_files_to_close:
            try:
                f.flush()
                f.close()
            except Exception:
                pass

    # 5. Clean active marker
    if active_marker and active_marker.exists():
        try:
            m_data = json.loads(active_marker.read_text(encoding="utf-8"))
            if m_data.get("token") == token:
                active_marker.unlink(missing_ok=True)
        except Exception:
            active_marker.unlink(missing_ok=True)

    # 6. Reconcile manifest
    if session_dir and (session_dir / "session.json").exists():
        try:
            from .session import load_session, save_session
            m = load_session(session_id)
            if m:
                if args.action == "record":
                    from .capture import CAPTURE_MANAGER
                    m = CAPTURE_MANAGER._index_raw_chunks(m, is_stopped=True)
                    m.pid = None
                    if not stopped_clean:
                        m.status = STATE_INTERRUPTED
                        m.error_message = "Capture interrupted: child processes could not be fully reaped"
                    elif child_exit_code not in (0, 255, -2, -15):
                        if not m.raw_chunks:
                            m.status = STATE_FAILED
                            m.error_message = f"Capture process failed (exit code {child_exit_code})"
                        else:
                            m.status = STATE_INTERRUPTED
                            m.error_message = f"Capture interrupted unexpectedly (exit code {child_exit_code})"
                    else:
                        m.status = STATE_READY if m.raw_chunks else STATE_INTERRUPTED
                        if stop_reason == "parent_death":
                            m.error_message = "Capture cleanly finalized after parent termination"
                        elif not m.raw_chunks:
                            m.error_message = "No audio data was captured"
                        else:
                            m.error_message = None
                    save_session(m)
                elif args.action == "transcribe" and m.status == STATE_PROCESSING:
                    if stop_reason in ("parent_death", "cancel", "error") or child_exit_code != 0:
                        m.status = STATE_INTERRUPTED if stop_reason == "cancel" else STATE_FAILED
                        m.error_message = (
                            f"Transcription failed (exit code {child_exit_code})"
                            if child_exit_code != 0
                            else f"Transcription interrupted: {stop_reason}"
                        )
                        save_session(m)
        except Exception:
            pass

    # 7. Close lock fd and exit (or retain lock indefinitely if cleanup failed and children remain alive)
    if not stopped_clean:
        while is_group_still_alive(child_pgid, child.pid):
            time.sleep(0.5)
        sys.exit(1)

    if lock_fd >= 0:
        try:
            os.close(lock_fd)
        except OSError:
            pass

    sys.exit(child_exit_code)


if __name__ == "__main__":
    guardian_main()
