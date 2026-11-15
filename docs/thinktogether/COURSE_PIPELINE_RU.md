# Генерация мини-курса из документа

Режим находится в кабинете эксперта, раздел контента: «Мини-курс из документа». Требуются права `content`. Поддерживаются PDF с текстовым слоем, TXT, MD, DOCX и PPTX до 10 МБ. EPUB и OCR не реализованы.

## Запуск на Windows

В `docker/thinktogether/.env` добавьте backend-переменную `DEEPSEEK_API_KEY` с действительным ключом официального DeepSeek API. Файл окружения не коммитится. API и worker читают один и тот же env_file. Ключ не передаётся браузеру.

```powershell
Set-Location 'C:\Users\Ferumm\zimran\learnhouse\.worktrees\thinktogether'
docker compose --env-file docker/thinktogether/.env -f docker/thinktogether/docker-compose.yml up -d --build api worker scheduler web gateway
```

Откройте [ThinkTogether](http://localhost:8096/thinktogether/expert), войдите как эксперт/администратор и выберите раздел контента. Если используется другой `TT_HTTP_PORT`, замените порт в адресе.

## Как работает

Существующий `/api/v1/tt/content/materials` извлекает и приватно сохраняет материал. Новый `POST /api/v1/tt/content/course-pipeline` принимает `title`, `material_id`, необязательные `audience` и `goal`, а также `idempotency_key`; возвращает HTTP 202 и ID задания. Повтор с тем же ключом и параметрами не создаёт второе задание.

Worker выполняет два вызова модели и локальную сборку, сохраняя результат каждого этапа:

1. Course Architect: `deepseek-flash`, thinking enabled, `reasoning_effort=medium`, до 24000 выходных токенов. Ответ строго проверяется: один заголовок протокола, три главы в правильном порядке, по одной схеме в каждой.
2. Diagram Architect: та же модель и настройки рассуждения, до 12000 выходных токенов. CSV читается `csv.DictReader(strict=True)`, проверяется и сохраняется до визуализации.
3. Python создаёт SVG без дополнительного LLM-вызова. Итоговый Markdown собирается только после проверки наличия всех файлов. Повтор локальной сборки использует сохранённый CSV.

На один вызов действует общий таймаут 240 секунд. Ошибки провайдера и некорректные ответы завершают генерацию явной ошибкой без автоматических платных повторов; валидный черновик первого этапа остаётся доступен. Для аварийной остановки worker и локальных I/O-ошибок сохраняется бюджет трёх попыток восстановления. Завершённые этапы не повторяются. Если процесс остановился после отправки запроса, но до сохранения ответа, текущий незавершённый вызов может повториться. После перезагрузки страницы история и статус восстанавливаются из базы.

Для источника выбирается до 12 фрагментов общим объёмом до 28000 символов, с предпочтением указанной темы и сохранением порядка в исходнике. UI показывает число использованных фрагментов и предупреждение о частичной обработке. Это ограниченный мини-курс по выбранным фрагментам, а не обещание обработки всей большой книги.

## Хранение и API

Используется существующее приватное хранилище заданий `TTJob.payload`. Миграция не требуется. SVG сначала создаются как `diagrams/<id>.svg` во временном каталоге, проверяются вместе с Markdown, затем весь пакет сохраняется одной транзакцией под логическими именами `course.draft.md`, `diagrams.csv`, `diagrams/ch01_d01.svg`–`ch03_d01.svg`, `course.final.md`. Временный каталог удаляется. Общий диск API/worker или публичный bucket не нужен.

- `GET /api/v1/tt/content/course-pipeline` — последние задания текущей организации.
- `GET /api/v1/tt/content/course-pipeline/{id}` — статус, покрытие источника, итоговый Markdown и браузерные URL схем.
- `GET /api/v1/tt/content/course-pipeline/{id}/artifacts/{name}` — приватный артефакт. SVG выдаётся с `image/svg+xml`, `nosniff`, ограничительным CSP и `Cache-Control: private, no-store`.

Все маршруты используют существующую авторизацию ThinkTogether и проверку прав `content`. Другие организации не видят задания или схемы. Файловые пути не принимаются от клиента и не возвращаются как URL. Курс доступен для просмотра и скачивания экспертом; автоматическая публикация в учебную программу не выполняется.

Промпты сохранены без переписывания в backend-ресурсах `course_architect.txt` и `diagram_architect.txt`. Голосовой агент, collab и действующий генератор PMP используют прежние настройки.

## Проверки

Проверено 15 ноября 2026: 47 backend-тестов, 12 frontend-тестов, TypeScript (`tsc --noEmit`) и целевой ESLint прошли. Презентация пользователя читается: 11 слайдов с текстом, 4695 символов, 11 фрагментов. Независимая проверка кода завершена, замечания исправлены. В работающем локальном API отсутствует `DEEPSEEK_API_KEY`; реальный платный вызов не выполнялся. Проверка production-сборки и браузерный end-to-end с реальным провайдером не выполнялись.

Тесты используют фиксированные ответы двух агентов и HTTP transport mock. Они не обращаются к платному API.

```powershell
docker run --rm --network none --mount "type=bind,source=${PWD}\apps\api,target=/workspace,readonly" --workdir /workspace --env PYTHONDONTWRITEBYTECODE=1 --entrypoint /app/api/.venv/bin/python thinktogether-api:local -m pytest src/tests/thinktogether/test_course_pipeline.py src/tests/thinktogether/test_content_generation.py src/tests/thinktogether/test_course_demo.py -q --no-cov -p no:cacheprovider
Set-Location apps/web
node node_modules/typescript/bin/tsc --noEmit --incremental false
node node_modules/eslint/bin/eslint.js components/ThinkTogether/CourseGenerator.tsx components/ThinkTogether/expert/ExpertContent.tsx
bun test --isolate ./tests/thinktogether-course-pipeline.test.mjs ./tests/thinktogether-generation.test.mjs ./tests/thinktogether-course-blocks.test.mjs
```

Официальная документация: [модель Flash](https://api-docs.deepseek.com/quick_start/pricing/), [thinking и соответствие medium → high](https://api-docs.deepseek.com/guides/thinking_mode/).

## Файлы этой реализации

Новые:

```text
apps/api/src/services/thinktogether/course_generation/__init__.py
apps/api/src/services/thinktogether/course_generation/course_architect.txt
apps/api/src/services/thinktogether/course_generation/diagram_architect.txt
apps/api/src/services/thinktogether/course_generation/prompts.py
apps/api/src/services/thinktogether/course_generation/source.py
apps/api/src/services/thinktogether/course_generation/validator.py
apps/api/src/services/thinktogether/course_generation/svg_renderer.py
apps/api/src/services/thinktogether/course_generation/provider.py
apps/api/src/services/thinktogether/course_generation/generator.py
apps/api/src/routers/thinktogether/course_generation.py
apps/api/src/tests/thinktogether/test_course_pipeline.py
apps/web/components/ThinkTogether/CourseGenerator.tsx
apps/web/tests/thinktogether-course-pipeline.test.mjs
docs/superpowers/plans/2026-11-15-course-pipeline.md
docs/thinktogether/COURSE_PIPELINE_RU.md
```

Изменены:

```text
apps/api/src/routers/thinktogether/router.py
apps/api/src/services/thinktogether/job_handlers.py
apps/api/src/services/thinktogether/course_materials.py
apps/web/components/ThinkTogether/expert/ExpertContent.tsx
docker/thinktogether/production.env.example
```
