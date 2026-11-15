-- db/004_fix_feed_url.sql
-- Подменяет адрес источника на настоящий RSS-поток Хабра.
--
-- Было:  https://habr.com/ru/feed/  — это HTML-страница (Content-Type: text/html),
--        XML там нет, сборщик по нему не работает.
-- Стало: https://habr.com/ru/rss/articles/ — RSS 2.0 всех публикаций Хабра
--        (Content-Type: text/xml), отдаёт 40 записей за прогон.
--
-- Поле url в таблице sources объявлено unique, поэтому апдейт упадёт,
-- если такой адрес уже заведён отдельной строкой.

update sources
set url = 'https://habr.com/ru/rss/articles/'
where url = 'https://habr.com/ru/feed/';
