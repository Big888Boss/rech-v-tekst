# ADR-001: Локальная диаризация для долгих звонков (v1.1.0)

- **Статус**: Accepted by Codex (2026-09-09)
- **Дата**: 2026-09-09
- **Исполнитель**: Antigravity
- **Роль Codex**: Архитектурный надзор и независимая приёмка
- **Базовый коммит**: `Big888Boss/rech-v-tekst` @ `dfd8f597f6b62273234675b983011cae6bc96fca` (v1.0)
- **Целевая ветка**: `feature/long-calls-diarization-v1.1`

---

## 1. Контекст и цели

Релиз Webinar Recorder v1.0 обеспечивает надёжную локальную расшифровку аудио (Whisper via Metal/CPU) для длинных записей.
В релизе v1.1.0 требуется добавить возможность **автономного разделения речи по голосам (Speaker Diarization)** для моно-записей звонков и вебинаров длительностью **до 8+ часов**.

### Ключевые требования:
1. **100% Offline и приватность**: никаких облачных запросов, токенов доступа Hugging Face, передачи звука/текста во внешние сервисы или скрытой телеметрии.
2. **Аппаратная поддержка**: локальная работа на macOS (Apple Silicon arm64 и Intel x86_64) без обязательного наличия дискретного GPU или PyTorch.
3. **Длинные записи (≥8 часов)**:
   - Предсказуемое потребление памяти: аудиобуфер и рантайм нейросетей строго ограничены размером скользящего окна ($O(W)$), метаданные реплик растут как $O(N)$ и пренебрежимо малы (< 1 МБ на 8 часов).
   - Оконная обработка с перекрытием (Windowed Diarization with Overlap): при окне 600 с и перекрытии 60 с (шаг 540 с) 8-часовой звонок обрабатывается ровно за **54 окна**.
   - Глобальное согласование идентификаторов говорящих (`speaker_01`, `speaker_02`, ...) сквозь границы окон и 120-секундных чанков с использованием реестра акустических центроидов (Acoustic Embedding Centroid Registry) через проверенный C-API `libsherpa-onnx-c-api.dylib`.
   - Атомарные контрольные точки (checkpoints) с сохранением центроидов и хэшей входных чанков для возобновления обработки при рестарте.
   - Независимость от Whisper: отмена/повтор диаризации не затрагивает уже готовый текст расшифровки.
4. **Безопасность исходных данных**: исходные нормализованные аудиофайлы неизменяемы.
5. **Безопасность путей и процессов**: использование существующего в `recorder.storage` метода `is_safe_regular_file` (`os.lstat`, `stat.S_ISREG`, `not stat.S_ISLNK`, `st.st_nlink == 1`, `os.O_NOFOLLOW`), запуск процессов строго списком (`shell=False`, `start_new_session=True`).
6. **Обработка сложных случаев**: тишина, короткие реплики, наложение голосов (`overlap`), явная индикация неопределённости (`speaker_unknown`).
7. **Ручное переименование**: присвоение человекочитаемых имён говорящим без повторного запуска нейросетей с быстрым пересозданием всех экспортов (TXT, MD, SRT, VTT, JSON).
8. **Граница продукта v1.1.0**: разделение «кто когда говорил» (диаризация) и ручные имена. Автоматическое биометрическое распознавание реальных персон по голосовым профилям (voiceprints) не входит в v1.1.0.

---

## 2. Сравнительный анализ и выбор движка

### 2.1. Оценка вариантов

| Критерий | `sherpa-onnx` (Выбран) | `pyannote.audio community-1` | `whisper.cpp diarize` |
| :--- | :--- | :--- | :--- |
| **Автономия / Токены** | **100% offline, без аккаунтов и токенов** | Требует аккаунт HF, принятие условий использования (Terms of Use / Gated Access) и токен | 100% offline |
| **Зависимости** | **Автономная C-библиотека / статический CLI (macOS Mach-O)** | Тяжёлый стек PyTorch, torchaudio, huggingface_hub (~2-4 ГБ) | Встроен в whisper.cpp |
| **Mono Diarization** | **Полноценная (сегментация + эмбеддинги + кластеризация)** | Полноценная | **Непригоден** (работает только по балансу стерео-каналов) |
| **Потребление RAM (RSS)** | **Измерено: 250 МБ (1 мин), 290 МБ (5 мин), 324 МБ (10 мин)** | 2 000 – 4 000 МБ | ~500 МБ |
| **Скорость на CPU (RTF)** | **~0.18–0.20** (>5x быстрее реального времени) | ~0.8–1.5 (медленно на CPU) | Быстро, но только стерео |
| **Лицензия компонентов** | **Apache 2.0 / MIT** | Ограничительные условия доступа HF | MIT |

