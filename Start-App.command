#!/bin/bash
# Запускает собранное macOS приложение "Речь в текст.app"
set -euo pipefail

BASE="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE"

APP_PATH="$BASE/dist/Речь в текст.app"

echo "============================================================"
echo "               Webinar Recorder (Native macOS)"
echo "============================================================"

if [ -d "$APP_PATH" ]; then
    echo "Запуск приложения..."
    open "$APP_PATH"
else
    echo "❌ Ошибка: Собранное приложение не найдено по пути:"
    echo "   $APP_PATH"
    echo "Сначала выполните ./build.sh для сборки."
    read -p "Нажмите Enter для выхода..." || true
    exit 1
fi
