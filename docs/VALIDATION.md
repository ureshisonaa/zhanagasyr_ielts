# Результаты проверки

- 39 Django-тестов прошли на SQLite: авторизация и CSRF; изоляция учеников/преподавателей; отзыв сессий и ссылок; версии; сроки и лимиты; конфликт автосохранения; scoring; аудиофайлы и HTTP Range; публикация/экспорт; полный цикл сдачи и ручной проверки.
- Браузерный smoke-тест прошёл в Chromium: вход; desktop/mobile без горизонтального переполнения; Listening; сохранение и перезагрузка без сброса времени; offline/reconnect; Writing; запись трёх частей Speaking с имитированным микрофоном; проверка преподавателем; публикация администратором; результат ученику. Ошибок JavaScript после исправления — 0.
- Backup SQLite восстановлен в отдельную папку; `PRAGMA integrity_check` вернул `ok`; все ожидаемые приватные файлы найдены.
- `manage.py check --deploy` прошёл с production-переменными.
- Gunicorn с DEBUG=0 вернул 200 для health, login и static через WhiteNoise.
- `docker compose config --quiet` прошёл. Реальный запуск контейнерного стека и PostgreSQL в этой среде не выполнен.
- Production в Vercel пока не развёрнут. Его инфраструктурные требования перечислены в `VERCEL.md`.

Файлы скриншотов и демонстрационные данные находятся в игнорируемой папке `var/`; реальные пароли и сессионные данные в Git не добавляются. Проверка на реальных Safari/iOS/Android, нагрузочное тестирование и калибровка полного банка IELTS ещё необходимы перед учебным запуском.

## Cambridge 21 import and exam workspace (2026-10-17)

- Imported the owner's PDF and four Listening recordings into private storage, outside Git: 40 Listening questions, 40 Reading questions, two Writing tasks including the original chart, and three Speaking parts.
- Answer keys checked against supplied pages 117–118. Unordered Listening pairs consume each correct answer only once; all-correct fixtures score 40/40.
- Source-page access is limited to the current assigned section; unrelated students cannot retrieve the pages. Cloning remaps source asset references.
- Exam-only stylesheet and controls: part tabs, numbered navigation, review flags, focus, text size, local notes, temporary highlighting, print/PDF export, native replay controls and playback speed when replay is allowed. The existing deadline and autosave rules remain in force.
- Desktop/mobile browser check verified part switching, answers and notes surviving reload, and no horizontal overflow or JavaScript errors.
- Source documents are shown alongside answer fields. This is an adaptation of the supplied Engnovate reference, not a pixel-identical copy or its paid section-practice service.

## Interactive text revision (2026-10-18)

- Version 2 replaces page scans with escaped text, an inline Listening table and notes, complete Reading passages, and a graph-only Writing image. Original attempts retain version 1 for historical scoring.
- Listening is 2,400 seconds (40 minutes). Updated default for newly created tests; each existing immutable version keeps its own duration.
- Multiple choice shows full option labels. Two-answer questions use a two-selection checkbox group backed by the original two answer IDs and unordered-pair scoring.
- Matching supports dragging a bank option, moving an existing answer, clicking a token and target, and a native select for keyboard/touch input. All routes update the same autosave fields.
- Presentation validation requires each question reference exactly once. Template escaping remains active; answer keys are excluded from student context.
- 44 Django tests pass. Browser checks cover timer duration, inline inputs, radios, checkbox limits, dragging/moving/clicking/selecting, reloading saved answers, passage switching, desktop/mobile width, and absence of scanned-page elements.
