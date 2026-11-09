"""Command-line entry point: python -m find_new."""

import argparse
import json
import sqlite3
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from .feeds import load_config, news_url, parse_items
from .http import Client, FetchError
from .store import Store


def positive_int(value):
    number = int(value)
    if not 1 <= number <= 10000:
        raise argparse.ArgumentTypeError("必须在 1 到 10000 之间")
    return number


def markdown(report):
    lines = ["# find-new", "", f"更新时间：{report['generated_at']}", ""]
    if "new_count" in report:
        lines += [f"本轮找到 {report['count']} 条，新增入库 {report['new_count']} 条。", ""]
    for item in report["items"]:
        # Link on a separate line avoids treating untrusted feed text as Markdown links.
        title = item["title"].translate(str.maketrans({char: "\\" + char for char in "\\`*_{}[]<>#!|"}))
        description = item["description"].translate(str.maketrans({char: "\\" + char for char in "\\`*_{}[]<>#!|"}))
        lines += [f"## {title}", "", item["url"], "",
                  f"来源：{item['source']} · 发布时间：{item['published'] or '未提供'}", ""]
        if description:
            lines += [description, ""]
    failures = [source for source in report.get("sources", []) if not source["ok"]]
    if failures:
        lines += ["## 抓取失败", ""]
        lines += [f"- {source['name']}：{source['error']}" for source in failures]
        lines.append("")
    return "\n".join(lines)


def collect(sources, hours, store, client):
    now = datetime.now(timezone.utc)
    timestamp = now.isoformat(timespec="seconds")
    statuses, items = [], {}
    new_count = 0
    for source in sources:
        if source.get("enabled", True) is False:
            continue
        name = source["name"]
        url = news_url(source, hours) if source["kind"] == "google_news" else source["url"]
        try:
            body = client.fetch(url, method=source.get("method", "GET"), body=source.get("body"))
            rows = parse_items(body, source, url, now, hours)
        except (FetchError, ValueError, ET.ParseError) as error:
            statuses.append({"name": name, "ok": False, "count": 0, "error": str(error)})
            print(f"[失败] {name}: {error}", file=sys.stderr)
            continue
        new_count += store.save(rows, timestamp)
        for row in rows:
            items.setdefault(row["url"], row)
        statuses.append({"name": name, "ok": True, "count": len(rows), "error": ""})
        print(f"[成功] {name}: {len(rows)} 条", file=sys.stderr)
    rows = sorted(items.values(), key=lambda item: item["published"] or timestamp, reverse=True)
    return {"generated_at": timestamp, "count": len(rows), "new_count": new_count,
            "sources": statuses, "items": rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description="搜索新闻、采集公开订阅和 JSON API，并在本地去重保存。")
    commands = parser.add_subparsers(dest="command", required=True)
    batch = commands.add_parser("collect", help="采集配置中的全部启用来源")
    batch.add_argument("--config", type=Path, default=Path("sources.json"))
    search = commands.add_parser("search", help="通过 Google News 搜索公开新闻并保存")
    search.add_argument("query", help="关键词，支持 Google News 搜索语法")
    search.add_argument("--days", type=positive_int, default=3)
    search.add_argument("--language", default="zh-CN")
    search.add_argument("--region", default="CN")
    local = commands.add_parser("list", help="查询已保存的结果，不访问网络")
    local.add_argument("--query", default="", help="在标题、描述和来源中匹配文本")
    local.add_argument("--limit", type=positive_int, default=50)
    for command in (batch, search, local):
        command.add_argument("--db", type=Path, default=Path("data/find-new.sqlite3"))
        command.add_argument("--format", choices=("json", "markdown"), default="json")
    for command in (batch, search):
        command.add_argument("--output-dir", type=Path, default=Path("data"))
    args = parser.parse_args(argv)

    # Validate configuration before opening or creating the database.
    config = None
    if args.command == "collect":
        config = load_config(args.config)
    if args.command == "search" and not args.query.strip():
        parser.error("搜索关键词不能为空")
    store = Store(args.db)
    try:
        if args.command == "list":
            items = store.search(args.query, args.limit)
            report = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "count": len(items), "items": items}
        else:
            if args.command == "collect":
                sources, hours = config["sources"], config.get("window_hours", 72)
            else:
                sources = [{"name": args.query, "kind": "google_news", "query": args.query,
                            "language": args.language, "region": args.region}]
                hours = args.days * 24
            report = collect(sources, hours, store, Client(store))
            args.output_dir.mkdir(parents=True, exist_ok=True)
            (args.output_dir / "latest.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (args.output_dir / "latest.md").write_text(markdown(report), encoding="utf-8")
        output = markdown(report) if args.format == "markdown" else json.dumps(report, ensure_ascii=False, indent=2)
        print(output)
        if args.command != "list" and not any(source["ok"] for source in report["sources"]):
            print("所有启用来源均未成功抓取；详见 sources 错误信息。", file=sys.stderr)
            return 1
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"错误：{error}", file=sys.stderr)
        raise SystemExit(1)
