#!/bin/bash
# Безопасная проверка и подготовка зависимостей для webinar-recorder (macOS).
# Запуск по умолчанию: ./setup.sh           (безопасный аудит окружения)
# Установка:            ./setup.sh --install (локальная установка компонентов распознавания)
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE"

MODE="${1:---check}"

if ! command -v python3 >/dev/null 2>&1; then
    echo "=== webinar-recorder: проверка готовности окружения ==="
    echo "Каталог проекта: $BASE"
    echo
    echo "  Python 3: ❌ не найден (установите Python 3: https://www.python.org)"
    exit 1
fi

if [ "$MODE" = "--install" ]; then
    echo "=== webinar-recorder: локальная установка компонентов распознавания ==="
    echo "Каталог проекта: $BASE"
    echo
    python3 -c "
import sys
import time
from recorder.installer import INSTALLER

print('Запуск фоновой установки компонентов...')
status = INSTALLER.start_install(force=False)
print('Статус:', status.get('stage'), status.get('message'))

while INSTALLER.is_running():
    st = INSTALLER.get_status()
    pct = st.get('progress_percent', 0.0)
    msg = st.get('message', '')
    print(f'  [{pct}%] {msg}', end='\r', flush=True)
    time.sleep(1.0)

print()
final_st = INSTALLER.get_status()
if final_st.get('status') == 'completed':
    print('✅ Установка успешно завершена!')
    sys.exit(0)
else:
    print('❌ Ошибка установки:', final_st.get('error'))
    sys.exit(1)
"
    echo
    echo "ℹ️  Примечание по BlackHole:"
    echo "   BlackHole не устанавливается автоматически, так как требует прав администратора."
    echo "   Он необходим ТОЛЬКО для захвата системного звука с динамиков/Zoom."
    echo "   Для установки выполните вручную: brew install --cask blackhole-2ch"
    echo
    echo "✅ Подготовка завершена. Запустите веб-интерфейс: ./Start.command или python3 ui.py"
    exit 0
fi

# Аудит окружения через единую логику recorder
python3 -c "
import os
import sys
from recorder.config import get_effective_settings
from recorder.media_tools import get_media_tools_status
from recorder.device import list_audio_devices

eff = get_effective_settings()
tools = get_media_tools_status()
devices, raw_stderr = list_audio_devices()

print('=== webinar-recorder: проверка готовности окружения ===')
print(f'Каталог проекта: {sys.path[0] or os.getcwd()}')
print()

# 1. Python 3
print(f'  Python 3:         ✅ найден ({sys.version.split()[0]})')

# 2. FFmpeg / FFprobe
if tools.ffmpeg and tools.ffprobe:
    src = 'локальный runtime' if 'work/' in (tools.ffmpeg_path or '') else 'в системе'
    print(f'  FFmpeg / FFprobe: ✅ найден ({src}: {tools.ffmpeg_path})')
else:
    print('  FFmpeg / FFprobe: ❌ не найден (требуется для аудио/видео; установите через brew install ffmpeg)')

# 3. Whisper CLI
if tools.whisper:
    w_src = eff.get('overrides', {}).get('whisper_bin', {}).get('source', '')
    src_label = f' ({w_src})' if w_src else ''
    print(f'  Whisper CLI:      ✅ найден ({tools.whisper_version}{src_label})')
    print(f'                    Путь: {tools.whisper_path}')
else:
    err = f': {tools.whisper_error}' if tools.whisper_error else ''
    print(f'  Whisper CLI:      ⚠ не найден или не готов{err}')
    print('                    Будет собран локально через ./setup.sh --install или в интерфейсе')

# 4. Модель whisper
m_stat = eff.get('model_status', {})
if tools.model:
    sha_short = (tools.model_sha256[:8] + '...') if tools.model_sha256 else ''
    print(f'  Модель whisper:   ✅ проверена ({m_stat.get(\"size_str\", \"\")} large-v3-turbo, SHA-256: {sha_short})')
    print(f'                    Путь: {tools.model_path}')
else:
    m_err = f': {m_stat.get(\"error\")}' if m_stat.get('error') else ''
    print(f'  Модель whisper:   ⚠ не найдена или не готова{m_err}')
    print('                    Будет загружена через ./setup.sh --install или в интерфейсе')

# 5. Устройства ввода звука (Микрофон и BlackHole)
mics = [d for d in devices if d.get('kind') != 'blackhole']
blackholes = [d for d in devices if d.get('kind') == 'blackhole']

if mics:
    m_names = ', '.join(d['name'] for d in mics)
    print(f'  Микрофон:         ℹ️  Найден ({m_names}), требуется проверка звука')
else:
    print('  Микрофон:         ❌ не найден (проверьте подключение и разрешения в Системных настройках macOS)')

if blackholes:
    bh_names = ', '.join(d['name'] for d in blackholes)
    print(f'  BlackHole:        ℹ️  Найден ({bh_names}), требуется проверка звука (маршрутизация)')
else:
    print('  BlackHole:        ℹ️  не установлен (не требуется для микрофона и файлов; нужен только для Zoom/вебинаров)')

# Сценарии готовности
has_py = True
has_ff = tools.ffmpeg and tools.ffprobe
has_w = tools.whisper and tools.model and has_ff
has_mic = len(mics) > 0
has_bh = len(blackholes) > 0

w_scenario_msg = '✅ Готов' if has_w else ('❌ Требуется FFmpeg' if not has_ff else '⚠ Требуется whisper-cli и модель')

print()
print('--- Готовность сценариев работы ---')
print('  1. Импорт готовых аудио/видео файлов: ', '✅ Готов' if (has_py and has_ff) else '❌ Требуется FFmpeg')
print('  2. Запись с микрофона:                 ', 'ℹ️  Найден, требуется проверка звука' if (has_py and has_ff and has_mic) else ('❌ Требуется FFmpeg' if not has_ff else '❌ Требуется микрофон'))
print('  3. Запись звука из Zoom / браузера:    ', 'ℹ️  Найден, требуется проверка звука' if (has_py and has_ff and has_bh) else ('⚠ Требуется BlackHole 2ch' if has_ff else '❌ Требуется FFmpeg'))
print('  4. Распознавание (Whisper):            ', w_scenario_msg)
print()

if not (tools.whisper and tools.model):
    print('ℹ️  Для локальной установки компонентов whisper выполните: ./setup.sh --install')
    print('   Или нажмите кнопку «⚙️ Настройки» в веб-интерфейсе.')
elif not has_ff:
    print('ℹ️  Для нормализации звука перед распознаванием установите FFmpeg: brew install ffmpeg')
if not has_bh:
    print('ℹ️  Для захвата системного звука (вебинары) установите BlackHole вручную:')
    print('     brew install --cask blackhole-2ch')
"
