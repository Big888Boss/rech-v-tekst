#!/bin/bash
# Запись с МИКРОФОНА для офлайн-лекций в нативной частоте устройства (без BlackHole).
# Нативный захват короткими атомарными сегментами.
#
#   ./record-mic.sh            запись (авто-выбор микрофона)
#   ./record-mic.sh --list     показать доступные аудио-входы и выйти
#   ./record-mic.sh --test     8-секундный тест уровня звука
#   MIC=2 ./record-mic.sh      принудительно устройство с индексом 2
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE"

export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/pycache}"
exec python3 -m recorder.cli record --kind mic "$@"
