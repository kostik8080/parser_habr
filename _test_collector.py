"""Юнит-тесты сборщика: нормализация URL, разбор HTML/RSS, даты, .env.

Запуск:
    python _test_collector.py
или
    python -m unittest _test_collector -v

Только стандартная библиотека. Сеть не трогают — тестируется чистая логика.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

# collector.py на импорте требует ключи окружения: подставляем заглушки,
# чтобы тесты шли без настроенного .env и без доступа к Supabase
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import collector  # noqa: E402


class NormalizeUrlTests(unittest.TestCase):
    def test_removes_utm_and_tracking(self):
        self.assertEqual(
            collector.normalize_url(
                "https://habr.com/ru/articles/1/?utm_source=rss&utm_medium=feed&ref=feed&fbclid=abc&gclid=x&yclid=y"
            ),
            "https://habr.com/ru/articles/1",
        )

    def test_keeps_meaningful_params(self):
        self.assertEqual(
            collector.normalize_url("https://habr.com/ru/articles/1/?page=2&sort=new"),
            "https://habr.com/ru/articles/1?page=2&sort=new",
        )

    def test_strips_trailing_slash(self):
        self.assertEqual(
            collector.normalize_url("https://habr.com/ru/articles/1/"),
            "https://habr.com/ru/articles/1",
        )

    def test_case_insensitive_param_names(self):
        self.assertEqual(
            collector.normalize_url("https://habr.com/ru/articles/1/?UTM_SOURCE=rss"),
            "https://habr.com/ru/articles/1",
        )

    def test_empty(self):
        self.assertEqual(collector.normalize_url(""), "")
        self.assertEqual(collector.normalize_url("   "), "")


class StripHtmlTests(unittest.TestCase):
    def test_removes_tags_and_unescapes(self):
        self.assertEqual(collector.strip_html("<p>Привет &amp; пока</p>"), "Привет & пока")

    def test_collapses_whitespace(self):
        self.assertEqual(collector.strip_html("<b>a</b>  <i>b</i>"), "a b")

    def test_empty(self):
        self.assertEqual(collector.strip_html(""), "")
        self.assertEqual(collector.strip_html(None), "")


class ExtractImageTests(unittest.TestCase):
    def test_finds_img_src(self):
        self.assertEqual(
            collector.extract_image('текст <img alt="x" src="https://habr.com/pic/1.jpg"> конец'),
            "https://habr.com/pic/1.jpg",
        )

    def test_no_image(self):
        self.assertIsNone(collector.extract_image("<p>текст без картинки</p>"))
        self.assertIsNone(collector.extract_image(""))
        self.assertIsNone(collector.extract_image(None))


class PubDateTests(unittest.TestCase):
    def test_offset_converted_to_utc(self):
        self.assertEqual(
            collector.parse_pubdate("Fri, 14 Nov 2026 10:00:00 +0300"),
            "2026-11-14T07:00:00+00:00",
        )

    def test_gmt_kept_as_utc(self):
        self.assertEqual(
            collector.parse_pubdate("Fri, 14 Nov 2026 10:00:00 GMT"),
            "2026-11-14T10:00:00+00:00",
        )

    def test_garbage_returns_none(self):
        self.assertIsNone(collector.parse_pubdate("не дата"))
        self.assertIsNone(collector.parse_pubdate(None))
        self.assertIsNone(collector.parse_pubdate(""))


class HelperTests(unittest.TestCase):
    def test_local_strips_namespace(self):
        self.assertEqual(
            collector.local("{http://purl.org/dc/elements/1.1/}creator"),
            "creator",
        )

    def test_child_text_reads_direct_children_only(self):
        xml = ET.fromstring("<item><title>t</title><sub><title>x</title></sub></item>")
        self.assertEqual(collector.child_text(xml, "title"), "t")

    def test_child_text_missing(self):
        xml = ET.fromstring("<item></item>")
        self.assertIsNone(collector.child_text(xml, "title"))


RSS_SAMPLE = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<rss version="2.0"><channel><title>Habr</title>\n'
    "<item>\n"
    "    <title>Статья про Python</title>\n"
    "    <link>https://habr.com/ru/articles/111/?utm_source=rss</link>\n"
    "    <description>Текст &lt;b&gt;с разметкой&lt;/b&gt; "
    '&lt;img src="https://habr.com/pic/1.jpg"&gt;</description>\n'
    "    <pubDate>Fri, 14 Nov 2026 10:00:00 +0000</pubDate>\n"
    "</item>\n"
    "<item>\n"
    "    <title>Без ссылки и даты</title>\n"
    "    <description>просто текст</description>\n"
    "</item>\n"
    "</channel></rss>"
)


class ParseFeedTests(unittest.TestCase):
    def test_parses_fields_of_first_item(self):
        entries = collector.parse_feed(RSS_SAMPLE.encode("utf-8"))
        self.assertEqual(len(entries), 2)
        first = entries[0]
        self.assertEqual(first["title"], "Статья про Python")
        self.assertEqual(first["material_url"], "https://habr.com/ru/articles/111")
        self.assertEqual(first["image_url"], "https://habr.com/pic/1.jpg")
        self.assertEqual(first["published_at"], "2026-11-14T10:00:00+00:00")
        self.assertEqual(first["description"], "Текст с разметкой")

    def test_missing_fields_become_empty_or_none(self):
        entries = collector.parse_feed(RSS_SAMPLE.encode("utf-8"))
        second = entries[1]
        self.assertEqual(second["material_url"], "")
        self.assertIsNone(second["published_at"])
        self.assertIsNone(second["image_url"])


class LoadEnvTests(unittest.TestCase):
    def test_reads_values_and_keeps_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "TEST_COLLECTOR_A=1\n"
                "# комментарий\n"
                "строка без знака равенства\n"
                'TEST_COLLECTOR_B="в кавычках"\n',
                encoding="utf-8",
            )
            os.environ["TEST_COLLECTOR_A"] = "existing"
            try:
                collector.load_env(path)
                self.assertEqual(os.environ["TEST_COLLECTOR_A"], "existing")
                self.assertEqual(os.environ["TEST_COLLECTOR_B"], "в кавычках")
            finally:
                os.environ.pop("TEST_COLLECTOR_A", None)
                os.environ.pop("TEST_COLLECTOR_B", None)


if __name__ == "__main__":
    unittest.main()