### 2.2. Принятое решение: `sherpa-onnx` (C-API + CLI)

Выбран **`sherpa-onnx`** версии **`1.13.7`**:
1. **Исполняемый модуль и библиотека**:
   - `sherpa-onnx-offline-speaker-diarization`: для сквозной диаризации окон.
   - `libsherpa-onnx-c-api.dylib`: компактная C-библиотека (8.7 МБ, Apache 2.0), вызываемая из стандартного Python через `ctypes` для извлечения 512-мерных акустических эмбеддингов (`SherpaOnnxCreateSpeakerEmbeddingExtractor`). Линкуется исключительно с системными `libSystem.B.dylib`, `libc++.1.dylib` и `Foundation.framework`.
2. **Модель сегментации**: `sherpa-onnx-pyannote-segmentation-3-0` (`model.int8.onnx`, 1.5 МБ, MIT License CNRS 2022).
3. **Модель эмбеддингов**: `3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx` (39.5 МБ, Apache 2.0 Alibaba DAMO Academy).

---

## 3. Закреплённые компоненты, URL, лицензии и контрольные суммы (SHA-256)

### 3.1. Бинарные дистрибутивы

1. **macOS Apple Silicon (arm64)**:
   - **URL архива static binary**: `https://github.com/k2-fsa/sherpa-onnx/releases/download/v1.13.7/sherpa-onnx-v1.13.7-osx-arm64-static.tar.bz2`
   - **SHA-256 архива static**: `4392c74b9d6138d15219d1113b4a54ef7edb2eb34b710d3a2ef0ffb7b5910b1a`
   - **SHA-256 бинарника `sherpa-onnx-offline-speaker-diarization`**: `7cba57f66d1b039c886716778345b1ab2802cd144beddbaffe7590b5768e0e43`
   - **URL архива shared-lib (C-API)**: `https://github.com/k2-fsa/sherpa-onnx/releases/download/v1.13.7/sherpa-onnx-v1.13.7-osx-arm64-shared-lib.tar.bz2`
   - **SHA-256 архива shared-lib**: `ba42ba552e690f0ca9b6264858dd98e2195df4a7eb0feee56c2d1b74bb26b010`
   - **Лицензия**: Apache License 2.0.

2. **macOS Intel (x86_64)**:
   - **Статус**: `BLOCKED (pending verified official asset & checksum verification before x64 distribution)`.
   - Все загрузки в installer работают по схеме fail-closed: если хэш не совпадает или архитектура не имеет проверенного pinned SHA-256, загрузка и установка блокируются с понятным сообщением об ошибке.

### 3.2. Модели (подтверждённые официальными архивами)

1. **Pyannote ONNX Segmentation 3.0**:
   - **URL архива**: `https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2`
   - **SHA-256 архива**: `24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488`
   - **Файл модели**: `model.int8.onnx` (1,586,839 байт)
   - **SHA-256 модели**: `d582f4b4c6b48205de7e0643c57df0df5615a3c176189be3fc461e9d18827b5d`
   - **Лицензия**: MIT License (Copyright (c) 2022 CNRS, подтверждена в файле `LICENSE` официального архива).

2. **3D-Speaker ERes2Net Base Embedding**:
   - **URL**: `https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx`
   - **Размер**: 39,593,761 байт
   - **SHA-256**: `1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b` (подтверждён официальным файлом `checksum.txt` в upstream-релизе `speaker-recongition-models`).
   - **Лицензия**: Apache License 2.0 (Alibaba DAMO Academy).

### 3.3. Публичный тестовый фикстур (Multi-Speaker Ground Truth)
- **Файл**: `0-four-speakers-zh.wav` (16 kHz, mono, 56.861 сек, 4 говорящих).
- **URL**: `https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/0-four-speakers-zh.wav`
- **SHA-256**: `bedf036caed208386c67b4ef4b11f83d74dd0d420b102163a1c33cd09cde7010`
- Для тестов стыковки 120-секундных чанков фикстур компонуется/зацикливается скриптом сборки тестов без использования синтетических искусственных тонов.

