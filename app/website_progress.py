"""Short-lived, validated website pages for restarting an interrupted search.

This is separate from the user's article cache: partial runs never replace that
cache or move its last-successful-refresh date. Every restart checks a fresh first
page before reusing any saved pages. Changed results invalidate the whole run.
"""
import hashlib
import json
import sqlite3
import time
from pathlib import Path


class WebsiteProgress:
    lifetime = 6 * 60 * 60

    def __init__(self, path, query_url):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.key = hashlib.sha256(query_url.encode('utf8')).hexdigest()
        with self.connect() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS website_pages '
                '(job TEXT, url TEXT, head TEXT, saved REAL, html TEXT, PRIMARY KEY(job,url))')
            connection.execute('DELETE FROM website_pages WHERE saved<?', (time.time()-self.lifetime,))
        self.head = None

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def check_head(self, rows, next_url, total):
        value = json.dumps([rows, next_url, total], sort_keys=True, ensure_ascii=False)
        self.head = hashlib.sha256(value.encode('utf8')).hexdigest()
        with self.connect() as connection:
            connection.execute('DELETE FROM website_pages WHERE job=? AND head<>?', (self.key, self.head))

    def read(self, url):
        if self.head is None:
            return None
        with self.connect() as connection:
            row = connection.execute('SELECT html FROM website_pages WHERE job=? AND url=? '
                'AND head=? AND saved>=?', (self.key, url, self.head, time.time()-self.lifetime)).fetchone()
        return row[0] if row else None

    def save(self, url, html):
        with self.connect() as connection:
            connection.execute('INSERT OR REPLACE INTO website_pages VALUES (?,?,?,?,?)',
                (self.key, url, self.head, time.time(), html))

    def discard(self):
        with self.connect() as connection:
            connection.execute('DELETE FROM website_pages WHERE job=?', (self.key,))
