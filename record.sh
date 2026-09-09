#!/bin/bash
# Записывает системный звук (Zoom) в нативной частоте устройства через BlackHole.
# Нативный захват короткими атомарными сегментами без потерь сэмплов.
# Остановить запись: Ctrl+C.
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE"

export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/pycache}"
exec python3 -m recorder.cli record --kind zoom "$@"
