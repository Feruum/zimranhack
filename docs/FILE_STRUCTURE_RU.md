# Базовая структура файлов ThinkTogether + ThinkTogether

Рабочий проект: `C:/Users/Ferumm/zimran/learnhouse/.worktrees/thinktogether`.

Это один репозиторий с несколькими приложениями. **ThinkTogether — основа; ThinkTogether добавлен внутрь web и API, а голосовой агент вынесен отдельно.**

## Основные папки

```text
thinktogether/
├── apps/
│   ├── web/                 # Интерфейс: Next.js / React
│   ├── api/                 # Сервер: FastAPI / Python
│   ├── collab/              # Совместные доски: Hocuspocus / Yjs
│   ├── voice-agent/         # Голосовой AI ThinkTogether
│   ├── cli/                 # Командная утилита ThinkTogether
│   └── e2e/                 # Сквозные проверки приложения
├── packages/
│   ├── sdk/                 # Клиентская библиотека API
│   ├── react/               # Интеграция с React
│   └── vue/                 # Интеграция с Vue
├── docker/thinktogether/    # Запуск и инфраструктура нашего проекта
├── scripts/                # Вспомогательные скрипты проекта
├── docs/                   # Документация
├── .github/workflows/      # Автоматические проверки и сборки CI
├── README.md               # Общее описание ThinkTogether
└── LICENSE                 # Лицензия проекта
```

## Интерфейс — `apps/web`

```text
apps/web/
├── app/                    # Страницы и маршруты Next.js
│   ├── auth/               # Вход и восстановление доступа
│   ├── editor/             # Редактор учебных материалов
│   ├── admin/              # Администрирование установки
│   ├── api/                # Серверные web-прокси и обработчики
│   └── orgs/[orgslug]/      # Страницы организации
│       ├── (withmenu)/      # Нативные разделы ThinkTogether с меню
│       ├── dash/            # Управление школой и контентом
│       └── thinktogether/   # Страницы нашего продукта
├── components/             # Компоненты интерфейса
│   ├── Objects/Menus/      # Верхнее меню, нижняя панель, More → Core
│   ├── ThinkTogether/      # Today, практика, группа, профиль, Expert
│   └── ui/                 # Переиспользуемые UI-компоненты
├── services/               # Запросы к API, конфигурация и клиентская логика
│   └── thinktogether/      # Клиент API и вспомогательная логика TT
├── hooks/                  # React hooks
├── locales/                # Переводы
├── public/                 # Статические файлы, доступные браузеру
├── styles/                 # Общие стили
├── tests/                  # Проверки frontend
├── proxy.ts                # Определение организации и маршрутизация
├── next.config.js          # Настройки Next.js
├── server-wrapper.js       # Запуск собранного web-сервера
└── package.json            # Зависимости и команды web-приложения
```

Файлы `page.tsx` задают страницы, `layout.tsx` — их общую оболочку. Крупные экраны ThinkTogether находятся в компонентах вроде `TodayPage.tsx`, `PracticePage.tsx`, `RoomPage.tsx`, а соответствующие `page.tsx` подключают их к маршрутам.

## Сервер — `apps/api`

```text
apps/api/
├── app.py                  # Главная точка входа FastAPI
├── cli.py                  # Установка и административные CLI-команды
├── config/                 # Чтение настроек приложения
├── src/
│   ├── router.py           # Подключение всех API-разделов
│   ├── routers/            # HTTP endpoints: принимают запросы
│   ├── services/           # Бизнес-логика: что делать с запросом
│   ├── db/                 # Модели таблиц и схемы данных
│   ├── security/           # Авторизация, роли и проверки доступа
│   ├── core/               # Общая инфраструктура и lifecycle
│   └── tests/              # Проверки backend
├── migrations/             # Версионированные изменения схемы базы
├── scripts/                # Проверки, калибровка AI, нагрузочные утилиты
├── content/                # Локальные файлы при соответствующем режиме
├── pyproject.toml          # Python-зависимости и настройки
├── uv.lock                 # Зафиксированные Python-зависимости
└── requirements-thinktogether.txt  # Дополнительные зависимости TT
```

Связь уровней: **router принимает запрос → service применяет бизнес-правила → db-модели описывают данные → база сохраняет результат**. Проверки доступа участвуют в этом пути на сервере.

ThinkTogether находится в отдельных подпапках:

- `src/routers/thinktogether/` — профиль, практика, группы, комнаты, оплата и staff API.
- `src/services/thinktogether/` — адаптация, оценивание, платежи, поддержка и фоновые задачи.
- `src/db/thinktogether/` — модели таблиц с префиксом `tt_`.
- `src/tests/thinktogether/` — тесты этих модулей.

Обычные курсы, задания, пользователи и остальные функции ThinkTogether остаются в соседних модулях.

## Доски, голос и запуск

| Файл / папка | Назначение |
|---|---|
| `apps/collab/src/index.ts` | Сервер совместных досок |
| `apps/collab/src/thinktogether.ts` | Дополнительные проверки TT-досок |
| `apps/voice-agent/thinktogether_voice/worker.py` | Запуск голосового агента LiveKit |
| `apps/api/src/services/thinktogether/jobs_worker.py` | Запуск worker или scheduler ThinkTogether |
| `docker/thinktogether/docker-compose.yml` | Состав локальных сервисов и их связи |
| `docker/thinktogether/production.compose.yml` | Отдельный шаблон production-развёртывания |
| `docker/thinktogether/nginx.conf` | Маршрутизация к web, API и collab |
| `docker/thinktogether/setup-local.ps1` | Подготовка и запуск локального окружения |

Одного `main` на всё приложение нет: web, API, доски, голос, worker и scheduler запускаются отдельными процессами.

## Где что менять

| Задача | Начать отсюда |
|---|---|
| Текст или вид экрана ThinkTogether | `apps/web/components/ThinkTogether/` |
| Верхнее/нижнее меню и More | `apps/web/components/Objects/Menus/` |
| Добавить страницу | `apps/web/app/` |
| Изменить запросы frontend к TT | `apps/web/services/thinktogether/` |
| Изменить учебное или коммерческое правило | `apps/api/src/services/thinktogether/` |
| Добавить HTTP endpoint | Соответствующий файл в `apps/api/src/routers/` |
| Изменить структуру данных | Модель в `apps/api/src/db/` и миграция в `apps/api/migrations/` |
| Изменить состав сервисов | `docker/thinktogether/` |
| Найти описание процессов | [FULL_APPLICATION_PIPELINE_RU.md](FULL_APPLICATION_PIPELINE_RU.md) |
| Посмотреть готовность и эксплуатацию TT | [STATUS.md](thinktogether/STATUS.md), [RUNBOOK.md](thinktogether/RUNBOOK.md) |

`node_modules`, `.next`, `__pycache__` — установленные зависимости и результаты работы инструментов. `.learnhouse` и `.frontend-workbench` содержат локальные рабочие данные и проверки. Это не основные исходники приложения. Секреты из `.env` не следует помещать в `public/`, документацию или Git.
