"""Component installer service for whisper.cpp binary and official model."""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import threading
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable

from .constants import BASE_DIR, DEFAULT_MODEL_PATH, MODELS_DIR
from .lock import GLOBAL_LOCK, LockBusyError
from .media_tools import (
    CATALOG_MODEL_NAME,
    CATALOG_MODEL_SHA256,
    CATALOG_MODEL_SIZE,
    invalidate_media_tools_cache,
    verify_whisper_engine,
    verify_whisper_model,
)
from .storage import (
    StorageError,
    check_disk_space,
    is_safe_regular_file,
    safe_make_dir,
)

# Pinned official sources
PINNED_WHISPER_VERSION = "v1.9.3"
PINNED_WHISPER_TAG = "v1.9.3"
PINNED_WHISPER_TARBALL_URL = "https://github.com/ggml-org/whisper.cpp/archive/refs/tags/v1.9.3.tar.gz"
PINNED_WHISPER_TARBALL_SHA256 = "1650f884effba487025143bd8facd2f9fb40a83b3737a732803c67a8d659d9c0"
PINNED_CMAKE_VERSION = "4.4.3"

PINNED_MODEL_FILENAME = CATALOG_MODEL_NAME
PINNED_MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin"
PINNED_MODEL_SIZE = CATALOG_MODEL_SIZE
PINNED_MODEL_SHA256 = CATALOG_MODEL_SHA256
MODEL_DOWNLOAD_DEADLINE_SEC = 1800  # 30 minutes overall download deadline

REQUIRED_DISK_BYTES = 3 * 1024 * 1024 * 1024  # 3.0 GB


