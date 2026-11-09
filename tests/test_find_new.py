import contextlib
import io
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from find_new.__main__ import collect, main, markdown
from find_new.feeds import news_url, parse_items
from find_new.http import Client, FetchError
from find_new.store import Store

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 9, 8, tzinfo=timezone.utc)


class FeedTests(unittest.TestCase):
    def parse(self, filename, kind="rss", **options):
        source = {"name": "test", "kind": kind, **options}
        return parse_items((FIXTURES / filename).read_bytes(), source,
                           "https://example.com/feed", NOW, 72)

    def test_rss_filters_old_items_and_preserves_undated_items(self):
        rows = self.parse("rss.xml")
        self.assertEqual([row["title"] for row in rows],
                         ["Inference & GPU update", "Without a date"])
        self.assertEqual(rows[0]["url"], "https://example.com/news?version=2")
        self.assertEqual(rows[0]["description"], "A new kernel with lower latency.")
        self.assertEqual(rows[0]["published"], "2026-11-09T01:00:00+00:00")
        self.assertIsNone(rows[1]["published"])

    def test_atom_uses_article_link_and_original_publication_date(self):
        row, = self.parse("atom.xml")
        self.assertEqual(row["url"], "https://example.com/releases/v1.2.0")
        self.assertEqual(row["published"], "2026-11-08T08:00:00+00:00")
        self.assertEqual(row["description"], "Faster inference.")

    def test_arxiv_rdf_namespaces_and_fallback_link(self):
        row, = self.parse("rdf.xml")
        self.assertEqual(row["url"], "https://arxiv.org/abs/2611.00001")
        self.assertEqual(row["title"], "A useful paper")

    def test_json_nested_fields_templates_and_incomplete_items(self):
        row, = self.parse("papers.json", "json", fields={
            "title": "paper.title", "url": "https://huggingface.co/papers/{paper.id}",
            "description": "paper.summary", "published": "publishedAt",
        })
        self.assertEqual(row["title"], "Efficient attention")
        self.assertEqual(row["url"], "https://huggingface.co/papers/2611.00002")

    def test_modelscope_list_and_unix_update_time(self):
        rows = self.parse("modelscope.json", "json", items_path="Data.Models", fields={
            "title": "{Path}/{Name}", "url": "https://modelscope.ai/models/{Path}/{Name}",
            "description": "{Description} · 下载 {Downloads} · 收藏 {Stars}",
            "published": "LastUpdatedTime",
        })
        row, = rows
        self.assertEqual(row["title"], "ExampleOrg/Test-Model")
        self.assertEqual(row["url"], "https://modelscope.ai/models/ExampleOrg/Test-Model")
        self.assertEqual(row["published"], "2026-11-09T02:00:00+00:00")
        self.assertEqual(row["description"], "Efficient attention · 下载 123 · 收藏 4")

    def test_html_response_is_a_source_error(self):
        with self.assertRaisesRegex(ValueError, "不是 RSS"):
            parse_items(b"<html><body>Access denied</body></html>",
                        {"name": "bad", "kind": "rss"}, "https://example.com", NOW, 72)

    def test_news_query_keeps_boolean_grouping_and_time_window(self):
        url = news_url({"query": "TPU OR GPU"}, 36)
        params = parse_qs(urlsplit(url).query)
        self.assertEqual(params["q"], ["(TPU OR GPU) when:2d"])
        self.assertEqual(params["ceid"], ["CN:zh-Hans"])


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "nested" / "items.sqlite3"
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def test_repeated_collection_updates_item_without_duplicate(self):
        item = {"url": "https://example.com/a", "title": "GPU kernels", "description": "v1",
                "source": "feed", "published": None}
        self.assertEqual(self.store.save([item], "2026-11-08T00:00:00+00:00"), 1)
        self.assertEqual(self.store.save([{**item, "description": "v2"}],
                                         "2026-11-09T00:00:00+00:00"), 0)
        self.store.close()
        self.store = Store(self.path)
        row, = self.store.search("GPU")
        self.assertEqual(row["description"], "v2")
        self.assertEqual(row["first_seen"], "2026-11-08T00:00:00+00:00")
        self.assertEqual(row["last_seen"], "2026-11-09T00:00:00+00:00")
        self.assertEqual(self.store.search("%"), [])
        self.assertEqual(self.store.search("' OR 1=1 --"), [])

    def test_bad_configuration_is_rejected_before_database_creation(self):
        config = Path(self.temporary.name) / "bad.json"
        config.write_text(json.dumps({"sources": [{"name": "bad", "kind": "shell"}]}))
        db = Path(self.temporary.name) / "must-not-exist.sqlite3"
        with self.assertRaisesRegex(ValueError, "kind"):
            main(["collect", "--config", str(config), "--db", str(db)])
        self.assertFalse(db.exists())


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requests = []
        cls.put_requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_PUT(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                HTTPTests.put_requests.append(body)
                payload = (FIXTURES / "modelscope.json").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                HTTPTests.requests.append((self.path, dict(self.headers)))
                status = 200
                headers = {}
                body = (FIXTURES / "rss.xml").read_bytes()
                if self.path == "/cached":
                    headers["ETag"] = '"v1"'
                    if self.headers.get("If-None-Match") == '"v1"':
                        status, body = 304, b""
                elif self.path == "/retry":
                    if sum(path == "/retry" for path, _ in HTTPTests.requests) == 1:
                        status, body = 429, b"limited"
                        headers["Retry-After"] = "0"
                elif self.path == "/blocked":
                    status, body = 403, b"denied"
                elif self.path == "/later":
                    status, body = 429, b"limited"
                    headers["Retry-After"] = "120"
                elif self.path == "/bad":
                    body = b"<html><body>Not a feed</body></html>"
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:" + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.requests.clear()
        self.put_requests.clear()
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.store = Store(self.directory / "items.sqlite3")
        self.client = Client(self.store, interval=0)

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def test_conditional_request_reuses_cached_body_on_304(self):
        first = self.client.fetch(self.base + "/cached")
        second = self.client.fetch(self.base + "/cached")
        self.assertEqual(first, second)
        self.assertEqual(self.requests[1][1]["If-None-Match"], '"v1"')

    def test_429_retries_after_server_delay(self):
        self.assertIn(b"<rss", self.client.fetch(self.base + "/retry"))
        self.assertEqual(len(self.requests), 2)

    def test_403_is_not_retried(self):
        with self.assertRaisesRegex(FetchError, "403"):
            self.client.fetch(self.base + "/blocked")
        self.assertEqual(len(self.requests), 1)

    def test_long_retry_after_skips_source(self):
        with self.assertRaisesRegex(FetchError, "120"):
            self.client.fetch(self.base + "/later")
        self.assertEqual(len(self.requests), 1)

    def test_one_bad_feed_does_not_discard_other_results(self):
        sources = [{"name": "bad", "kind": "rss", "url": self.base + "/bad"},
                   {"name": "good", "kind": "rss", "url": self.base + "/good"}]
        with contextlib.redirect_stderr(io.StringIO()):
            report = collect(sources, 8760, self.store, self.client)
        self.assertEqual([source["ok"] for source in report["sources"]], [False, True])
        self.assertTrue(any(item["title"] == "Without a date" for item in report["items"]))
        self.assertIn("bad", markdown(report))

    def test_json_query_sends_configured_put_body(self):
        config = self.directory / "modelscope.json"
        config.write_text(json.dumps({"window_hours": 8760, "sources": [{
            "name": "models", "kind": "json", "url": self.base + "/models",
            "method": "PUT", "body": {"Path": "ExampleOrg", "PageNumber": 1, "PageSize": 20},
            "items_path": "Data.Models", "fields": {
                "title": "Name", "url": "https://modelscope.ai/models/{Path}/{Name}",
                "published": "LastUpdatedTime",
            },
        }]}))
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            status = main(["collect", "--config", str(config), "--db", str(self.directory / "models.sqlite3"),
                           "--output-dir", str(self.directory / "output")])
        self.assertEqual(status, 0)
        self.assertEqual(self.put_requests, [{"Path": "ExampleOrg", "PageNumber": 1, "PageSize": 20}])
        row, = json.loads(stdout.getvalue())["items"]
        self.assertEqual(row["published"], "2026-11-09T02:00:00+00:00")

    def test_cli_collect_repeat_and_local_search(self):
        config = self.directory / "sources.json"
        config.write_text(json.dumps({"window_hours": 8760, "sources": [
            {"name": "local fixture", "kind": "rss", "url": self.base + "/cached"}
        ]}))
        args = [sys.executable, "-m", "find_new", "collect", "--config", str(config),
                "--db", str(self.directory / "cli.sqlite3"),
                "--output-dir", str(self.directory / "output")]
        first = subprocess.run(args, capture_output=True, text=True, check=True)
        self.assertGreater(json.loads(first.stdout)["new_count"], 0)
        second = subprocess.run(args, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(second.stdout)["new_count"], 0)
        report = json.loads((self.directory / "output/latest.json").read_text())
        self.assertEqual(report["count"], len(report["items"]))
        self.assertTrue((self.directory / "output/latest.md").is_file())
        requests_before = len(self.requests)
        saved = subprocess.run([sys.executable, "-m", "find_new", "list", "--query", "GPU",
                                "--db", str(self.directory / "cli.sqlite3")],
                               capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(saved.stdout)["items"][0]["title"], "Inference & GPU update")
        self.assertEqual(len(self.requests), requests_before)

    def test_all_sources_failing_returns_nonzero_and_writes_report(self):
        config = self.directory / "bad.json"
        config.write_text(json.dumps({"sources": [
            {"name": "blocked", "kind": "rss", "url": self.base + "/blocked"}
        ]}))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            status = main(["collect", "--config", str(config), "--db", str(self.directory / "bad.sqlite3"),
                           "--output-dir", str(self.directory / "output")])
        self.assertEqual(status, 1)
        report = json.loads((self.directory / "output/latest.json").read_text())
        self.assertFalse(report["sources"][0]["ok"])


if __name__ == "__main__":
    unittest.main()
