#!/bin/bash
# Офлайн-нормализация в 16 кГц mono, транскрипция через Whisper и саммари.
# Использование:  ./process.sh                         (берёт самую свежую сессию)
#                 ./process.sh webinar_20260625_1600  (конкретная папка в out/)
#                 ./process.sh /path/to/audio.m4a      (готовый аудиофайл без удаления оригинала)
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE"

export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/pycache}"
exec python3 -m recorder.cli process "$@"
