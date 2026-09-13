# RELEASE_PUBLICATION_HANDOFF (v1.3.1)

## Release Metadata
- **Release Title:** Release v1.3.1
- **Exact Tag:** v1.3.1
- **Target Commit:** This commit (to be merged into `main`).

## GitHub Release Preparation
**Public URL for Operator:**
[Prepare Release on GitHub](https://github.com/Big888Boss/rech-v-tekst/releases/new?tag=v1.3.1)

> **BOUNDARY STOP:** Do not click the final "Publish release" button until you have explicitly confirmed readiness. The Antigravity agent cannot bypass this final confirmation step.

## Release Notes (Russian)
```markdown
Версия 1.3.1:
- Добавлена загрузка файлов (file upload).
- Добавлен чек-лист микрофона/BlackHole при первом запуске.
- Встроена офлайн-документация пользователя.
- Добавлены всплывающие подсказки кнопок.
- Внедрен процесс создания отчетов об ошибках (bug-report workflow).
- Полная конфиденциальность локальных данных (local-data privacy).
- Системные требования macOS и инструкция по установке.
- **Внимание (Gatekeeper):** Приложение подписано локально (ad-hoc) и не прошло нотаризацию Apple. При первом запуске потребуется подтверждение в настройках безопасности macOS.
- Интегрированы тесты пользовательского интерфейса, контрольные суммы (SHA256) проверены. Известные границы нативного тестирования соблюдены.
```

## Release Artifacts to Upload
1. **Application Archive**
   - **Path:** `/Users/kuznetcovpavel/max/rech-v-tekst-speaker-identity-v1_3-20260911/dist/Rech-v-tekst-v1.3.1-macOS.zip`
   - **Size:** 6900052 bytes
2. **Checksum File**
   - **Path:** `/Users/kuznetcovpavel/max/rech-v-tekst-speaker-identity-v1_3-20260911/dist/Rech-v-tekst-v1.3.1-macOS.zip.sha256`
   - **Digest:** `1b964819f0134feb870e59a540bb0b07c1e78f7f29e603ed80fe3659e3873a26`

## Failure & Rollback Plan
If native use fails, execute the following non-destructive rollback:
```bash
mv "/Applications/Речь в текст.app" "/Applications/Речь в текст.candidate-failed-$(date +%s).app"
mv "/Applications/Речь в текст.v1.3.0.backup.app" "/Applications/Речь в текст.app"
```
