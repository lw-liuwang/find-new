"""Small HTTP client with caching, host pacing and bounded retries."""

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .feeds import parse_date

USER_AGENT = "find-new/0.1 (+https://github.com/lw-liuwang/find-new)"
MAX_BYTES = 5 * 1024 * 1024


class FetchError(Exception):
    pass


class Client:
    def __init__(self, store, *, timeout=20, interval=2, max_retry_wait=60):
        self.store = store
        self.timeout = timeout
        self.interval = interval
        self.max_retry_wait = max_retry_wait
        self.last_request = {}

    def fetch(self, url, *, method="GET", body=None):
        payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode() if body is not None else None
        # Query APIs may use PUT/POST; their different query bodies must not share a cache entry.
        cache_key = url if method == "GET" else method + " " + url + " " + (payload or b"").decode()
        cache = self.store.get_cache(cache_key)
        headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/json, application/xml, text/xml, */*"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if cache and method == "GET":
            if cache["etag"]:
                headers["If-None-Match"] = cache["etag"]
            if cache["modified"]:
                headers["If-Modified-Since"] = cache["modified"]
        host = urlsplit(url).netloc
        for attempt in range(2):
            remaining = self.interval - (time.monotonic() - self.last_request.get(host, float("-inf")))
            if remaining > 0:
                time.sleep(remaining)
            self.last_request[host] = time.monotonic()
            try:
                request = urllib.request.Request(url, headers=headers, method=method, data=payload)
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = response.read(MAX_BYTES + 1)
                    if len(body) > MAX_BYTES:
                        raise FetchError("响应超过 5 MiB，停止下载")
                    self.store.put_cache(cache_key, response.headers.get("ETag"),
                                         response.headers.get("Last-Modified"), body)
                    return body
            except urllib.error.HTTPError as error:
                if error.code == 304 and cache:
                    error.close()
                    return cache["body"]
                status = error.code
                retry_after = error.headers.get("Retry-After")
                error.close()
                if status == 403:
                    raise FetchError("HTTP 403：访问被拒，本次不重试") from None
                if attempt or status != 429 and status < 500:
                    raise FetchError(f"HTTP {status}") from None
                delay = 10 if status == 429 else 3
                if retry_after:
                    if retry_after.strip().isdigit():
                        delay = int(retry_after)
                    else:
                        date = parse_date(retry_after)
                        if date:
                            delay = max(0, (date - datetime.now(timezone.utc)).total_seconds())
                if delay > self.max_retry_wait:
                    raise FetchError(f"HTTP {status}：服务要求等待 {delay:.0f} 秒，本次跳过") from None
                time.sleep(delay)
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                if attempt:
                    raise FetchError(f"网络请求失败：{error}") from None
                time.sleep(3)
        raise FetchError("请求失败")
