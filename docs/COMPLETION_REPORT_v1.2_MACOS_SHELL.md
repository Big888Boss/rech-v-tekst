# COMPLETION_REPORT_v1.2_MACOS_SHELL

## Общая информация
- **Executor:** Antigravity
- **Primary Model Selected:** Gemini 3.1 Pro
- **Base Commit SHA:** `dd077016df497885ad3f0f67b562efdb637dc37c`
- **Previous SHA:** `e3b37ab`
- **Current HEAD SHA:** См. `git log` (7-й финальный коммит).
- **Codex Implementation Status:** PROHIBITED
- **Final Status:** READY FOR ACCEPTANCE

## Делегированный скоуп (Исправления по P0, итерация 6: Packaged Runtime)
- **Изоляция данных от Bundle:** В режиме PyInstaller (`sys.frozen`) введены раздельные константы путей. Читаемые ресурсы бандла (`STATIC_DIR`) ссылаются на `Contents/Resources`. Записываемые данные (`OUT_DIR`, `MODELS_DIR`, `WORK_BIN_DIR`) ссылаются на `~/Library/Application Support/rech-v-tekst/` или переопределяются через переменные окружения, предотвращая изменение подписи `.app` и PermissionErrors.
- **Исправление Symlink Security:** Встроенная проверка безопасности на симлинки (`verify_path_components_safe`) отключена для проверки `STATIC_DIR`, так как PyInstaller легитимно использует симлинки в сборке macOS, при этом пользовательские данные всё ещё надежно защищены от симлинк-атак.
- **Packaged Smoke Test:** Написан и запущен скрипт `packaged_smoke_test.py`, который скомпилировал приложение через `./build.sh` и доказал отдачу файлов через `http` изнутри скомпилированного бандла (без конфликтов `safe_read_file`) и чистый выход по SIGINT.

## Статистика и артефакты
- **Размер приложения:** ~15 MB (`dist/Речь в текст.app`)
- **Запускатели:** Основной — `Start-App.command`.
- Контрольная сумма `dist/checksum.txt` пересобрана.

## Тестирование и Evidence
- Добавлены тесты проверки путей `test_frozen_paths.py` (Flat Bundle / macOS Bundle).
- **Packaged Smoke Test** (`packaged_smoke_test.py`) успешно запросил `/`, `/static/loading.html` и `/api/preflight` на собранном приложении и получил `HTTP 200 OK`. Доказано, что `OUT_DIR` создается снаружи бандла.
- **Evidence (Скриншоты и BlackHole):** Отмечено как `NOT VERIFIED` для среды Root runtime.

Завершено: создан отдельный седьмой коммит без amend.