---

## 4. Спецификация формата данных (`schema_version: 2`)

### 4.1. Канонический `transcript.json`

- Поле `confidence` строго `null`, так как sherpa CLI не предоставляет покадровой вероятности; вымышленные значения исключены.
- В сегментах сохраняется только `speaker_id`. Имя `display_name` резолвится динамически из `diarization.speakers` при UI-рендеринге и экспорте, что исключает рассинхронизацию при переименовании.

```json
{
  "schema_version": 2,
  "session_id": "upload_20260909_013921_66d1d94ecf38",
  "title": "Звонок команды",
  "language": "ru",
  "total_duration_sec": 14400.0,
  "diarization": {
    "status": "completed",
    "backend": "sherpa-onnx",
    "model_version": "pyannote-3.0-int8+eres2net",
    "params": {
      "num_speakers": null,
      "cluster_threshold": 0.85,
      "window_sec": 600,
      "overlap_sec": 60
    },
    "speakers": {
      "speaker_01": {
        "display_name": "Говорящий 1",
        "color_index": 0
      },
      "speaker_02": {
        "display_name": "Говорящий 2",
        "color_index": 1
      },
      "speaker_unknown": {
        "display_name": "Неизвестный",
        "color_index": -1
      }
    },
    "turns": [
      {
        "from_sec": 0.318,
        "to_sec": 6.848,
        "speaker_id": "speaker_01",
        "confidence": null,
        "overlap": false
      },
      {
        "from_sec": 7.017,
        "to_sec": 10.679,
        "speaker_id": "speaker_02",
        "confidence": null,
        "overlap": false
      }
    ]
  },
  "segments": [
    {
      "id": 1,
      "from_sec": 0.5,
      "to_sec": 6.7,
      "text": "Коллеги, добрый день, начинаем встречу.",
      "speaker_id": "speaker_01",
      "overlap": false
    },
    {
      "id": 2,
      "from_sec": 7.1,
      "to_sec": 10.5,
      "text": "Да, всем привет.",
      "speaker_id": "speaker_02",
      "overlap": false
    }
  ]
}
```

### 4.2. Обратная совместимость с v1.0
- Файлы v1.0 без `schema_version` автоматически парсятся как `schema_version: 1`, `diarization: null`.
- Никаких скрытых или принудительных перезаписей существующих файлов сессий не производится.

---

## 5. Архитектура для длинных звонков (≥8 часов)

### 5.1. Эмпирические замеры потребления памяти и оконная нарезка
Замеры с помощью `/usr/bin/time -l` на реальном Apple Silicon CPU:
* **56.9 с (1 минута)**: пиковый RSS = `262 848 512` байт (**~250 МБ**).
* **300.0 с (5 минут)**: пиковый RSS = `304 005 120` байт (**~290 МБ**).
* **600.0 с (10 минут, рабочее окно)**: пиковый RSS = `340 475 904` байт (**~324 МБ**).

Параметры окна:
- Длина окна $W = 600$ с (10 минут = ровно 5 нормализованных чанков по 120 с).
- Перекрытие $O = 60$ с (половина 120-секундного чанка).
- Сдвиг $S = 540$ с.
- Количество окон для 8 часов (28 800 с):
  $$N_{windows} = 1 + \lceil (28800 - 600) / 540 \rceil = 1 + 53 = 54\text{ окна}.$$
- **Потребление памяти**: аудиобуфер и нейросетевой рантайм строго ограничены размером окна ($O(W) \approx 324$ МБ RSS), а метаданные реплик и контрольные точки в памяти растут как $O(N)$ и пренебрежимо малы (< 1 МБ на 8 часов).

### 5.2. Реестр акустических центроидов (Acoustic Embedding Centroid Registry)

Доказан рабочий путь извлечения эмбеддингов: C-API функция `SherpaOnnxSpeakerEmbeddingExtractorComputeEmbedding` через `ctypes` вычисляет 512-мерный вектор эмбеддинга для любого аудио-сегмента за миллисекунды.

#### Калибровка порогов на официальном multi-speaker фикстуре:
* **Один и тот же говорящий** (Speaker 0 в разные моменты 1.0с, 22.5с, 52.8с):
  косинусное сходство составляет **0.560 ... 0.709** (среднее **~0.625**).
