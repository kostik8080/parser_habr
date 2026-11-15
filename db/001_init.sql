-- 001_init.sql — инициализация схемы БД
-- Парсер-робот публичной активности статей
-- СУБД: PostgreSQL (Supabase)

-- ============================================================
-- Таблица sources — источники
-- ============================================================
create table sources (
    id              bigint generated always as identity primary key,
    url             text not null unique,
    title           text,
    is_active       boolean not null default true,
    last_checked_at timestamptz,
    last_success_at timestamptz,
    error_streak    integer not null default 0
);

comment on table  sources               is 'Источники, за которыми следит сборщик';
comment on column sources.url           is 'Адрес источника (уникальный)';
comment on column sources.title         is 'Название источника для удобства чтения';
comment on column sources.is_active     is 'Включён ли источник';
comment on column sources.last_checked_at is 'Когда сборщик последний раз обращался к источнику';
comment on column sources.last_success_at is 'Когда источник последний раз успешно ответил';
comment on column sources.error_streak  is 'Сколько ошибок подряд (сбрасывается при успешном ответе)';

-- ============================================================
-- Таблица events — события
-- ============================================================
create table events (
    id            bigint generated always as identity primary key,
    source_id     bigint not null references sources(id),
    material_url  text not null unique,
    event_type    text not null,
    published_at  timestamptz,
    collected_at  timestamptz not null default now()
);

comment on table  events              is 'События по материалам (реакции, новые статьи, правки, подборки)';
comment on column events.source_id    is 'Ссылка на источник, от которого пришло событие';
comment on column events.material_url is 'Ссылка на материал. Уникальный ключ события — защита от дублей при повторных прогонах';
comment on column events.event_type   is 'Тип события: reaction / new_article / update / featured';
comment on column events.published_at is 'Дата публикации материала. Может быть пустой — не у всех источников есть эта дата';
comment on column events.collected_at is 'Когда сборщик увидел материал. Всегда заполняется';

-- ============================================================
-- Таблица summaries — сводки
-- ============================================================
create table summaries (
    id                    bigint generated always as identity primary key,
    created_at            timestamptz not null default now(),
    content               text not null,
    delivered_to_telegram boolean not null default false,
    delivered_to_email    boolean not null default false
);

comment on table  summaries                   is 'Готовые сводки для отправки пользователю';
comment on column summaries.created_at        is 'Когда сводка сформирована';
comment on column summaries.content           is 'Текст сводки';
comment on column summaries.delivered_to_telegram is 'Доставлена ли в Telegram';
comment on column summaries.delivered_to_email    is 'Доставлена ли на email';
