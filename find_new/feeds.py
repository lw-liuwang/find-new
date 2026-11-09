"""Parse public RSS, Atom, RDF and configurable JSON feeds."""

import html
import json
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"p", "br", "div", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
        if tag in {"p", "div", "li"}:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(value):
    parser = TextParser()
    parser.feed(str(value or ""))
    return " ".join("".join(parser.parts).split())


def normalize_url(value, base=""):
    url = urlsplit(urljoin(base, str(value or "").strip()))
    if url.scheme not in {"http", "https"} or not url.hostname:
        raise ValueError("链接必须是 HTTP(S) 地址")
    query = [(key, value) for key, value in parse_qsl(url.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}]
    return urlunsplit((url.scheme.lower(), url.netloc.lower(), url.path or "/",
                       urlencode(query), ""))


def parse_date(value):
    if value is None or value == "":
        return None
    if (isinstance(value, (int, float)) and not isinstance(value, bool)
            or isinstance(value, str) and value.isdecimal()):
        try:
            return datetime.fromtimestamp(float(value), timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    try:
        date = parsedate_to_datetime(str(value))
    except (ValueError, TypeError, OverflowError):
        try:
            date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return date.astimezone(timezone.utc)


def child_text(element, names):
    # Field order matters: prefer published over updated and summaries over content.
    for name in names:
        for child in element:
            if child.tag.rsplit("}", 1)[-1] == name:
                value = "".join(child.itertext()).strip()
                if value:
                    return value
    return ""


def parse_xml(body):
    root = ET.fromstring(body)
    if root.tag.rsplit("}", 1)[-1] not in {"rss", "feed", "RDF"}:
        raise ValueError("响应不是 RSS、Atom 或 RDF 订阅源")
    rows = []
    for entry in root.iter():
        if entry.tag.rsplit("}", 1)[-1] not in {"item", "entry"}:
            continue
        link = child_text(entry, ["link"])
        if not link:
            link = next((child.get("href", "") for child in entry
                         if child.tag.rsplit("}", 1)[-1] == "link"
                         and child.get("rel", "alternate") == "alternate"), "")
        link = link or entry.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about", "")
        rows.append({
            "title": child_text(entry, ["title"]),
            "url": link,
            "description": child_text(entry, ["description", "summary", "encoded", "content"]),
            "published": child_text(entry, ["pubDate", "published", "date", "updated"]),
        })
    return rows


def dig(row, path):
    for key in path.split("."):
        if isinstance(row, dict):
            row = row.get(key)
        elif isinstance(row, list) and key.isdigit() and int(key) < len(row):
            row = row[int(key)]
        else:
            return ""
    return row if row is not None else ""


def parse_json(body, source):
    data = json.loads(body)
    rows = dig(data, source["items_path"]) if source.get("items_path") else data
    if not isinstance(rows, list):
        raise ValueError("JSON 的条目路径必须指向列表")
    fields = source["fields"]
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = {}
        for name in ("title", "url", "description", "published"):
            spec = fields.get(name, "")
            if "{" in spec:
                paths = re.findall(r"\{([^{}]+)\}", spec)
                if name in {"title", "url"} and any(dig(row, path) == "" for path in paths):
                    item[name] = ""
                else:
                    item[name] = re.sub(r"\{([^{}]+)\}", lambda m: str(dig(row, m[1])), spec)
            else:
                item[name] = dig(row, spec)
        result.append(item)
    return result


def news_url(source, hours):
    language = source.get("language", "zh-CN")
    region = source.get("region", "CN")
    edition_language = {"zh-CN": "zh-Hans", "zh-TW": "zh-Hant", "zh-HK": "zh-Hant"}.get(
        language, language.split("-", 1)[0])
    query = urlencode({
        "q": f"({source['query']}) when:{max(1, math.ceil(hours / 24))}d",
        "hl": language,
        "gl": region,
        "ceid": f"{region}:{edition_language}",
    })
    return "https://news.google.com/rss/search?" + query


def parse_items(body, source, base_url, now, hours):
    raw = parse_json(body, source) if source["kind"] == "json" else parse_xml(body)
    items = {}
    cutoff = now - timedelta(hours=hours)
    for row in raw:
        title = plain_text(row["title"])
        if not title or not row["url"]:
            continue
        try:
            url = normalize_url(html.unescape(str(row["url"])), base_url)
        except ValueError:
            continue
        published = parse_date(row["published"])
        if published and published < cutoff:
            continue
        items[url] = {
            "source": source["name"],
            "title": title,
            "url": url,
            "published": published.isoformat(timespec="seconds") if published else None,
            "description": plain_text(row["description"])[:4000],
        }
    return list(items.values())


def load_config(path):
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(config.get("sources"), list):
        raise ValueError("配置必须包含 sources 列表")
    hours = config.get("window_hours", 72)
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 0 < hours <= 8760:
        raise ValueError("window_hours 必须在 0 到 8760 小时之间")
    names = set()
    for source in config["sources"]:
        if not isinstance(source, dict):
            raise ValueError("每个来源必须是一个对象")
        name = source.get("name")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError("来源必须具有非空且唯一的 name")
        names.add(name)
        if not isinstance(source.get("enabled", True), bool):
            raise ValueError(f"{name}: enabled 必须是布尔值")
        kind = source.get("kind")
        if not isinstance(kind, str) or kind not in {"rss", "json", "google_news"}:
            raise ValueError(f"{name}: 不支持的 kind")
        if kind == "google_news":
            for field in ("query", "language", "region"):
                value = source.get(field, "" if field == "query" else "default")
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"{name}: {field} 必须是非空字符串")
        else:
            if not isinstance(source.get("url"), str):
                raise ValueError(f"{name}: 缺少 url")
            normalize_url(source["url"])
        if kind == "json":
            fields = source.get("fields")
            if (not isinstance(fields, dict) or not fields.get("title") or not fields.get("url")
                    or any(not isinstance(value, str) for value in fields.values())):
                raise ValueError(f"{name}: fields 必须包含 title 和 url 字符串映射")
            if not isinstance(source.get("items_path", ""), str):
                raise ValueError(f"{name}: items_path 必须是字符串")
        method = source.get("method", "GET")
        if not isinstance(method, str) or method not in {"GET", "POST", "PUT"}:
            raise ValueError(f"{name}: method 只支持 GET、POST、PUT")
        if method != "GET" and kind != "json":
            raise ValueError(f"{name}: 只有 JSON 来源可以配置 POST/PUT 查询")
        if "body" in source and (method == "GET" or not isinstance(source["body"], dict)):
            raise ValueError(f"{name}: body 必须是 POST/PUT 查询的 JSON 对象")
    return config
