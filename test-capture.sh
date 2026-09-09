#!/bin/bash
# Быстрый тест ДО вебинара: 8 секунд записи + проверка уровня звука BlackHole.
# Не изменяет системные настройки звука.
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE"

export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/pycache}"
exec python3 -m recorder.cli record --kind zoom --test
