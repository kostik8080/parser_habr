-- 002_sources.sql — наполнение таблицы sources
-- Парсер-робот публичной активности статей
-- СУБД: PostgreSQL (Supabase)

-- Источник: общая лента Habr. Фильтрация по подпискам
-- (авторы, хабы, компании) выполняется на этапе формирования сводки.

insert into sources (url, title, is_active)
values (
    'https://habr.com/ru/feed/',
    'Habr / общая лента',
    true
)
on conflict (url) do nothing;

-- Поля состояния (last_checked_at, last_success_at, error_streak)
-- не заполняем — их проставляет сборщик.