* **Разные говорящие** (Speaker 0 vs 1, 2, 3):
  косинусное сходство составляет **-0.052 ... 0.242** (среднее **~0.084**).
* **Запас разделения**: между разными и одинаковыми говорящими составляет **более 0.30**.

#### Алгоритм согласования:
1. Для каждого глобального говорящего хранится нормализованный центроид $C(S_i) \in \mathbb{R}^{512}$ ($\|C(S_i)\| = 1$) и суммарная длительность его речи $T_{total}(S_i)$.
2. В каждом окне $W_k$ для каждого локального кластера $c_j$ извлекается усреднённый эмбеддинг $e(c_j)$.
3. Вычисляется матрица сходства $M_{ij} = C(S_i) \cdot e(c_j)$.
4. **Пороговые значения (Provisional, вынесены в `DiarizationConfig`)**:
   - $\theta_{match} = 0.52$: при $\max_i M_{ij} \ge \theta_{match}$ кластер надёжно сопоставляется глобальному спикеру $S_{i^*}$.
   - Проверка неоднозначности (Ambiguity): если второй лучший кандидат $M_{i'j}$ близок ($\Delta < 0.08$), в качестве арбитра используется temporal overlap в зоне $O$.
   - $\theta_{new} = 0.35$: если для всех $i$ сходство $M_{ij} < \theta_{new}$, кластер гарантированно является **новым говорящим** $S_{new}$ (`speaker_03`, `speaker_04`...).
   - Неопределённая зона $[0.35, 0.52]$: при наличии подтверждающего temporal overlap в зоне $O$ кластер сопоставляется; без подтверждения — помечается как `speaker_unknown`.
5. **Обновление центроида (Moving Average)**:
   $$C_{new}(S_{i^*}) = \text{normalize}\left(\alpha C_{old}(S_{i^*}) + (1 - \alpha) e(c_j)\right)$$
   где $\alpha = 0.8$, предотвращая дрейф центроида при адаптации к акустике.

### 5.3. Атомарные контрольные точки (Checkpoints)
Файл `out/<session>/diarization_checkpoint.json` записывается атомарно (запись во временный файл + rename) после каждого завершённого окна.
**Обязательный состав Checkpoint**:
1. `schema_version`: 2
2. `last_processed_window`: индекс и конечная секунда.
3. `chunk_fingerprints`: словарь `{ "chunk_000.wav": "sha256:...", ... }` с SHA-256 каждого входного нормализованного файла. При изменении входных файлов checkpoint признаётся невалидным.
4. `model_fingerprints`: SHA-256 используемых бинарника и моделей.
5. `global_centroids`: сериализованные векторы центроидов каждого известного говорящего.
6. `turns`: накопленный и отсортированный список глобальных реплик.

При рестарте сервера уже завершённые окна не вычисляются повторно. При отмене диаризации готовый транскрипт Whisper остаётся валидным и доступным.

---

## 6. Алгоритм объединения с текстом (Merge Strategy)

1. Для каждого сегмента Whisper $S = [t_{start}, t_{end}]$:
   - Рассчитывается временное пересечение со всеми $T_i \in \text{turns}$.
   - Основной говорящий: $speaker(S) = \arg\max_i \Delta t_i$.
   - Если $\Delta t_{max} < 0.2 \times \text{duration}(S)$ или сегмент в паузе: $speaker(S) = \text{speaker\_unknown}$.
   - Если в пределах $S$ говорят $\ge 2$ разных людей с длительностью $> 0.3$ с у каждого: выставляется флаг `overlap: true`.
2. Интерфейс отображает понятный индикатор `[Наложение речи]`.

---

## 7. Ручное переименование и экспорт

- Переименование отправляет `POST /api/session/speakers` с JSON `{"session_id": "...", "speakers": {"speaker_01": "Иван"}}`.
- Сервер атомарно обновляет маппинг `display_name` в `session.json` и `transcript.json`.
- Модуль `recorder/export.py` быстро пересоздаёт 5 файлов без вызова нейросетей:
  - **`transcript.txt`**: блоки с именами перед репликами.
  - **`transcript.md`**: заголовки `### [HH:MM:SS] Имя`.
  - **`transcript.srt`**: субтитры с префиксом `Имя: Текст`.
  - **`transcript.vtt`**: WebVTT voice tags `<v Имя>Текст</v>`.
  - **`transcript.json`**: структура с обновлёнными `display_name`.

---

## 8. Безопасность процессов и путей (Process & Filesystem Hardening)

1. **Запуск процессов**:
   - Строгий список аргументов без шелла: `subprocess.Popen(args_list, shell=False, start_new_session=True)`.
   - Запрет конкатенации командной строки в шелл.
2. **Проверка путей (Filesystem Hardening)**:
   - Обязательное использование существующей проверенной функции `is_safe_regular_file` из `recorder.storage` (`os.lstat`, `stat.S_ISREG`, `not stat.S_ISLNK`, `st.st_nlink == 1`).
   - Защита от TOCTOU / symlink-атак: открытие файлов с флагом `os.O_NOFOLLOW` через `safe_read_bytes`.
   - Расположение строго внутри разрешённых каталогов приложения (`work/bin` и `models/diarization`).
   - Проверка SHA-256 перед запуском.
3. **Остановка и очистка (Reap)**:
   - При таймауте или отмене: `os.killpg(os.getpgid(proc.pid), signal.SIGTERM)` с ограниченным дренажом пайпов и последующим `SIGKILL` при отсутствии завершения за grace period (2.0 с).

---

## 9. Соответствие реальным файлам кодовой базы

* **Ядро (`recorder/`)**:
  * `recorder/diarizer.py` [NEW] — вызов `sherpa-onnx`, sliding window, centroid registry via C-API, checkpoints.
  * `recorder/diarization_merge.py` [NEW] — привязка turns к segments, overlap, fallback.
  * `recorder/session.py` [MODIFY] — поддержка schema v2, структуры `diarization`, безопасная загрузка v1.
  * `recorder/installer.py` [MODIFY] — fail-closed загрузка и верификация sherpa-onnx и моделей.
  * `recorder/export.py` [MODIFY] — экспорт имён говорящих во все форматы, быстрый re-export.
  * `recorder/transcribe.py` [MODIFY] — опциональная координация диаризации после Whisper.
  * `recorder/http_server.py` [MODIFY] — эндпоинты `POST /api/session/diarize`, `POST /api/session/diarize/cancel`, `POST /api/session/speakers`, `POST /api/settings` с валидацией CSRF.
* **Интерфейс (`static/`)**:
  * `static/index.html` [MODIFY] — тумблер диаризации, выбор 2–20 / авто, модалка переименования, прогресс.
  * `static/app.css` [MODIFY] — цвета для 20 говорящих, индикаторы наложения речи.
  * `static/app.js` [MODIFY] — клиентская логика переименования, отображение цветов и прогресса.

---

## 10. План тестов (согласно `CODEX_DIARIZATION_PLAN_22.md`)

1. **Unit-тесты** (`tests/test_diarization_merge.py`):
   - Расчёт временного перекрытия (граничные точки, частичные наложения, тишина, вложенные реплики).
   - Определение одновременной речи (`overlap: true`).
   - Присвоение `speaker_unknown` при отсутствии значимого пересечения.
   - Сшивание окон через Centroid Registry: перестановка speaker IDs, возвращение говорящего после часа молчания, появление нового участника.
   - Обратная совместимость: чтение transcript.json v1.0.
2. **Integration-тесты** (`tests/test_diarization_pipeline.py`):
   - Прогон на реальном фикстуре `0-four-speakers-zh.wav`: проверка разделения на 4 говорящих.
   - Скомпонованный multi-speaker WAV с репликами на стыке 120-секундного чанка: проверка отсутствия разрыва меток.
3. **Recovery и отказоустойчивость** (`tests/test_diarization_recovery.py`):
   - Отмена в процессе -> подтверждение сохранности транскрипта Whisper.
   - Возобновление по готовому checkpoint -> пропуск ранее вычисленных окон.
   - Проверка невалидности checkpoint при изменении SHA-256 входных чанков.
   - Повреждённый бинарник/модель -> fail-closed обработка без падения сервера.
4. **Performance & Memory Bound** (`tests/test_diarization_long_calls.py`):
   - Виртуальный таймлайн на 8 часов (54 окна по 10 минут).
   - Проверка: аудиобуфер bounded $O(W) \approx 324$ МБ, метаданные $O(N)$, время работы стабильно, контрольные точки пишутся атомарно.
5. **UI & Regression** (`tests/test_ui_settings.py`, полный существующий suite):
   - Все 140 тестов v1.0 продолжают проходить без сбоев.