class InstallerManager:
    """Manages asynchronous download and build of Whisper components with cross-process coordination."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._cancel_requested = False
        self._active_proc: subprocess.Popen | None = None
        self._state: dict[str, Any] = {
            "status": "idle",  # idle, running, completed, failed
            "stage": "idle",   # idle, checking, downloading_model, verifying_model, building_whisper, completed, failed
            "progress_percent": 0.0,
            "bytes_downloaded": 0,
            "bytes_total": PINNED_MODEL_SIZE,
            "speed_mb_s": 0.0,
            "eta_sec": None,
            "message": "Компоненты не устанавливаются",
            "error": None,
            "started_at": None,
            "finished_at": None,
        }

    def get_status(self) -> dict[str, Any]:
        """Return a copy of the current installation state with unified aliases."""
        with self._lock:
            s = dict(self._state)
            s["is_running"] = s["status"] == "running"
            s["completed"] = s["status"] == "completed"
            s["phase"] = s["stage"]
            s["progress"] = s["progress_percent"]
            return s

    def is_running(self) -> bool:
        """Check if an installation job is currently active."""
        with self._lock:
            return self._state["status"] == "running"

    def cancel_install(self) -> None:
        """Gracefully cancel installation job and terminate any active subprocess."""
        self._cancel_requested = True
        with self._lock:
            proc = self._active_proc
        if proc is not None:
            try:
                if proc.poll() is None:
                    from .capture import stop_and_reap_process_group
                    stop_and_reap_process_group(proc, timeout_sec=2.0)
            except Exception:
                pass

        if self._thread and self._thread.is_alive() and self._thread != threading.current_thread():
            self._thread.join(timeout=3.0)

    def start_install(
        self,
        force: bool = False,
        on_before_start: Callable[[threading.Thread], None] | None = None,
        on_rollback: Callable[[], None] | None = None,
        on_finish: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """Start background installation job with cross-process reservation."""
        with self._lock:
            if self._state["status"] == "running":
                raise RuntimeError("Установка компонентов уже выполняется")

            # Check disk space upfront before taking lock
            disk = check_disk_space()
            if disk["free_bytes"] < REQUIRED_DISK_BYTES:
                free_gb = round(disk["free_bytes"] / (1024 * 1024 * 1024), 2)
                raise StorageError(
                    f"Недостаточно свободного места на диске: требуется минимум 3.0 ГБ, доступно {free_gb} ГБ"
                )

            # Cross-process reservation / lock acquisition
            try:
                GLOBAL_LOCK.acquire("install", "installer_job")
            except LockBusyError as exc:
                raise RuntimeError(f"Невозможно запустить установку: {exc}") from exc

            self._cancel_requested = False
            self._active_proc = None
            self._state = {
                "status": "running",
                "stage": "checking",
                "progress_percent": 0.0,
                "bytes_downloaded": 0,
                "bytes_total": PINNED_MODEL_SIZE,
                "speed_mb_s": 0.0,
                "eta_sec": None,
                "message": "Проверка существующих компонентов...",
                "error": None,
                "started_at": time.time(),
                "finished_at": None,
            }

            self._thread = threading.Thread(
                target=self._run_install_worker,
                args=(force, on_finish),
                daemon=True,
            )

            if on_before_start:
                try:
                    on_before_start(self._thread)
                except Exception as exc:
                    GLOBAL_LOCK.release(expected_session_id="installer_job")
                    self._state["status"] = "failed"
                    self._state["stage"] = "failed"
                    self._state["error"] = f"Failed in on_before_start: {exc}"
                    self._state["message"] = self._state["error"]
                    self._state["finished_at"] = time.time()
                    raise

            try:
                self._thread.start()
            except Exception as exc:
                if on_rollback:
                    try:
                        on_rollback()
                    except Exception:
                        pass
                GLOBAL_LOCK.release(expected_session_id="installer_job")
                self._state["status"] = "failed"
                self._state["stage"] = "failed"
                self._state["error"] = f"Failed to spawn installer thread: {exc}"
                self._state["message"] = self._state["error"]
                self._state["finished_at"] = time.time()
                raise

            return dict(self._state)

    def _set_stage(self, stage: str, message: str, percent: float | None = None) -> None:
        with self._lock:
            self._state["stage"] = stage
            self._state["message"] = message
            if percent is not None:
                self._state["progress_percent"] = round(percent, 1)

    def _run_cmd_with_reap(
        self,
        cmd: list[str],
        timeout_sec: float,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Execute a command with finite timeout and strict process reaping."""
        if self._cancel_requested:
            raise InterruptedError("Установка отменена оператором")

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(cwd) if cwd else None,
            env=env,
            start_new_session=True,
        )
        with self._lock:
            self._active_proc = proc

        try:
            stdout, stderr = proc.communicate(timeout=timeout_sec)
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=proc.returncode,
                stdout=stdout,
                stderr=stderr,
            )
        except subprocess.TimeoutExpired:
            from .capture import stop_and_reap_process_group
            stop_and_reap_process_group(proc, timeout_sec=2.0)
            try:
                stdout, stderr = proc.communicate(timeout=1.0)
            except Exception:
                stdout, stderr = "", ""
            raise TimeoutError(f"Превышено время ожидания команды {cmd[0]} ({timeout_sec} сек)")
        except Exception:
            if proc.poll() is None:
                from .capture import stop_and_reap_process_group
                stop_and_reap_process_group(proc, timeout_sec=2.0)
                try:
                    proc.communicate(timeout=1.0)
                except Exception:
                    pass
            raise
        finally:
            with self._lock:
                self._active_proc = None

    def _run_install_worker(self, force: bool, on_finish: Callable[[], None] | None = None) -> None:
        try:
            work_bin_dir = BASE_DIR / "work" / "bin"
            work_bin_dir.mkdir(parents=True, exist_ok=True)
            MODELS_DIR.mkdir(parents=True, exist_ok=True)

            target_whisper = work_bin_dir / "whisper-cli"
            target_model = MODELS_DIR / PINNED_MODEL_FILENAME

            # Step 1: Check existing model with full verification
            model_needs_download = True
            if not force:
                m_info = verify_whisper_model(target_model, force_recheck=True)
                if m_info["ready"] and m_info["state"] == "verified":
                    self._set_stage("checking", "Модель large-v3-turbo уже загружена и проверена", 50.0)
                    model_needs_download = False

            # Step 2: Download model if needed
            if model_needs_download:
                self._download_and_verify_model(target_model)

            # Step 3: Check existing whisper binary with full verification
            binary_needs_build = True
            if not force:
                ready, w_path, w_ver, w_err = verify_whisper_engine(str(target_whisper), force_recheck=True)
                if ready:
                    self._set_stage("checking", f"Исполняемый файл whisper-cli уже готов ({w_ver})", 90.0)
                    binary_needs_build = False

            # Step 4: Build whisper-cli if needed
            if binary_needs_build:
                self._build_whisper_static(target_whisper)

            # Step 5: Final preflight verification
            invalidate_media_tools_cache()
            ready, _, w_ver, _ = verify_whisper_engine(str(target_whisper), force_recheck=True)
            m_info = verify_whisper_model(target_model, force_recheck=True)
            if not (ready and m_info["ready"]):
                raise RuntimeError("Финальная верификация установленных компонентов не удалась")

            with self._lock:
                self._state["status"] = "completed"
                self._state["stage"] = "completed"
                self._state["progress_percent"] = 100.0
                self._state["message"] = f"Компоненты распознавания успешно установлены и проверены ({w_ver})!"
                self._state["finished_at"] = time.time()

        except Exception as exc:
            with self._lock:
                self._state["status"] = "failed"
                self._state["stage"] = "failed"
                self._state["error"] = str(exc)
                self._state["message"] = f"Ошибка установки: {exc}"
                self._state["finished_at"] = time.time()
        finally:
            try:
                GLOBAL_LOCK.release(expected_session_id="installer_job")
            except Exception:
                pass
            if on_finish:
                try:
                    on_finish()
                except Exception:
                    pass

    def _download_and_verify_model(self, target_model: Path) -> None:
        self._set_stage("downloading_model", "Подключение к HuggingFace...", 0.0)

        # Unique staging part file with O_EXCL and O_NOFOLLOW
        part_name = f".{PINNED_MODEL_FILENAME}.part.{os.getpid()}.{uuid.uuid4().hex[:8]}"
        part_path = MODELS_DIR / part_name

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW

        fd = os.open(part_path, flags, 0o600)
        fd_closed = False
        success = False
        try:
            req = urllib.request.Request(PINNED_MODEL_URL, headers={"User-Agent": "webinar-recorder-installer/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                total_size = int(resp.headers.get("Content-Length", PINNED_MODEL_SIZE))
                if total_size != PINNED_MODEL_SIZE:
                    raise ValueError(f"Сервер HuggingFace вернул неверный Content-Length: {total_size} != {PINNED_MODEL_SIZE}")

                with os.fdopen(fd, "wb", closefd=True) as f:
                    fd_closed = True
                    downloaded = 0
                    start_time = time.time()
                    deadline = start_time + MODEL_DOWNLOAD_DEADLINE_SEC
                    last_update = start_time
                    h = hashlib.sha256()

                    chunk_size = 1024 * 1024  # 1MB
                    while True:
                        if self._cancel_requested:
                            raise InterruptedError("Загрузка отменена пользователем")
                        if time.time() > deadline:
                            raise TimeoutError(f"Превышен общий лимит времени на скачивание модели ({MODEL_DOWNLOAD_DEADLINE_SEC} сек)")
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        h.update(chunk)
                        downloaded += len(chunk)

                        if downloaded > PINNED_MODEL_SIZE + 65536:
                            raise ValueError(f"Размер скачиваемых данных превысил допустимый предел ({downloaded} байт)")

                        now = time.time()
                        if now - last_update >= 0.5:
                            elapsed = max(now - start_time, 0.001)
                            speed = downloaded / elapsed
                            remaining_bytes = max(total_size - downloaded, 0)
                            eta = remaining_bytes / speed if speed > 0 else 0
                            pct = (downloaded / total_size * 50.0)

                            with self._lock:
                                self._state["bytes_downloaded"] = downloaded
                                self._state["bytes_total"] = total_size
                                self._state["progress_percent"] = round(pct, 1)
                                self._state["speed_mb_s"] = round(speed / (1024 * 1024), 2)
                                self._state["eta_sec"] = round(eta, 1)
                                self._state["message"] = (
                                    f"Скачивание модели large-v3-turbo: {downloaded // (1024*1024)} / "
                                    f"{total_size // (1024*1024)} МБ ({round(speed / (1024*1024), 1)} МБ/с)"
                                )
                            last_update = now

                    f.flush()

            # Verify size
            actual_size = part_path.stat().st_size
            if actual_size != PINNED_MODEL_SIZE:
                raise ValueError(f"Размер скачанного файла ({actual_size} байт) не совпадает с ожидаемым ({PINNED_MODEL_SIZE} байт)")

            # Verify digest
            self._set_stage("verifying_model", "Проверка контрольной суммы SHA-256...", 55.0)
            digest = h.hexdigest()
            if digest != PINNED_MODEL_SHA256:
                raise ValueError(f"Контрольная сумма SHA-256 не совпадает: {digest} != {PINNED_MODEL_SHA256}")

            # Atomic rename into place
            part_path.chmod(0o644)
            os.replace(part_path, target_model)
            success = True
            invalidate_media_tools_cache()
            self._set_stage("verifying_model", "Модель проверена и сохранена", 60.0)

        finally:
            if not fd_closed:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if not success:
                part_path.unlink(missing_ok=True)

    def _prepare_cmake(self) -> Path:
        """Locate existing CMake or prepare isolated pinned CMake in work/build_tools."""
        # 1. Check isolated build_tools
        cmake_local = BASE_DIR / "work" / "build_tools" / "cmake" / "data" / "bin" / "cmake"
        if cmake_local.is_file() and os.access(cmake_local, os.X_OK):
            try:
                res = self._run_cmd_with_reap([str(cmake_local), "--version"], timeout_sec=5.0)
                if res.returncode == 0:
                    return cmake_local
            except Exception:
                pass

        # 2. Check system PATH
        cmake_system = shutil.which("cmake")
        if cmake_system:
            try:
                res = self._run_cmd_with_reap([cmake_system, "--version"], timeout_sec=5.0)
                if res.returncode == 0:
                    return Path(cmake_system)
            except Exception:
                pass

        # 3. Prepare isolated cmake via python -m pip --target work/build_tools
        self._set_stage("building_whisper", "Подготовка изолированного CMake в work/build_tools...", 66.0)
        import sys
        build_tools_dir = BASE_DIR / "work" / "build_tools"
        build_tools_dir.mkdir(parents=True, exist_ok=True)

        pip_cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            f"cmake=={PINNED_CMAKE_VERSION}",
            "--target",
            str(build_tools_dir),
            "--no-user",
        ]
        res_pip = self._run_cmd_with_reap(pip_cmd, timeout_sec=120.0)
        if res_pip.returncode != 0:
            raise RuntimeError(f"Не удалось установить изолированный CMake: {res_pip.stderr.strip() or res_pip.stdout.strip()}")

        if cmake_local.is_file() and os.access(cmake_local, os.X_OK):
            return cmake_local

        raise RuntimeError("CMake подготовлен, но исполняемый файл не найден в work/build_tools/cmake/data/bin/cmake")

    def _build_whisper_static(self, target_whisper: Path) -> None:
        self._set_stage("building_whisper", "Подготовка компилятора и исходников whisper.cpp...", 65.0)
        cmake_bin = self._prepare_cmake()

        src_dir = BASE_DIR / "work" / "src_whisper_v193"
        if not (src_dir / "CMakeLists.txt").is_file():
            self._set_stage("building_whisper", "Загрузка исходного кода whisper.cpp v1.9.3...", 70.0)
            src_dir.mkdir(parents=True, exist_ok=True)

            tar_tmp = BASE_DIR / "work" / f".whisper_{uuid.uuid4().hex[:8]}.tar.gz"
            try:
                req = urllib.request.Request(PINNED_WHISPER_TARBALL_URL, headers={"User-Agent": "webinar-recorder-installer/1.0"})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    h = hashlib.sha256()
                    with open(tar_tmp, "wb") as f:
                        while chunk := resp.read(64 * 1024):
                            if self._cancel_requested:
                                raise InterruptedError("Загрузка отменена")
                            f.write(chunk)
                            h.update(chunk)
                    digest = h.hexdigest()
                    if digest != PINNED_WHISPER_TARBALL_SHA256:
                        raise ValueError(f"Контрольная сумма исходного кода whisper.cpp не совпадает: {digest}")

                # Safely extract stripping the root component
                with tarfile.open(tar_tmp, "r:gz") as tar:
                    members = tar.getmembers()
                    if not members:
                        raise ValueError("Архив исходного кода пуст")
                    prefix = members[0].name.split("/")[0] + "/"
                    for m in members:
                        if m.name.startswith(prefix):
                            m.path = m.name[len(prefix):]
                        if not m.path or m.path.startswith("/") or ".." in Path(m.path).parts:
                            continue
                        tar.extract(m, path=src_dir)
            finally:
                tar_tmp.unlink(missing_ok=True)

        build_dir = BASE_DIR / "work" / f"build_static_{os.getpid()}_{uuid.uuid4().hex[:6]}"
        build_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._set_stage("building_whisper", "Конфигурация CMake (Release, static, Metal embedded)...", 75.0)
            cfg_cmd = [
                str(cmake_bin),
                "-B", str(build_dir),
                "-S", str(src_dir),
                "-DBUILD_SHARED_LIBS=OFF",
                "-DGGML_METAL_EMBED_LIBRARY=ON",
                "-DCMAKE_BUILD_TYPE=Release",
                "-DWHISPER_BUILD_TESTS=OFF",
                "-DWHISPER_BUILD_EXAMPLES=ON",
            ]
            res_cfg = self._run_cmd_with_reap(cfg_cmd, timeout_sec=60.0)
            if res_cfg.returncode != 0:
                raise RuntimeError(f"Ошибка конфигурации CMake: {res_cfg.stderr.strip()}")

            self._set_stage("building_whisper", "Компиляция whisper-cli с ускорением Metal...", 85.0)
            build_cmd = [str(cmake_bin), "--build", str(build_dir), "--target", "whisper-cli", "-j4"]
            res_build = self._run_cmd_with_reap(build_cmd, timeout_sec=300.0)
            if res_build.returncode != 0:
                raise RuntimeError(f"Ошибка сборки whisper-cli: {res_build.stderr.strip()}")

            built_bin = build_dir / "bin" / "whisper-cli"
            if not built_bin.is_file():
                raise RuntimeError("Собранный бинарный файл whisper-cli не найден в каталоге сборки")

            # Unique staging binary
            work_bin_dir = target_whisper.parent
            staging_whisper = work_bin_dir / f".whisper-cli.staging.{os.getpid()}.{uuid.uuid4().hex[:8]}"
            try:
                shutil.copy2(built_bin, staging_whisper)
                staging_whisper.chmod(0o755)

                # Run smoke check on staging binary
                res_smoke = self._run_cmd_with_reap([str(staging_whisper), "--version"], timeout_sec=5.0)
                if res_smoke.returncode != 0:
                    raise RuntimeError(f"Собранный whisper-cli не прошёл smoke-тест (код {res_smoke.returncode}): {res_smoke.stderr.strip()}")

                # Atomic replace
                os.replace(staging_whisper, target_whisper)
                invalidate_media_tools_cache()
            finally:
                staging_whisper.unlink(missing_ok=True)

            self._set_stage("building_whisper", "whisper-cli успешно собран и размещён", 95.0)
        finally:
            shutil.rmtree(build_dir, ignore_errors=True)


# Global singleton installer manager
INSTALLER = InstallerManager()
atexit.register(INSTALLER.cancel_install)


def get_diarization_install_status() -> dict[str, Any]:
    """Get status of speaker diarization backend and models."""
    import platform
    from .diarizer import verify_diarizer_components
    status = verify_diarizer_components()
    arch = platform.machine()
    status["arch"] = arch
    status["arch_supported"] = (arch == "arm64")
    if arch != "arm64":
        status["blocked_reason"] = (
            "Установка diarization на macOS Intel x86_64 заблокирована: "
            "ожидает проверенных официальных дистрибутивов и контрольных сумм. "
            "Поддерживается macOS Apple Silicon arm64."
        )
    return status


def install_diarization_components(force: bool = False) -> dict[str, Any]:
    """Install or verify pinned sherpa-onnx binary, library, and models."""
    import platform
    arch = platform.machine()
    if arch != "arm64":
        raise RuntimeError(
            "Установка diarization на macOS Intel x86_64 заблокирована: "
            "ожидает проверенных официальных дистрибутивов и контрольных сумм. "
            "Поддерживается macOS Apple Silicon arm64."
        )

    status = get_diarization_install_status()
    if status["ready"] and not force:
        return status

    # Check local task spike directory as offline cache first
    spike_dir = BASE_DIR / "work" / "diarization-spike"
    work_bin_dir = BASE_DIR / "work" / "bin"
    work_lib_dir = BASE_DIR / "work" / "lib"
    models_diar_dir = MODELS_DIR / "diarization" / "sherpa-onnx-pyannote-segmentation-3-0"

    work_bin_dir.mkdir(parents=True, exist_ok=True)
    work_lib_dir.mkdir(parents=True, exist_ok=True)
    models_diar_dir.mkdir(parents=True, exist_ok=True)

    if spike_dir.exists():
        spike_bin = spike_dir / "bin" / "sherpa-onnx-offline-speaker-diarization"
        if spike_bin.exists():
            dest = work_bin_dir / "sherpa-onnx-offline-speaker-diarization"
            if not dest.exists() or force:
                shutil.copy2(spike_bin, dest)
                dest.chmod(0o755)

        spike_lib = spike_dir / "lib" / "libsherpa-onnx-c-api.dylib"
        if spike_lib.exists():
            dest_lib = work_lib_dir / "libsherpa-onnx-c-api.dylib"
            if not dest_lib.exists() or force:
                shutil.copy2(spike_lib, dest_lib)

        spike_onnx = spike_dir / "lib" / "libonnxruntime.dylib"
        if spike_onnx.exists():
            dest_onnx = work_lib_dir / "libonnxruntime.dylib"
            if not dest_onnx.exists() or force:
                shutil.copy2(spike_onnx, dest_onnx)

        spike_seg = spike_dir / "models" / "sherpa-onnx-pyannote-segmentation-3-0" / "model.int8.onnx"
        if spike_seg.exists():
            dest_seg = models_diar_dir / "model.int8.onnx"
            if not dest_seg.exists() or force:
                shutil.copy2(spike_seg, dest_seg)

        spike_emb = spike_dir / "models" / "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
        if spike_emb.exists():
            dest_emb = MODELS_DIR / "diarization" / "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
            if not dest_emb.exists() or force:
                shutil.copy2(spike_emb, dest_emb)

    # Re-check status with hash verification
    new_status = get_diarization_install_status()
    if not new_status["ready"]:
        raise RuntimeError(f"Не удалось подготовить компоненты диаризации: {', '.join(new_status.get('errors', []))}")

    return new_status

