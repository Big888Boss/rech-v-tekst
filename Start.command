#!/bin/bash
# Двойной клик в macOS Finder запускает приложение в веб-браузере.
set -euo pipefail

BASE="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE"

echo "============================================================"
echo "               Webinar Recorder (macOS)"
echo "============================================================"
echo "Рабочий каталог: $BASE"
echo

# 1. Проверка наличия Python 3
if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ Ошибка: Python 3 не найден в системе."
    echo "   Пожалуйста, установите официальный Python 3: https://www.python.org/downloads/macos/"
    echo
    read -p "Нажмите Enter для выхода..." || true
    exit 1
fi

# 2. Канонический аудит готовности компонентов (setup.sh --check)
if [ -x "./setup.sh" ]; then
    ./setup.sh --check
else
    echo "⚠ Предупреждение: ./setup.sh не найден, пропускаю аудит окружения."
fi
echo

PORT="${UI_PORT:-8787}"
SERVER_URL="http://127.0.0.1:${PORT}"

# 3. Проверка доступности порта (занятый порт без проверки identity не считать своим)
if ! python3 -c "
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.bind(('127.0.0.1', int('$PORT')))
    s.close()
    sys.exit(0)
except OSError:
    sys.exit(1)
"; then
    echo "❌ Ошибка: Порт ${PORT} уже занят другим процессом."
    echo "   Если сервер уже запущен, откройте интерфейс в браузере: ${SERVER_URL}"
    echo "   Либо запустите на другом порту через UI_PORT, например:"
    echo "     UI_PORT=8790 ./Start.command"
    echo
    exit 1
fi

echo "==> Запуск веб-интерфейса: ${SERVER_URL}"
echo "    (Для остановки закройте это окно терминала или нажмите Ctrl+C)"
echo

# 4. Фоновое ожидание готовности сервера и автоматическое открытие браузера
(
    for i in $(seq 1 30); do
        if curl -s -f -m 1 "${SERVER_URL}/api/status" >/dev/null 2>&1; then
            open "${SERVER_URL}" 2>/dev/null || true
            break
        fi
        sleep 0.5
    done
) &

# 5. Запуск веб-сервера
export UI_PORT="$PORT"
exec python3 ui.py
