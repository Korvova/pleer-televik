Как программа разговаривает с центральным блоком Televic Plixus. Выяснено 17.11.2026 по коду
веб-панели Plixus (`js/min/main.min.js`) и проверено запросами к живому блоку 10.0.20.32.

## Подключение

* WebSocket `ws://<адрес>:6111`, подпротокол `unicos-v2` — это модуль ERS (записи, дискуссия, места).
  Пароля нет, адрес страницы (Origin) не проверяется. Вход в веб-панель — отдельная история, к API не относится.
* Остальные порты веб-панели: 6012 (CU, бинарный TCCP), 6212 (ECS), 6312 (камеры) — нам не нужны.
* Сообщения — JSON `{"module": "...", "name": "...", "data": ...}`.
* Сначала подписка, иначе движок молчит:
  `connection / subscribe_modules {"client_id": "player", "modules": [{"name": "recording", "version": 0}, …]}`.
* Раз в 10 с — `connection / lfc` (проверка связи, как у веб-панели).

## Записи (`module: recording`)

| Запрос | Ответ | Что это |
|---|---|---|
| `get_files "internal"` (или `"external"`) | `files {"storage", "files": [{"name": "/audio/internal/…mp3"}]}` | список файлов |
| `get_settings` | `settings {storage, bitrate, autodelete, auto_split, auto_split_time, prefix, suffix, channels}` | настройки записи |
| `set_settings {…весь объект…}` | `settings` или `record_error ["kErrorIllegalPrefix"]` | изменить настройки |
| `get_current_state` | `current_state "idle" / "active" / "paused"` | состояние |
| `set_current_state "active" / "idle" / "paused"` | `current_state …` | старт, стоп, пауза |

* Файл скачивается обычным GET: `http://<адрес>/audio/internal/<имя>`, тип `application/octet-stream`.
  Заголовок `Range` блок игнорирует (всегда отдаёт файл целиком).
* Имя файла: `[<префикс>_]ГГГГ-ММ-ДД_ЧЧhММmССs_<канал>.mp3`, канал — `Floor`, `01-RUS`, `02-ENG`…
  Время старта одинаковое у всех каналов одной записи. При `auto_split` новый файл каждые `auto_split_time` секунд.
* Префикс: латиница, цифры, `-`, `_`. Кириллица → `kErrorIllegalPrefix`.
* Часы блока спешат примерно на 4 минуты относительно ПК (17.11.2026).

## Каналы (`module: interpretation`)

`get_conference_audio_channels` → `[{"sysch": 0, "disp_txt": "Floor"}, {"sysch": 3, "disp_txt": "01 RUS"}, …]`.
В настройках записи `channels` — номера `sysch`. Сейчас пишутся 0, 3, 4 (Floor, RUS, ENG); по лицензии до 4 каналов.

## Микрофоны (`module: discussion`, `module: seat`)

* `discussion / get_all_audio_input_states` → `all_audio_input_states [{"seat_id": 1, "state": "off"}, …]`.
* Событие при нажатии микрофона: `audio_input_state {"seat_id", "state"}`; состояния `on`, `off`, `request`,
  `requeston`, `reply`, `replyrequest`. Метка = отрезок, пока состояние `on`.
* `seat / get_seats` → места с `seat_id`, `label` (подписи сейчас пустые), `state` (`normal` / `broken`).
  На 17.11 места 3 и 4 — `broken`.
