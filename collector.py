"""Сборщик данных: читает источники из Supabase, обходит RSS-ленты
и складывает найденные записи в таблицу events.

Запуск:
    python collector.py

Ключи и адрес базы читаются из файла .env (см. .env.example).
В коде их нет и быть не должно.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

# ------------------------------------------------------------
# Настройка окружения
# ------------------------------------------------------------

ENV_PATH = Path(__file__).resolve().parent / ".env"


def load_env(path: Path) -> None:
    """Читает .env в os.environ. Не перезаписывает уже заданные значения."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env(ENV_PATH)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    print("ОШИБКА: не заданы SUPABASE_URL и/или SUPABASE_SERVICE_ROLE_KEY.")
    print("Скопируйте .env.example в .env и заполните значениями из Supabase.")
    sys.exit(1)

# ------------------------------------------------------------
# HTTP-клиент (только стандартная библиотека)
# ------------------------------------------------------------

HTTP_TIMEOUT = 15  # ограничение времени ожидания запроса, сек
USER_AGENT = "telegram-habr-collector/1.0"


def http_get(url: str, headers: dict | None = None) -> bytes:
    """GET-запрос. Следует редиректам (urllib делает это сам)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return resp.read()


# ------------------------------------------------------------
# Supabase REST
# ------------------------------------------------------------

PG_REST_HEADERS = {
    "apikey": SUPABASE_SERVICE_ROLE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    "Content-Type": "application/json",
    "Accept-Profile": "public",
    "Content-Profile": "public",
}


def supabase_get(table: str, params: str = "") -> list[dict]:
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    if params:
        url += "?" + params
    data = http_get(url, headers=PG_REST_HEADERS)
    return json.loads(data.decode("utf-8"))


def supabase_upsert(table: str, rows: list[dict], on_conflict: str) -> list[dict]:
    """Upsert с защитой от дублей на уровне базы (on conflict merge).

    При конфликте по ключу on_conflict строка обновляется, а не дублируется
    и не роняет запрос ошибкой уникальности. Возвращает список фактически
    вставленных строк (обновлённые дубли база не считает вставкой).
    """
    url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}"
    req = urllib.request.Request(
        url,
        data=json.dumps(rows).encode("utf-8"),
        headers={**PG_REST_HEADERS, "Prefer": "resolution=merge-duplicates, return=representation"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        data = resp.read()
    return json.loads(data.decode("utf-8")) if data else []


def supabase_patch(table: str, row_id: int, fields: dict) -> None:
    """Обновление полей состояния источника."""
    url = f"{SUPABASE_URL}/rest/v1/{table}?id=eq.{row_id}"
    req = urllib.request.Request(
        url,
        data=json.dumps(fields).encode("utf-8"),
        headers={**PG_REST_HEADERS, "Prefer": "return=minimal"},
        method="PATCH",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        resp.read()


def supabase_upsert_events(table: str, rows: list[dict]) -> int:
    """Upsert записей событий по material_url.

    Повторный прогон не создаёт копий и не падает на уникальности:
    при конфликте по material_url обновляются только контентные поля
    (title, description, image_url, published_at). Дата обнаружения
    collected_at при обновлении НЕ переписывается — остаётся той,
    что была при первой записи.

    Возвращает количество записей, которых в базе ещё не было.
    """
    # 1. Считаем, какие material_url уже есть в базе.
    urls = [r["material_url"] for r in rows]
    existing = supabase_get(table, params="select=material_url&material_url=in.(" + ",".join(urls) + ")")
    existing_urls = {r["material_url"] for r in existing}

    # 2. Готовим payload: при обновлении collected_at не трогаем.
    payload = []
    for row in rows:
        patch = {k: v for k, v in row.items() if k != "collected_at"}
        payload.append(patch)

    # 3. Upsert с merge-duplicates: дубли обновляются, не падают на уникальности.
    url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict=material_url"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={**PG_REST_HEADERS, "Prefer": "resolution=merge-duplicates, return=minimal"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        resp.read()

    # 4. Количество новых = те, которых не было в existing_urls.
    return sum(1 for r in rows if r["material_url"] not in existing_urls)


# ------------------------------------------------------------
# Нормализация ссылок
# ------------------------------------------------------------

_UTM_PREFIX = "utm_"
_DROP_PARAMS = {"ref", "ref_src", "fbclid", "gclid", "yclid"}


def normalize_url(url: str) -> str:
    """Приводит ссылку к единому виду: убирает utm_* и другие рекламные
    метки, срезает завершающий слеш. Именно эта ссылка идёт в базу."""
    if not url:
        return ""
    parts = urllib.parse.urlparse(url.strip())
    query = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(_UTM_PREFIX) and k.lower() not in _DROP_PARAMS
    ]
    path = parts.path.rstrip("/")
    return urllib.parse.urlunparse(
        (parts.scheme, parts.netloc, path, "", urllib.parse.urlencode(query), "")
    )


# ------------------------------------------------------------
# Разбор RSS
# ------------------------------------------------------------

_IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"', re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(fragment: str) -> str:
    """Убирает HTML-разметку, оставляя чистый текст."""
    if not fragment:
        return ""
    text = _TAG_RE.sub(" ", fragment)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def extract_image(fragment: str) -> str | None:
    """Достаёт ссылку на изображение из описания, если оно есть."""
    if not fragment:
        return None
    m = _IMG_RE.search(fragment)
    return m.group(1) if m else None


def local(tag: str) -> str:
    """Имя тега без пространства имён."""
    return tag.split("}")[-1]


def child_text(item: ET.Element, name: str) -> str | None:
    for child in item:
        if local(child.tag) == name and child.text:
            return child.text.strip()
    return None


def parse_pubdate(raw: str | None) -> str | None:
    """Дата публикации материала. Может отсутствовать — тогда None."""
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def parse_feed(xml_bytes: bytes) -> list[dict]:
    """Разбирает RSS-ленту и возвращает записи в нормализованном виде."""
    root = ET.fromstring(xml_bytes)
    entries: list[dict] = []
    for item in root.iter("item"):
        link_raw = child_text(item, "link") or ""
        description_html = child_text(item, "description") or ""
        entries.append(
            {
                "title": child_text(item, "title") or "",
                "material_url": normalize_url(link_raw),
                "image_url": extract_image(description_html),
                "published_at": parse_pubdate(child_text(item, "pubDate")),
                "description": strip_html(description_html),
            }
        )
    return entries


# ------------------------------------------------------------
# Основной сценарий
# ------------------------------------------------------------

EVENT_TYPE = "new_article"  # тип события: публикация новой статьи


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def collect_source(source: dict) -> tuple[int, int, bool]:
    """Обходит один источник. Возвращает (найдено записей, записано, успешно ли)."""
    url = source["url"]
    try:
        xml_bytes = http_get(url, headers={"Accept": "application/rss+xml, application/xml, text/xml"})
        entries = parse_feed(xml_bytes)
    except (urllib.error.URLError, urllib.error.HTTPError, ET.ParseError, ValueError, OSError) as exc:
        print(f"{url} | ошибка: {exc}")
        # фиксируем обращение и ошибку
        supabase_patch(
            "sources",
            source["id"],
            {
                "last_checked_at": now_iso(),
                "error_streak": int(source.get("error_streak") or 0) + 1,
            },
        )
        return 0, 0, False

    collected_at = now_iso()
    rows = []
    for e in entries:
        if not e["material_url"]:
            continue
        rows.append(
            {
                "source_id": source["id"],
                "material_url": e["material_url"],
                "event_type": EVENT_TYPE,
                "published_at": e["published_at"],
                "collected_at": collected_at,
                "title": e["title"],
                "description": e["description"],
                "image_url": e["image_url"],
            }
        )

    written = 0
    if rows:
        # Upsert по material_url: повторный прогон не создаёт копий и не падает
        # на уникальности. Дата обнаружения collected_at при обновлении не переписывается.
        written = supabase_upsert_events("events", rows)

    # фиксируем успешное обращение
    supabase_patch(
        "sources",
        source["id"],
        {
            "last_checked_at": collected_at,
            "last_success_at": collected_at,
            "error_streak": 0,
        },
    )

    return len(rows), written, True


def main() -> int:
    sources = supabase_get("sources", params="select=id,url,error_streak&is_active=eq.true")
    if not sources:
        print("Нет активных источников (is_active = true).")
        print("ИТОГО: 0 источников обработано, 0 с ошибкой, 0 новых событий")
        return 0

    total_found = 0
    total_written = 0
    total_errors = 0
    for source in sources:
        try:
            found, written, ok = collect_source(source)
        except Exception as exc:  # защита от падения одного источника на весь цикл
            total_errors += 1
            print(f"{source['url']} | ошибка: {exc}")
            continue
        if not ok:
            total_errors += 1
        total_found += found
        total_written += written
        print(f"{source['url']} | найдено: {found} | записано: {written}")

    print(
        f"ИТОГО: источников обработано {len(sources)}, "
        f"с ошибкой {total_errors}, новых событий {total_written}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
