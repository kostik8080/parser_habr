-- db/003_events_columns.sql
-- Добавляет в events колонки для заголовка, описания и изображения.
-- Этих полей нет в docs/db-schema.md — они нужны, чтобы сводка была
-- читаемой без обхода исходных страниц. Запускать после 001_init.sql.

alter table events
    add column if not exists title       text,
    add column if not exists description text,
    add column if not exists image_url   text;

comment on column events.title       is 'Заголовок материала из ленты';
comment on column events.description is 'Короткое описание материала (текст без разметки)';
comment on column events.image_url   is 'Ссылка на изображение материала, если есть';
