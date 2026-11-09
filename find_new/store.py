"""Local collection history and conditional HTTP cache."""

import sqlite3


class Store:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS items (
                url TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                published TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS http_cache (
                url TEXT PRIMARY KEY,
                etag TEXT,
                modified TEXT,
                body BLOB NOT NULL
            );
        """)

    def close(self):
        self.connection.close()

    def get_cache(self, url):
        return self.connection.execute("SELECT * FROM http_cache WHERE url = ?", (url,)).fetchone()

    def put_cache(self, url, etag, modified, body):
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO http_cache VALUES (?, ?, ?, ?)",
                (url, etag, modified, body),
            )

    def save(self, items, now):
        new = 0
        with self.connection:
            for item in items:
                cursor = self.connection.execute(
                    """INSERT OR IGNORE INTO items
                       (url, source, title, description, published, first_seen, last_seen)
                       VALUES (:url, :source, :title, :description, :published, :now, :now)""",
                    {**item, "now": now},
                )
                new += cursor.rowcount
                self.connection.execute(
                    """UPDATE items SET title = :title, description = :description,
                       published = COALESCE(:published, published), last_seen = :now
                       WHERE url = :url""", {**item, "now": now},
                )
        return new

    def search(self, query="", limit=50):
        # Treat user search text literally; bind values instead of constructing SQL.
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = self.connection.execute(
            """SELECT * FROM items
               WHERE title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\'
                  OR source LIKE ? ESCAPE '\\'
               ORDER BY COALESCE(published, first_seen) DESC, url LIMIT ?""",
            (pattern, pattern, pattern, limit),
        )
        return [dict(row) for row in rows]
