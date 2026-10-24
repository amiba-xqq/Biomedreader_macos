from __future__ import annotations

import json
import sqlite3
import sys
import uuid
import re
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from article_types import normalize_types
from source_modes import cache_mode
from pdf_library import default_paper_directory, legacy_paper_directory, copy_pdf


DEFAULT_JOURNALS = [
    ("The New England Journal of Medicine", "0028-4793", ""),
    ("The Lancet", "0140-6736", ""),
    ("JAMA", "0098-7484", ""),
    ("BMJ", "0959-8138", ""),
    ("Cell", "0092-8674", ""),
    ("Cancer Cell", "1535-6108", ""),
    ("Immunity", "1074-7613", ""),
    ("Neuron", "0896-6273", ""),
    ("Molecular Cell", "1097-2765", ""),
    ("Cell Metabolism", "1550-4131", ""),
    ("Cell Stem Cell", "1934-5909", ""),
    ("Nature Medicine", "1078-8956", ""),
    ("Nature Genetics", "1061-4036", ""),
    ("Nature Neuroscience", "1097-6256", ""),
    ("Nature Immunology", "1529-2908", ""),
    ("Nature Biotechnology", "1087-0156", ""),
    ("Science Translational Medicine", "1946-6234", ""),
    ("Circulation", "0009-7322", ""),
    ("Blood", "0006-4971", ""),
    ("Journal of Clinical Investigation", "0021-9738", ""),
]




def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """SQLite persistence layer shared by all pages of the desktop application.

    Connections are short lived so the GUI thread and background search thread never share the
    same sqlite3 connection object. WAL mode lets the UI continue reading cached articles while a
    completed network refresh is being committed.
    """

    def __init__(self, path: Path, paper_directory: Path | None = None):
        self.path = Path(path)
        self.paper_directory = Path(paper_directory) if paper_directory is not None else default_paper_directory(self.path.parent)
        self._metrics = None
        self._metric_lock=threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()
        if paper_directory is None:
            self.migrate_legacy_pdfs()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS profile_hidden_articles (
                    profile_id INTEGER NOT NULL REFERENCES search_profiles(id) ON DELETE CASCADE,
                    article_key TEXT NOT NULL,doi TEXT NOT NULL DEFAULT '',pmid TEXT NOT NULL DEFAULT '',
                    hidden_at TEXT NOT NULL,PRIMARY KEY(profile_id,article_key)
                );

                CREATE TABLE IF NOT EXISTS card_positions (
                    profile_id INTEGER NOT NULL REFERENCES search_profiles(id) ON DELETE CASCADE,
                    source_mode TEXT NOT NULL, page INTEGER NOT NULL, scroll_y INTEGER NOT NULL,
                    sort_mode TEXT NOT NULL,
                    PRIMARY KEY(profile_id,source_mode)
                );
                CREATE TABLE IF NOT EXISTS doi_visits (
                    profile_id INTEGER NOT NULL REFERENCES search_profiles(id) ON DELETE CASCADE,
                    source_mode TEXT NOT NULL, article_key TEXT NOT NULL REFERENCES articles(article_key),
                    generation TEXT NOT NULL,
                    PRIMARY KEY(profile_id,source_mode,article_key)
                );
                CREATE TABLE IF NOT EXISTS shared_article_metrics (
                    article_key TEXT PRIMARY KEY REFERENCES articles(article_key) ON DELETE CASCADE,
                    jif REAL, jif_year INTEGER, jif_source TEXT
                );
                CREATE TABLE IF NOT EXISTS journal_scopes (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    issns_json TEXT NOT NULL,
                    is_builtin INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS profile_results (
                    profile_id INTEGER NOT NULL REFERENCES search_profiles(id) ON DELETE CASCADE,
                    source_mode TEXT NOT NULL,
                    article_key TEXT NOT NULL REFERENCES articles(article_key),
                    article_json TEXT NOT NULL,
                    relevance_score REAL NOT NULL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY(profile_id,source_mode,article_key)
                );
                CREATE TABLE IF NOT EXISTS profile_cache_states (
                    profile_id INTEGER NOT NULL REFERENCES search_profiles(id) ON DELETE CASCADE,
                    source_mode TEXT NOT NULL,
                    refreshed_at TEXT NOT NULL,
                    PRIMARY KEY(profile_id,source_mode)
                );
                CREATE TABLE IF NOT EXISTS favorite_group_articles (
                    group_id INTEGER NOT NULL REFERENCES favorite_groups(id) ON DELETE CASCADE,
                    article_key TEXT NOT NULL REFERENCES articles(article_key),
                    PRIMARY KEY(group_id,article_key)
                );
                CREATE TABLE IF NOT EXISTS suppressed_profile_groups (profile_id INTEGER PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS article_pdfs (
                    article_key TEXT PRIMARY KEY REFERENCES articles(article_key),
                    filename TEXT NOT NULL,
                    linked_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS journals (
                    issn TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '生物医学',
                    jif REAL,
                    jif_year INTEGER,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS search_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    query_expression TEXT NOT NULL,
                    journal_issns_json TEXT NOT NULL,
                    minimum_jif REAL NOT NULL DEFAULT 0,
                    maximum_results INTEGER NOT NULL DEFAULT 200,
                    sort_mode TEXT NOT NULL DEFAULT '综合最佳',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_refreshed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS articles (
                    article_key TEXT PRIMARY KEY,
                    doi TEXT,
                    pmid TEXT,
                    pmcid TEXT,
                    title TEXT NOT NULL,
                    abstract TEXT,
                    authors TEXT,
                    journal_title TEXT,
                    issn TEXT,
                    publication_date TEXT,
                    publication_year INTEGER,
                    cited_by_count INTEGER NOT NULL DEFAULT 0,
                    is_open_access INTEGER NOT NULL DEFAULT 0,
                    landing_page_url TEXT,
                    graphical_abstract_url TEXT,
                    source_name TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS profile_articles (
                    profile_id INTEGER NOT NULL,
                    article_key TEXT NOT NULL,
                    relevance_score REAL NOT NULL DEFAULT 0,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (profile_id, article_key),
                    FOREIGN KEY (profile_id) REFERENCES search_profiles(id) ON DELETE CASCADE,
                    FOREIGN KEY (article_key) REFERENCES articles(article_key) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS favorites (
                    article_key TEXT PRIMARY KEY,
                    is_favorite INTEGER NOT NULL DEFAULT 1,
                    tags TEXT NOT NULL DEFAULT '',
                    personal_note TEXT NOT NULL DEFAULT '',
                    saved_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (article_key) REFERENCES articles(article_key) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS favorite_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    keywords TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_articles_doi ON articles(doi);
                CREATE INDEX IF NOT EXISTS idx_articles_pmid ON articles(pmid);
                CREATE INDEX IF NOT EXISTS idx_articles_date ON articles(publication_date);
                CREATE INDEX IF NOT EXISTS idx_profile_articles_profile ON profile_articles(profile_id);
                """
            )

            now = utc_now()
            # Additive migration preserves existing profiles, favorites and per-journal choices.
            for table, column, declaration in (
                ("journals", "aliases", "TEXT NOT NULL DEFAULT ''"),
                ("journals", "source", "TEXT NOT NULL DEFAULT '用户/原始目录'"),
                ("search_profiles", "year_from", "INTEGER"),
                ("search_profiles", "year_to", "INTEGER"),
                ("search_profiles", "scope_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("search_profiles", "article_type", "TEXT NOT NULL DEFAULT 'all'"),
                ("search_profiles", "view_mode", "TEXT NOT NULL DEFAULT 'table'"),
                ("search_profiles", "display_order", "INTEGER NOT NULL DEFAULT 0"),
                ("search_profiles", "card_width", "INTEGER NOT NULL DEFAULT 0"),
                ("search_profiles", "pubmed_maximum_results", "TEXT"),
                ("search_profiles", "biorxiv_maximum_results", "TEXT"),
                ("search_profiles", "search_sources", "TEXT"),
                ("search_profiles", "display_cache_mode", "TEXT"),
                ("articles", "publication_types_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("articles", "volume", "TEXT NOT NULL DEFAULT ''"),
                ("articles", "issue", "TEXT NOT NULL DEFAULT ''"),
                ("articles", "pages", "TEXT NOT NULL DEFAULT ''"),
                ("articles", "bibliography_checked_at", "TEXT NOT NULL DEFAULT ''"),
                ("favorite_groups", "profile_id", "INTEGER"),
                ("article_pdfs", "storage_location", "TEXT NOT NULL DEFAULT 'legacy'"),
            ):
                columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                if column not in columns:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
                    if column in ('pubmed_maximum_results','biorxiv_maximum_results'):
                        connection.execute(f'UPDATE search_profiles SET {column}=CAST(maximum_results AS TEXT)')
                    if table == 'search_profiles' and column == 'display_order':
                        for index, row in enumerate(connection.execute('SELECT id FROM search_profiles ORDER BY updated_at DESC,id').fetchall()):
                            connection.execute('UPDATE search_profiles SET display_order=? WHERE id=?', (index, row['id']))
            snapshot = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets/nlm_journals.json"
            connection.execute('CREATE INDEX IF NOT EXISTS idx_profile_results_article ON profile_results(article_key)')
            seeded = connection.execute("SELECT value FROM settings WHERE key='nlm_snapshot_version'").fetchone()
            if snapshot.exists() and (not seeded or seeded[0] != "2026-10-12"):
                for row in json.loads(snapshot.read_text(encoding="utf-8")):
                    connection.execute(
                        "INSERT INTO journals(issn,title,category,aliases,source,updated_at) "
                        "VALUES(?,?,?,?,?,?) ON CONFLICT(issn) DO UPDATE SET "
                        "aliases=CASE WHEN journals.aliases='' THEN excluded.aliases ELSE journals.aliases END",
                        (row["issn"],row["title"],row["category"],row["aliases"],row["source"],now),
                    )
                connection.execute("INSERT INTO settings(key,value) VALUES('nlm_snapshot_version','2026-10-12') "
                                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
            for title, issn, category in DEFAULT_JOURNALS:
                connection.execute(
                    """
                    INSERT INTO journals (issn, title, category, enabled, updated_at)
                    VALUES (?, ?, ?, 1, ?)
                    ON CONFLICT(issn) DO NOTHING
                    """,
                    (issn, title, category, now),
                )

            # Bundled ranges are the user's exact ISSN lists, not inferred JIF values.
            scope_asset = snapshot.parent / "journal_scopes.json"
            original_if_asset=snapshot.parent/'if_scope_union_2025.json'
            original_if=json.loads(original_if_asset.read_text(encoding='utf8'))['issns'] if original_if_asset.exists() else []
            if scope_asset.exists():
                known = {alias for row in connection.execute("SELECT issn,aliases FROM journals")
                         for alias in {row["issn"], *(row["aliases"] or "").split(";")}}
                for scope in json.loads(scope_asset.read_text(encoding="utf-8")):
                    connection.execute(
                        "INSERT INTO journal_scopes VALUES(?,?,?,1,?,?) ON CONFLICT(id) DO NOTHING",
                        (scope["id"], scope["name"], json.dumps(scope["issns"]), now, now))
                    for issn in scope["issns"]:
                        if issn not in known:
                            connection.execute(
                                "INSERT INTO journals(issn,title,category,source,updated_at) "
                                "VALUES(?,?,'待分类','用户内置 ISSN 目录',?) ON CONFLICT(issn) DO NOTHING",
                                (issn, f"待补全期刊名称（{issn}）", now))
                # Keep the master catalogue intact when an IF directory drops a
                # missing/out-of-range journal. Only these three memberships change.
                for issn in original_if:
                    if issn not in known:
                        connection.execute("INSERT INTO journals(issn,title,category,source,updated_at) VALUES(?,?,'待分类','用户内置 ISSN 目录',?) ON CONFLICT(issn) DO NOTHING",
                            (issn,f'待补全期刊名称（{issn}）',now))
            metrics_asset = snapshot.parent / "nc_journals.json"
            if metrics_asset.exists() and not connection.execute("SELECT 1 FROM settings WHERE key='nc_metrics_seeded_150'").fetchone():
                for row in json.loads(metrics_asset.read_text(encoding="utf-8")):
                    connection.execute("UPDATE journals SET jif=?,jif_year=?,source=?,updated_at=? WHERE issn=?",
                        (row["jif"], None, "NC精选 · 用户表格2026发布版（指标年度未核验） · " + row["source"], now, row["issn"]))
                connection.execute("INSERT INTO settings VALUES('nc_metrics_seeded_150','1')")
            # Versioned seeding applies this requested update once, preserving subsequent edits.
            builtin_asset = snapshot.parent / "ablesci_jif_2025.json"
            if builtin_asset.exists() and not connection.execute("SELECT 1 FROM settings WHERE key='builtin_metrics_160'").fetchone():
                builtin_issns = {
                    i for scope in json.loads(scope_asset.read_text(encoding="utf-8")) for i in scope["issns"]}
                for row in json.loads(builtin_asset.read_text(encoding="utf-8")):
                    if row["record"] and row["record"]["jif"] is not None:
                        metric = row["record"]
                        connection.execute("UPDATE journals SET jif=?,jif_year=2025,source=?,updated_at=? WHERE issn=?",
                            (metric["jif"], "科研通最新值 · 2026发布版 · 2025指标年度（用户指定） · " + metric["source"], now, row["canonical_issn"]))
                # This includes NC精选 and aliases. Missing values remain empty, never fabricated.
                for row in connection.execute("SELECT issn,aliases FROM journals").fetchall():
                    if builtin_issns.intersection({row["issn"], *(row["aliases"] or "").split(";")}):
                        connection.execute("UPDATE journals SET jif_year=2025 WHERE issn=?", (row["issn"],))
                connection.execute("INSERT INTO settings VALUES('builtin_metrics_160','1')")
            # Extend the fixed NC scope in existing installations without touching custom scopes.
            if scope_asset.exists() and not connection.execute("SELECT 1 FROM settings WHERE key='builtin_catalogue_170'").fetchone():
                bundled_scopes = json.loads(scope_asset.read_text(encoding="utf-8"))
                nc_scope = next(s for s in bundled_scopes if s['id'] == 'builtin_nc_selected')
                previous = connection.execute("SELECT issns_json FROM journal_scopes WHERE id=?", (nc_scope['id'],)).fetchone()
                merged = list(dict.fromkeys((json.loads(previous[0]) if previous else []) + nc_scope['issns']))
                connection.execute("UPDATE journal_scopes SET issns_json=?,updated_at=? WHERE id=? AND is_builtin=1",
                                   (json.dumps(merged), now, nc_scope['id']))
                if builtin_asset.exists():
                    for row in json.loads(builtin_asset.read_text(encoding="utf-8")):
                        metric = row.get('record')
                        if metric and metric['jif'] is not None:
                            connection.execute("UPDATE journals SET jif=?,jif_year=2025,source=?,updated_at=? WHERE issn=? AND jif IS NULL",
                                (metric['jif'], f"科研通最新值 · {metric.get('release_year') or '未知'}发布版 · 2025指标年度（用户指定） · {metric['source']}", now, row['canonical_issn']))
                members = {i for s in bundled_scopes for i in s['issns']}
                for journal in connection.execute("SELECT issn,aliases FROM journals").fetchall():
                    if members.intersection({journal['issn'], *(journal['aliases'] or '').split(';')}):
                        connection.execute("UPDATE journals SET jif_year=2025 WHERE issn=?", (journal['issn'],))
                connection.execute("INSERT INTO settings VALUES('builtin_catalogue_170','1')")
            # Preserve the exact scope of old profiles, together with their offline results.
            for profile in connection.execute("SELECT * FROM search_profiles WHERE scope_ids_json='[]'").fetchall():
                scope_id = "legacy_" + str(profile["id"])
                name = "原方案目录 · " + profile["name"]
                connection.execute(
                    "INSERT INTO journal_scopes VALUES(?,?,?,0,?,?) ON CONFLICT(id) DO NOTHING",
                    (scope_id, name, profile["journal_issns_json"], now, now))
                connection.execute("UPDATE search_profiles SET scope_ids_json=? WHERE id=?",
                                   (json.dumps([scope_id]), profile["id"]))

            # Remove obsolete credentials from existing databases as well as the UI.
            connection.execute("DELETE FROM settings WHERE key IN ('clarivate_api_key','jcr_year','ncbi_api_key')")
            old_default=connection.execute("SELECT value FROM settings WHERE key='default_maximum_results'").fetchone()
            defaults = {
                "keyword_default_view": "cards_compact",
                "search_sources": "both",
                "biorxiv_search_method": "metadata",
                "biorxiv_website_scope": "title_abstract",
                "preprint_servers": "biorxiv",
                "default_maximum_results": "200",
                "default_pubmed_maximum_results": old_default[0] if old_default else '200',
                "default_biorxiv_maximum_results": old_default[0] if old_default else '200',
                "biorxiv_recent_years": "2",
            }
            for key, value in defaults.items():
                connection.execute(
                    "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
                    (key, value),
                )
            # Upgrade each profile from its own last successful snapshot, not whatever
            # global selector happened to be left active when the program was closed.
            old_source=connection.execute("SELECT value FROM settings WHERE key='search_sources'").fetchone()
            for row in connection.execute('SELECT id FROM search_profiles WHERE search_sources IS NULL OR display_cache_mode IS NULL').fetchall():
                latest=connection.execute('SELECT source_mode FROM profile_cache_states WHERE profile_id=? ORDER BY refreshed_at DESC,rowid DESC LIMIT 1',(row['id'],)).fetchone()
                mode=latest[0] if latest else None
                source='pubmed' if mode and mode.startswith('pubmed') else 'biorxiv' if mode and mode.startswith('biorxiv') else 'both' if mode else (old_source[0] if old_source and old_source[0] in ('pubmed','biorxiv','both') else 'both')
                connection.execute('UPDATE search_profiles SET search_sources=COALESCE(search_sources,?),display_cache_mode=COALESCE(display_cache_mode,?) WHERE id=?',(source,mode,row['id']))
            connection.execute("DELETE FROM settings WHERE key IN ('search_sources','default_maximum_results','default_pubmed_maximum_results','default_biorxiv_maximum_results','biorxiv_search_method')")
            # Recover journal names in old Europe PMC cache rows using exact ISSN/alias matches.
            repairs = {alias: row["title"] for row in connection.execute("SELECT title,issn,aliases FROM journals")
                       for alias in (row["aliases"] or row["issn"]).split(";")}
            for row in connection.execute("SELECT article_key,issn FROM articles WHERE COALESCE(journal_title,'')=''").fetchall():
                if row["issn"] in repairs:
                    connection.execute("UPDATE articles SET journal_title=? WHERE article_key=?",
                                       (repairs[row["issn"]],row["article_key"]))
            connection.execute("UPDATE articles SET publication_year=CAST(substr(publication_date,1,4) AS INTEGER) "
                               "WHERE publication_date GLOB '[12][0-9][0-9][0-9]-*' AND "
                               "COALESCE(publication_year,0) != CAST(substr(publication_date,1,4) AS INTEGER)")
            connection.execute("UPDATE journals SET category='' WHERE category<>''")
            if not connection.execute("SELECT 1 FROM settings WHERE key='favorite_origins_150'").fetchone():
                # Pre-1.5 links record search provenance, not actual save provenance. Preserve
                # existing favorites in those historical groups, but discard non-favorite links.
                # All new saves record their exact profile; no text inference is performed.
                connection.execute("DELETE FROM favorite_group_articles WHERE article_key NOT IN "
                                   "(SELECT article_key FROM favorites WHERE is_favorite=1)")
                connection.execute("INSERT INTO settings VALUES('favorite_origins_150','1')")
            # Preserve pre-1.4 mixed caches before a single-source refresh replaces its legacy links.
            migrated = connection.execute("SELECT 1 FROM settings WHERE key='source_snapshots_migrated'").fetchone()
            if not migrated:
                for profile in connection.execute("SELECT id,last_refreshed_at FROM search_profiles").fetchall():
                    old_rows = connection.execute("SELECT a.*,pa.relevance_score,pa.fetched_at FROM articles a "
                        "JOIN profile_articles pa ON pa.article_key=a.article_key WHERE pa.profile_id=?", (profile["id"],)).fetchall()
                    if old_rows or profile["last_refreshed_at"]:
                        for row in old_rows:
                            article = dict(row)
                            article["publication_types"] = json.loads(article["publication_types_json"])
                            connection.execute("INSERT OR IGNORE INTO profile_results VALUES(?,?,?,?,?,?)",
                                (profile["id"], "both", article["article_key"], json.dumps(article), row["relevance_score"], row["fetched_at"]))
                        connection.execute("INSERT OR IGNORE INTO profile_cache_states VALUES(?,?,?)",
                                           (profile["id"], "both", profile["last_refreshed_at"] or utc_now()))
                connection.execute("INSERT INTO settings VALUES('source_snapshots_migrated','1')")

            connection.execute("DELETE FROM settings WHERE key='contact_email'")
            connection.execute("CREATE TABLE IF NOT EXISTS refresh_checkpoints ("
                               "profile_id INTEGER NOT NULL REFERENCES search_profiles(id) ON DELETE CASCADE,"
                               "source_mode TEXT NOT NULL,signature TEXT NOT NULL,started_at TEXT NOT NULL,"
                               "PRIMARY KEY(profile_id,source_mode))")
            if 'completed_at' not in {row[1] for row in connection.execute('PRAGMA table_info(refresh_checkpoints)')}:
                connection.execute('ALTER TABLE refresh_checkpoints ADD COLUMN completed_at TEXT')
            connection.execute('CREATE TABLE IF NOT EXISTS source_refresh_checkpoints ('
                'profile_id INTEGER NOT NULL REFERENCES search_profiles(id) ON DELETE CASCADE,'
                'source_mode TEXT NOT NULL,source TEXT NOT NULL,signature TEXT NOT NULL,'
                'started_at TEXT NOT NULL,completed_at TEXT,PRIMARY KEY(profile_id,source_mode,source))')
            connection.execute("DELETE FROM settings WHERE key LIKE 'ablesci_%'")
            # Archive the old Europe PMC modes once, keeping favorites/PDFs intact.
            # Never relabel an old mixed cache as a new PubMed + bioRxiv response.
            if not connection.execute("SELECT 1 FROM settings WHERE key='biorxiv_sources_180'").fetchone():
                for table in ("profile_results", "profile_cache_states"):
                    connection.execute(f"UPDATE {table} SET source_mode='legacy_europe_both' WHERE source_mode='both'")
                    connection.execute(f"UPDATE {table} SET source_mode='legacy_europe_pmc' WHERE source_mode='europe_pmc'")
                connection.execute("UPDATE settings SET value='biorxiv' WHERE key='search_sources' AND value='europe_pmc'")
                connection.execute("INSERT INTO settings VALUES('biorxiv_sources_180','1')")

            # Archive original years before selecting the displayed year. Never
            # relabel a 2025 metric as 2026 merely because the calendar changed.
            self._snapshot_metrics(connection)
            supplement=Path(getattr(sys,'_MEIPASS',Path(__file__).parent))/'assets/user_jif_2023_2025.json'
            if supplement.exists() and not connection.execute("SELECT 1 FROM settings WHERE key='user_jif_2023_2025_1913'").fetchone():
                from jif_supplement import apply_supplement
                report=apply_supplement(connection,json.loads(supplement.read_text(encoding='utf8')),utc_now())
                connection.execute("INSERT INTO settings VALUES('user_jif_2023_2025_1913',?)",(json.dumps(report,ensure_ascii=False),))
            if original_if and not connection.execute("SELECT 1 FROM settings WHERE key='if_scopes_2025_1916'").fetchone():
                from if_scopes import regroup_if_scopes,SCOPE_IDS
                members=set(original_if)
                for identifier in SCOPE_IDS:
                    row=connection.execute('SELECT issns_json FROM journal_scopes WHERE id=? AND is_builtin=1',(identifier,)).fetchone()
                    if row:members.update(json.loads(row[0]))
                report=regroup_if_scopes(connection,members,now)
                report['input_identifiers']=report['original_identifiers']
                report['original_identifiers']=len(set(original_if))
                connection.execute("INSERT INTO settings VALUES('if_scopes_2025_1916',?)",(json.dumps(report,ensure_ascii=False),))
            selected=connection.execute("SELECT value FROM settings WHERE key='jif_year'").fetchone()
            auto=connection.execute("SELECT value FROM settings WHERE key='jif_year_auto'").fetchone()
            try:year=int(selected[0]) if selected and auto and auto[0]=='0' else datetime.now().year-1
            except (TypeError,ValueError):year=datetime.now().year-1
            year=max(2010,min(datetime.now().year,year))
            connection.execute("INSERT INTO settings VALUES('jif_year',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(year),))
            self._activate_jif_year(connection,year)

    def _snapshot_metrics(self,connection):
        connection.execute('CREATE TABLE IF NOT EXISTS journal_metrics '
            '(issn TEXT NOT NULL,year INTEGER NOT NULL,jif REAL,source TEXT,updated_at TEXT,PRIMARY KEY(issn,year))')
        connection.execute('INSERT INTO journal_metrics SELECT issn,jif_year,jif,source,updated_at FROM journals '
            'WHERE jif IS NOT NULL AND jif_year BETWEEN 2010 AND ? '
            'ON CONFLICT(issn,year) DO UPDATE SET jif=excluded.jif,source=excluded.source,updated_at=excluded.updated_at',
            (datetime.now().year,))

    def _activate_jif_year(self,connection,year):
        connection.execute('UPDATE journals SET jif=(SELECT m.jif FROM journal_metrics m WHERE m.issn=journals.issn AND m.year=?),'
            'jif_year=?,source=COALESCE((SELECT m.source FROM journal_metrics m WHERE m.issn=journals.issn AND m.year=?),source)',
            (year,year,year))

    def select_jif_year(self,year):
        year=int(year)
        if not 2010<=year<=datetime.now().year:raise ValueError('JIF 年份须在2010年至本机当前年份之间。')
        with self._metric_lock:
            with self.connect() as connection:
                self._snapshot_metrics(connection)
                self._activate_jif_year(connection,year)
                connection.execute("INSERT INTO settings VALUES('jif_year',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(year),))
                connection.execute("INSERT INTO settings VALUES('jif_year_auto','0') ON CONFLICT(key) DO UPDATE SET value='0'")
            self._metrics=None

    def get_settings(self) -> dict[str, str]:
        with self.connect() as connection:
            return {row["key"]: row["value"] for row in connection.execute("SELECT key, value FROM settings")}

    def save_settings(self, values: dict[str, str]) -> None:
        with self.connect() as connection:
            for key, value in values.items():
                connection.execute(
                    "INSERT INTO settings(key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, str(value)),
                )

    def list_journals(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE enabled = 1" if enabled_only else ""
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM journals {where} ORDER BY category, title"
            ).fetchall()
            return [dict(row) for row in rows]

    def save_journals(self, journals: list[dict[str, Any]]) -> None:
        self._metrics = None
        now = utc_now()
        with self.connect() as connection:
            for journal in journals:
                connection.execute(
                    """
                    INSERT INTO journals (issn, title, category, jif, jif_year, enabled, updated_at, aliases, source)
                    VALUES (:issn, :title, :category, :jif, :jif_year, :enabled, :updated_at, :aliases, :source)
                    ON CONFLICT(issn) DO UPDATE SET
                        title = excluded.title,
                        category = excluded.category,
                        jif = excluded.jif,
                        jif_year = excluded.jif_year,
                        enabled = excluded.enabled,
                        aliases = COALESCE(NULLIF(excluded.aliases,''), journals.aliases),
                        source = excluded.source,
                        updated_at = excluded.updated_at
                    """,
                    {"aliases": "", "source": "用户导入", **journal, "category": "", "updated_at": now},
                )
                if journal.get('jif') is None and journal.get('jif_year'):
                    connection.execute('DELETE FROM journal_metrics WHERE issn=? AND year=?',(journal['issn'],journal['jif_year']))
            self._snapshot_metrics(connection)
            year=int(connection.execute("SELECT value FROM settings WHERE key='jif_year'").fetchone()[0])
            self._activate_jif_year(connection,year)

    def list_scopes(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows=[dict(row) for row in connection.execute(
                "SELECT * FROM journal_scopes ORDER BY is_builtin DESC,"
                "CASE id WHEN 'builtin_if_6_10' THEN 0 WHEN 'builtin_if_10_20' THEN 1 "
                "WHEN 'builtin_if_20_500' THEN 2 ELSE 3 END,created_at,id")]
        return self._ordered_cards(rows,'scope_card_order')

    def _ordered_cards(self,rows,key):
        try:order=json.loads(self.get_settings().get(key,'[]'))
        except (ValueError,TypeError):order=[]
        positions={str(identifier):index for index,identifier in enumerate(order)}
        return sorted(rows,key=lambda row:positions.get(str(row['id']),len(positions)))

    def save_card_order(self,kind,identifiers):
        if kind not in ('scope','favorite'):raise ValueError('卡片类型无效。')
        values=[str(value) for value in identifiers]
        if len(values)!=len(set(values)):raise ValueError('卡片顺序包含重复项。')
        valid={str(row['id']) for row in (self.list_scopes() if kind=='scope' else self.list_custom_groups())}
        if kind=='favorite':valid.add('__ungrouped__')
        if not set(values)<=valid:raise ValueError('卡片已发生变化，请刷新后重试。')
        self.save_settings({kind+'_card_order':json.dumps(values)})

    def save_scope(self, name: str, issns: list[str], scope_id: str | None = None) -> str:
        """Custom scopes are live references; changing one affects the next manual refresh only."""
        if not name.strip() or not issns:
            raise ValueError("请填写目录名称并至少选择一本期刊。")
        scope_id = scope_id or uuid.uuid4().hex
        with self.connect() as connection:
            old = connection.execute("SELECT * FROM journal_scopes WHERE id=?", (scope_id,)).fetchone()
            if old and old["is_builtin"]:
                raise ValueError("内置固定目录不能修改；可以复制为自定义目录。")
            connection.execute(
                "INSERT INTO journal_scopes VALUES(?,?,?,0,?,?) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name,issns_json=excluded.issns_json,updated_at=excluded.updated_at",
                (scope_id, name.strip(), json.dumps(sorted(set(issns))), utc_now(), utc_now()))
        return scope_id

    def delete_scope(self, scope_id: str) -> None:
        with self.connect() as connection:
            scope = connection.execute("SELECT is_builtin FROM journal_scopes WHERE id=?", (scope_id,)).fetchone()
            if scope and scope[0]:
                raise ValueError("内置固定目录不能删除。")
            users = [p["name"] for p in connection.execute("SELECT name,scope_ids_json FROM search_profiles")
                     if scope_id in json.loads(p["scope_ids_json"])]
            if users:
                raise ValueError("以下方案正在使用此目录，请先编辑方案：" + "、".join(users))
            connection.execute("DELETE FROM journal_scopes WHERE id=?", (scope_id,))

    def scope_union(self, scope_ids: list[str]) -> list[str]:
        scopes = {s["id"]: s for s in self.list_scopes()}
        if any(ident not in scopes for ident in scope_ids):
            raise ValueError("所选期刊目录已不存在，请重新编辑关键词方案。")
        return sorted({issn for ident in scope_ids for issn in json.loads(scopes[ident]["issns_json"])})

    def profile_issns(self, profile: dict[str, Any]) -> list[str]:
        scope_ids = json.loads(profile.get("scope_ids_json") or "[]")
        return self.scope_union(scope_ids) if scope_ids else json.loads(profile["journal_issns_json"])

    def list_profiles(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                WITH cached AS (SELECT profile_id,article_key FROM profile_results
                                UNION SELECT profile_id,article_key FROM profile_articles),
                counts AS (SELECT profile_id,COUNT(*) AS total FROM cached GROUP BY profile_id)
                SELECT p.*,COALESCE(c.total,0) AS article_count
                FROM search_profiles p LEFT JOIN counts c ON c.profile_id=p.id
                ORDER BY p.display_order, p.id
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def get_profile(self, profile_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM search_profiles WHERE id = ?", (profile_id,)).fetchone()
            return dict(row) if row else None

    def save_profile(self, profile: dict[str, Any]) -> int:
        now = utc_now()
        with self.connect() as connection:
            if profile.get("id"):
                connection.execute(
                    """
                    UPDATE search_profiles SET
                        name = ?, query_expression = ?, journal_issns_json = ?, minimum_jif = ?,
                        maximum_results = ?, sort_mode = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        profile["name"], profile["query_expression"],
                        json.dumps(profile["journal_issns"], ensure_ascii=False),
                        profile["minimum_jif"], str(profile["maximum_results"]),
                        profile["sort_mode"], now, profile["id"],
                    ),
                )
                connection.execute("UPDATE search_profiles SET year_from=?,year_to=?,scope_ids_json=? WHERE id=?",
                                   (profile.get("year_from"), profile.get("year_to"),
                                    json.dumps(profile.get("scope_ids", [])), profile["id"]))
                connection.execute("UPDATE search_profiles SET article_type=? WHERE id=?",
                                   (json.dumps(normalize_types(profile.get("article_type", "all"))), profile["id"]))
                self._rename_profile_group(connection, int(profile["id"]), profile["name"])
                for field in ('pubmed_maximum_results','biorxiv_maximum_results'):
                    if field in profile:
                        connection.execute(f'UPDATE search_profiles SET {field}=? WHERE id=?',(str(profile[field]),profile['id']))
                if 'search_sources' in profile:
                    if profile['search_sources'] not in ('pubmed','biorxiv','both'):raise ValueError('检索来源无效。')
                    connection.execute('UPDATE search_profiles SET search_sources=? WHERE id=?',(profile['search_sources'],profile['id']))
                return int(profile["id"])

            cursor = connection.execute(
                """
                INSERT INTO search_profiles (
                    name, query_expression, journal_issns_json, minimum_jif, maximum_results,
                    sort_mode, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile["name"], profile["query_expression"],
                    json.dumps(profile["journal_issns"], ensure_ascii=False),
                    profile["minimum_jif"], str(profile["maximum_results"]),
                    profile["sort_mode"], now, now,
                ),
            )
            connection.execute("UPDATE search_profiles SET year_from=?,year_to=?,scope_ids_json=? WHERE id=?",
                               (profile.get("year_from"), profile.get("year_to"),
                                json.dumps(profile.get("scope_ids", [])), cursor.lastrowid))
            connection.execute("UPDATE search_profiles SET article_type=? WHERE id=?",
                               (json.dumps(normalize_types(profile.get("article_type", "all"))), cursor.lastrowid))
            connection.execute("UPDATE search_profiles SET display_order=(SELECT COALESCE(MAX(display_order),-1)+1 "
                               "FROM search_profiles WHERE id<>?) WHERE id=?", (cursor.lastrowid, cursor.lastrowid))
            connection.execute('UPDATE search_profiles SET pubmed_maximum_results=?,biorxiv_maximum_results=? WHERE id=?',
                (str(profile.get('pubmed_maximum_results',profile['maximum_results'])),
                 str(profile.get('biorxiv_maximum_results',profile['maximum_results'])),cursor.lastrowid))
            source=profile.get('search_sources','both')
            if source not in ('pubmed','biorxiv','both'):raise ValueError('检索来源无效。')
            connection.execute('UPDATE search_profiles SET search_sources=? WHERE id=?',(source,cursor.lastrowid))
            return int(cursor.lastrowid)

    def profile_cache_mode(self,profile_id):
        """Reading always shows the last successfully committed snapshot."""
        profile=self.get_profile(profile_id)
        if profile and profile.get('display_cache_mode'):return profile['display_cache_mode']
        if profile:
            with self.connect() as connection:
                latest=connection.execute('SELECT source_mode FROM profile_cache_states WHERE profile_id=? ORDER BY refreshed_at DESC,rowid DESC LIMIT 1',(profile_id,)).fetchone()
                if latest:return latest[0]
            return 'pubmed' if profile.get('search_sources')=='pubmed' else 'both'
        return 'both'

    def reorder_profiles(self, identifiers):
        identifiers = list(identifiers)
        with self.connect() as connection:
            current = {row[0] for row in connection.execute('SELECT id FROM search_profiles')}
            if len(identifiers) != len(current) or set(identifiers) != current:
                raise ValueError('关键词方案已变化，请返回首页后重新排序。')
            connection.executemany('UPDATE search_profiles SET display_order=? WHERE id=?', enumerate(identifiers))

    def set_profile_card_width(self, identifier, width):
        width = int(width)
        if width != 0 and not 140 <= width <= 1800:
            raise ValueError('卡片宽度需为 140 至 1800 像素，或 0（自动）。')
        with self.connect() as connection:
            connection.execute('UPDATE search_profiles SET card_width=? WHERE id=?', (width, identifier))

    def reset_profile_card_widths(self):
        with self.connect() as connection:
            connection.execute('UPDATE search_profiles SET card_width=0')

    def delete_profile(self, profile_id: int) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM search_profiles WHERE id = ?", (profile_id,))

    def store_refresh_results(self, profile_id: int, candidates: list[tuple[dict[str, Any], float]], source_mode: str | None = None,
                              refresh_checkpoint: dict | None = None, partial: bool = False,source_checkpoints: dict | None = None) -> None:
        """Commit one completed refresh as a single transaction without deleting old cache rows."""
        now = utc_now()
        source_mode = source_mode or cache_mode(self.get_settings())
        with self.connect() as connection:
            for source,checkpoint in (source_checkpoints or {}).items():
                connection.execute('INSERT INTO source_refresh_checkpoints VALUES(?,?,?,?,?,?) '
                    'ON CONFLICT(profile_id,source_mode,source) DO UPDATE SET signature=excluded.signature,'
                    'started_at=excluded.started_at,completed_at=excluded.completed_at',
                    (profile_id,source_mode,source,checkpoint['signature'],checkpoint['started_at'],checkpoint.get('completed_at')))
            if not partial:
                connection.execute("DELETE FROM refresh_checkpoints WHERE profile_id=? AND source_mode=?", (profile_id, source_mode))
            if refresh_checkpoint:
                connection.execute("INSERT INTO refresh_checkpoints(profile_id,source_mode,signature,started_at,completed_at) VALUES(?,?,?,?,?)",
                    (profile_id, source_mode, refresh_checkpoint['signature'], refresh_checkpoint['started_at'],refresh_checkpoint.get('completed_at')))
            if not partial:
                connection.execute("DELETE FROM doi_visits WHERE profile_id=?", (profile_id,))
                connection.execute("DELETE FROM profile_results WHERE profile_id=? AND source_mode=?", (profile_id, source_mode))
                connection.execute("DELETE FROM profile_articles WHERE profile_id=?", (profile_id,))
            for article, relevance_score in candidates:
                article_values = {**article, "first_seen_at": now, "updated_at": now}
                connection.execute(
                    """
                    INSERT INTO articles (
                        article_key, doi, pmid, pmcid, title, abstract, authors, journal_title,
                        issn, publication_date, publication_year, cited_by_count, is_open_access,
                        landing_page_url, graphical_abstract_url, source_name, first_seen_at, updated_at
                    ) VALUES (
                        :article_key, :doi, :pmid, :pmcid, :title, :abstract, :authors, :journal_title,
                        :issn, :publication_date, :publication_year, :cited_by_count, :is_open_access,
                        :landing_page_url, :graphical_abstract_url, :source_name, :first_seen_at, :updated_at
                    )
                    ON CONFLICT(article_key) DO UPDATE SET
                        doi = COALESCE(NULLIF(excluded.doi, ''), articles.doi),
                        pmid = COALESCE(NULLIF(excluded.pmid, ''), articles.pmid),
                        pmcid = COALESCE(NULLIF(excluded.pmcid, ''), articles.pmcid),
                        title = COALESCE(NULLIF(excluded.title, ''), articles.title),
                        abstract = CASE WHEN length(excluded.abstract) > length(COALESCE(articles.abstract, ''))
                            THEN excluded.abstract ELSE articles.abstract END,
                        authors = COALESCE(NULLIF(excluded.authors, ''), articles.authors),
                        journal_title = COALESCE(NULLIF(excluded.journal_title, ''), articles.journal_title),
                        issn = COALESCE(NULLIF(excluded.issn, ''), articles.issn),
                        publication_date = COALESCE(NULLIF(excluded.publication_date, ''), articles.publication_date),
                        publication_year = COALESCE(excluded.publication_year, articles.publication_year),
                        cited_by_count = MAX(articles.cited_by_count, excluded.cited_by_count),
                        is_open_access = MAX(articles.is_open_access, excluded.is_open_access),
                        landing_page_url = COALESCE(NULLIF(excluded.landing_page_url, ''), articles.landing_page_url),
                        graphical_abstract_url = COALESCE(
                            NULLIF(excluded.graphical_abstract_url, ''), articles.graphical_abstract_url),
                        source_name = CASE
                            WHEN instr(articles.source_name, excluded.source_name) > 0 THEN articles.source_name
                            WHEN instr(excluded.source_name, articles.source_name) > 0 THEN excluded.source_name
                            ELSE articles.source_name || ' + ' || excluded.source_name END,
                        updated_at = excluded.updated_at
                    """,
                    article_values,
                )
                connection.execute(
                    """
                    INSERT INTO profile_articles(profile_id, article_key, relevance_score, fetched_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(profile_id, article_key) DO UPDATE SET
                        relevance_score = excluded.relevance_score,
                        fetched_at = excluded.fetched_at
                    """,
                    (profile_id, article["article_key"], relevance_score, now),
                )
                connection.execute("UPDATE articles SET publication_types_json=? WHERE article_key=?",
                                   (json.dumps(article.get("publication_types") or []), article["article_key"]))
                for field in ('volume','issue','pages'):
                    if article.get(field):
                        connection.execute(f'UPDATE articles SET {field}=? WHERE article_key=?', (str(article[field]),article['article_key']))
                connection.execute("INSERT OR REPLACE INTO profile_results VALUES(?,?,?,?,?,?)",
                                   (profile_id, source_mode, article["article_key"], json.dumps(article, ensure_ascii=False), relevance_score, now))
            if partial:
                connection.execute("INSERT OR IGNORE INTO profile_cache_states VALUES(?,?,?)", (profile_id,source_mode,''))
                connection.execute("UPDATE search_profiles SET display_cache_mode=? WHERE id=?", (source_mode,profile_id))
                return
            connection.execute("INSERT INTO profile_cache_states VALUES(?,?,?) ON CONFLICT(profile_id,source_mode) "
                               "DO UPDATE SET refreshed_at=excluded.refreshed_at", (profile_id, source_mode, now))
            connection.execute(
                "UPDATE search_profiles SET last_refreshed_at = ?, updated_at = ?,display_cache_mode=? WHERE id = ?",
                (now, now, source_mode, profile_id),
            )

    def refresh_checkpoint(self, profile_id, source_mode):
        with self.connect() as connection:
            row = connection.execute("SELECT signature,started_at,completed_at FROM refresh_checkpoints WHERE profile_id=? AND source_mode=?",
                                     (profile_id, source_mode)).fetchone()
            return dict(row) if row else None

    def source_refresh_checkpoints(self,profile_id,source_mode):
        with self.connect() as connection:
            return {row['source']:dict(row) for row in connection.execute(
                'SELECT source,signature,started_at,completed_at FROM source_refresh_checkpoints WHERE profile_id=? AND source_mode=?',(profile_id,source_mode))}

    def hidden_article_tokens(self,profile_id):
        with self.connect() as connection:
            rows=connection.execute('SELECT article_key,doi,pmid FROM profile_hidden_articles WHERE profile_id=?',(profile_id,)).fetchall()
        return {(name,str(row[name]).strip().casefold()) for row in rows for name in ('article_key','doi','pmid') if row[name]}

    @staticmethod
    def matches_hidden_article(article,tokens):
        return any((name,str(article.get(name) or '').strip().casefold()) in tokens for name in ('article_key','doi','pmid') if article.get(name))

    def set_article_hidden(self,profile_id,article,hidden):
        """Only this search profile is affected. DOI/PMID keep the state across refreshed keys."""
        with self.connect() as connection:
            # Remove all equivalent stored identities before hide/unhide.
            connection.execute('DELETE FROM profile_hidden_articles WHERE profile_id=? AND (article_key=? OR (?<>\'\' AND lower(doi)=?) OR (?<>\'\' AND pmid=?))',
                (profile_id,article['article_key'],str(article.get('doi') or '').strip().casefold(),str(article.get('doi') or '').strip().casefold(),str(article.get('pmid') or '').strip(),str(article.get('pmid') or '').strip()))
            if hidden:
                connection.execute('INSERT INTO profile_hidden_articles VALUES(?,?,?,?,?)',
                    (profile_id,article['article_key'],str(article.get('doi') or '').strip().casefold(),str(article.get('pmid') or '').strip(),utc_now()))

    def result_metadata(self, profile_id, source_mode):
        """Read only sorting/filter fields: do not deserialize abstracts for the result index."""
        with self.connect() as connection:
            state=connection.execute('SELECT 1 FROM profile_cache_states WHERE profile_id=? AND source_mode=?',(profile_id,source_mode)).fetchone()
            if state:
                return [dict(row) for row in connection.execute("""SELECT article_key,relevance_score,
                    json_extract(article_json,'$.doi') AS doi,json_extract(article_json,'$.pmid') AS pmid,
                    json_extract(article_json,'$.publication_date') AS publication_date,
                    json_extract(article_json,'$.publication_year') AS publication_year,
                    json_extract(article_json,'$.source_name') AS source_name,
                    json_extract(article_json,'$.website_rank') AS website_rank,
                    json_extract(article_json,'$.publication_types') AS types_json,
                    json_extract(article_json,'$.website_date_filtered') AS website_date_filtered
                    FROM profile_results WHERE profile_id=? AND source_mode=?""",(profile_id,source_mode))]
            if source_mode.endswith('_website'):return []
            wanted={'pubmed':'PubMed','biorxiv':'bioRxiv'}.get(source_mode,'PubMed')
            return [dict(row) for row in connection.execute('''SELECT a.article_key,pa.relevance_score,
                a.doi,a.pmid,a.publication_date,a.publication_year,a.source_name,a.publication_types_json AS types_json,
                0 AS website_date_filtered FROM profile_articles pa JOIN articles a ON a.article_key=pa.article_key
                WHERE pa.profile_id=? AND a.source_name=?''',(profile_id,wanted))]

    def result_count(self,profile_id,source_mode):
        with self.connect() as connection:
            if connection.execute('SELECT 1 FROM profile_cache_states WHERE profile_id=? AND source_mode=?',(profile_id,source_mode)).fetchone():
                return connection.execute('SELECT COUNT(*) FROM profile_results WHERE profile_id=? AND source_mode=?',(profile_id,source_mode)).fetchone()[0]
            if source_mode.endswith('_website'):return 0
            wanted={'pubmed':'PubMed','biorxiv':'bioRxiv'}.get(source_mode,'PubMed')
            return connection.execute('SELECT COUNT(*) FROM profile_articles pa JOIN articles a ON a.article_key=pa.article_key WHERE pa.profile_id=? AND a.source_name=?',(profile_id,wanted)).fetchone()[0]

    def cached_articles(self, profile_id: int, source_mode: str | None = None, article_keys=None) -> list[dict[str, Any]]:
        source_mode = source_mode or self.profile_cache_mode(profile_id)
        if article_keys is not None and not article_keys:return []
        key_clause=(' AND r.article_key IN ('+','.join('?' for _ in article_keys)+')') if article_keys is not None else ''
        key_parameters=tuple(article_keys or ())
        with self.connect() as connection:
            state = connection.execute("SELECT 1 FROM profile_cache_states WHERE profile_id=? AND source_mode=?", (profile_id, source_mode)).fetchone()
            if state:
                rows = connection.execute(
                    "SELECT r.*,COALESCE(f.is_favorite,0) AS is_favorite,COALESCE(f.saved_at,'') AS favorite_saved_at,"
                    "COALESCE(f.tags,'') AS tags,COALESCE(f.personal_note,'') AS personal_note,j.jif,j.jif_year,"
                    "a.volume AS bib_volume,a.issue AS bib_issue,a.pages AS bib_pages "
                    "FROM profile_results r LEFT JOIN favorites f ON f.article_key=r.article_key "
                    "LEFT JOIN articles a ON a.article_key=r.article_key LEFT JOIN journals j ON j.issn=a.issn "
                    "WHERE r.profile_id=? AND r.source_mode=?"+key_clause, (profile_id, source_mode,*key_parameters)).fetchall()
                return self.attach_jif([{**json.loads(row["article_json"]),
                         **{k:row['bib_'+k] for k in ('volume','issue','pages') if row['bib_'+k]}, **{k: row[k] for k in
                         ("relevance_score", "fetched_at", "is_favorite", "favorite_saved_at", "tags", "personal_note", "jif", "jif_year")}}
                        for row in rows])
            if source_mode.endswith('_website'):
                return []
            rows = connection.execute(
                """
                SELECT a.*, pa.relevance_score, pa.fetched_at,
                       COALESCE(f.is_favorite, 0) AS is_favorite,
                       COALESCE(f.saved_at, '') AS favorite_saved_at,
                       COALESCE(f.tags, '') AS tags,
                       COALESCE(f.personal_note, '') AS personal_note,
                       j.jif, j.jif_year
                FROM profile_articles AS pa
                JOIN articles AS a ON a.article_key = pa.article_key
                LEFT JOIN favorites AS f ON f.article_key = a.article_key
                LEFT JOIN journals AS j ON j.issn = a.issn
                WHERE pa.profile_id = ?
                """+key_clause.replace('r.article_key','a.article_key'),
                (profile_id,*key_parameters),
            ).fetchall()
            # Old mixed records cannot reconstruct source-specific abstracts, so single-source
            # views only expose unambiguous legacy records until that source is refreshed.
            wanted = {"pubmed": "PubMed", "biorxiv": "bioRxiv"}.get(source_mode, "PubMed")
            return self.attach_jif([{**dict(row), "publication_types": json.loads(row["publication_types_json"])}
                    for row in rows if wanted is None or row["source_name"] == wanted])

    def update_biorxiv_detail(self, article_key, detail):
        """Hydrate metadata without touching refresh times, DOI visits or favorite membership."""
        fields=('abstract','authors','publication_date','publication_year','preprint_version')
        with self.connect() as connection:
            connection.execute('UPDATE articles SET abstract=?,authors=?,publication_date=?,publication_year=? WHERE article_key=?',
                (detail.get('abstract') or '',detail.get('authors') or '',detail.get('publication_date') or '',detail.get('publication_year'),article_key))
            for row in connection.execute('SELECT profile_id,source_mode,article_json FROM profile_results WHERE article_key=?',(article_key,)).fetchall():
                article=json.loads(row['article_json'])
                if article.get('biorxiv_search_method')!='website':continue
                if article.get('preprint_version') and article['preprint_version']!=detail.get('preprint_version'):continue
                article.update({key:detail.get(key) for key in fields})
                connection.execute('UPDATE profile_results SET article_json=? WHERE profile_id=? AND source_mode=? AND article_key=?',
                    (json.dumps(article,ensure_ascii=False),row['profile_id'],row['source_mode'],article_key))
                profile=connection.execute('SELECT query_expression,sort_mode FROM search_profiles WHERE id=?',(row['profile_id'],)).fetchone()
                if profile and profile['sort_mode']!='最相关':
                    # Keep official Best Match ranks intact. Other offline scores can
                    # use the newly cached abstract on the next sorting pass.
                    from literature import LiteratureClient
                    score=LiteratureClient.local_relevance(article.get('title',''),article.get('abstract',''),profile['query_expression'])
                    connection.execute('UPDATE profile_results SET relevance_score=? WHERE profile_id=? AND source_mode=? AND article_key=?',
                        (score,row['profile_id'],row['source_mode'],article_key))

    def set_favorite(self, article_key: str, favorite: bool, tags: str = "", note: str = "", profile_id: int | None = None) -> None:
        # Unfavoriting is global. UI entry points must confirm before calling.
        if not favorite:profile_id=None
        now = utc_now()
        with self.connect() as connection:
            if profile_id is not None:
                profile = connection.execute("SELECT * FROM search_profiles WHERE id=?", (profile_id,)).fetchone()
                if not profile:
                    raise ValueError("关键词方案已不存在，请重新打开页面。")
                group_id = self._ensure_profile_group(connection, profile_id, profile["name"], profile["query_expression"])
                connection.execute("INSERT OR IGNORE INTO favorite_group_articles VALUES(?,?)", (group_id, article_key))
            elif not favorite:
                connection.execute("DELETE FROM favorite_group_articles WHERE article_key=?", (article_key,))
            existing = connection.execute(
                "SELECT saved_at, tags, personal_note FROM favorites WHERE article_key = ?",
                (article_key,),
            ).fetchone()
            saved_at = existing["saved_at"] if existing else now
            previous_tags = existing["tags"] if existing else ""
            previous_note = existing["personal_note"] if existing else ""
            connection.execute(
                """
                INSERT INTO favorites(article_key, is_favorite, tags, personal_note, saved_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(article_key) DO UPDATE SET
                    is_favorite = excluded.is_favorite,
                    tags = excluded.tags,
                    personal_note = excluded.personal_note,
                    updated_at = excluded.updated_at
                """,
                (
                    article_key, 1 if favorite else 0,
                    tags if tags != "" else previous_tags,
                    note if note != "" else previous_note,
                    saved_at, now,
                ),
            )
            if not favorite:
                self._remove_unfavorited_pdf(connection,article_key)

    def cancel_favorites(self,article_keys):
        """Commit per article so a locked PDF retains that article and its groups."""
        removed=[];failed=[]
        for key in dict.fromkeys(article_keys):
            try:self.set_favorite(key,False);removed.append(key)
            except (OSError,sqlite3.Error) as error:failed.append({'article_key':key,'error':str(error)})
        return dict(removed=removed,failed=failed)

    def _remove_unfavorited_pdf(self,connection,article_key):
        """Remove only this application's unused PDF copy, never its source file.

        The caller's write transaction protects the membership check. A locked
        file raises an error and rolls back the cancellation, retaining its link.
        Shared filenames are retained while any other article links to them.
        """
        if connection.execute('SELECT 1 FROM favorite_group_articles WHERE article_key=?',(article_key,)).fetchone():return
        if connection.execute('SELECT 1 FROM favorites WHERE article_key=? AND is_favorite=1',(article_key,)).fetchone():return
        row=connection.execute('SELECT filename,storage_location FROM article_pdfs WHERE article_key=?',(article_key,)).fetchone()
        if not row:return
        filename=row['filename']
        valid=(isinstance(filename,str) and Path(filename).name==filename and '/' not in filename and '\\' not in filename and Path(filename).suffix.lower()=='.pdf')
        shared=connection.execute('SELECT 1 FROM article_pdfs WHERE filename=? AND article_key<>?',(filename,article_key)).fetchone()
        if valid and row['storage_location']=='data' and not shared:
            target=self.paper_directory/filename
            if not target.is_symlink() and target.resolve().parent==self.paper_directory.resolve():
                target.unlink(missing_ok=True)
        connection.execute('DELETE FROM article_pdfs WHERE article_key=?',(article_key,))

    def save_favorite_notes(self, article_key: str, tags: str, note: str) -> None:
        now = utc_now()
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT is_favorite, saved_at FROM favorites WHERE article_key = ?",
                (article_key,),
            ).fetchone()
            favorite = int(existing["is_favorite"]) if existing else 0
            saved_at = existing["saved_at"] if existing else now
            connection.execute(
                """
                INSERT INTO favorites(article_key, is_favorite, tags, personal_note, saved_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(article_key) DO UPDATE SET
                    tags = excluded.tags, personal_note = excluded.personal_note,
                    is_favorite = excluded.is_favorite, updated_at = excluded.updated_at
                """,
                (article_key, favorite, tags, note, saved_at, now),
            )

    def article_record(self, article_key):
        with self.connect() as connection:
            row=connection.execute('SELECT * FROM articles WHERE article_key=?',(article_key,)).fetchone()
            return dict(row) if row else {}

    def save_bibliography(self, article_key, metadata):
        """Enrich old caches without changing result sets or refresh checkpoints."""
        with self.connect() as connection:
            for field in ('volume','issue','pages'):
                if metadata.get(field):
                    connection.execute(f'UPDATE articles SET {field}=? WHERE article_key=?',(str(metadata[field]),article_key))
            connection.execute('UPDATE articles SET bibliography_checked_at=? WHERE article_key=?',(utc_now(),article_key))

    def save_pdf_favorite(self, article, source_pdf, group_id):
        """Commit metadata, group membership and the linked application-owned copy together."""
        copied=None
        try:
            with self.connect() as connection:
                if not connection.execute('SELECT 1 FROM favorite_groups WHERE id=?',(group_id,)).fetchone():
                    raise ValueError('目标收藏分组已不存在。')
                doi=str(article.get('doi') or '').strip().lower();pmid=str(article.get('pmid') or '')
                old=connection.execute("SELECT * FROM articles WHERE article_key=? OR (?<>'' AND lower(doi)=?) OR (?<>'' AND pmid=?) LIMIT 1",
                    (article['article_key'],doi,doi,pmid,pmid)).fetchone()
                key=old['article_key'] if old else article['article_key']
                columns=('article_key','doi','pmid','pmcid','title','abstract','authors','journal_title','issn',
                    'publication_date','publication_year','cited_by_count','is_open_access','landing_page_url',
                    'graphical_abstract_url','source_name','volume','issue','pages','publication_types_json','first_seen_at','updated_at')
                now=utc_now();values={field:article.get(field) or '' for field in columns}
                values.update(article_key=key,publication_year=article.get('publication_year'),
                    cited_by_count=article.get('cited_by_count') or 0,is_open_access=article.get('is_open_access') or 0,
                    publication_types_json=json.dumps(article.get('publication_types') or []),first_seen_at=now,updated_at=now)
                if old:
                    sources=set(filter(None,(str(old['source_name'])+' + '+str(values['source_name'])).split(' + ')))
                    values['source_name']=' + '.join(sorted(sources))
                    for field in columns:
                        if field not in ('article_key','first_seen_at') and values[field] not in ('',None):
                            connection.execute(f'UPDATE articles SET {field}=? WHERE article_key=?',(values[field],key))
                else:
                    connection.execute('INSERT INTO articles('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',
                        [values[field] for field in columns])
                connection.execute("INSERT INTO favorites(article_key,is_favorite,saved_at,updated_at) VALUES(?,1,?,?) "
                    "ON CONFLICT(article_key) DO UPDATE SET is_favorite=1,updated_at=excluded.updated_at",(key,now,now))
                connection.execute('INSERT OR IGNORE INTO favorite_group_articles VALUES(?,?)',(group_id,key))
                # Keep any older linked copy intact; never alter the selected source file.
                already=connection.execute('SELECT filename FROM article_pdfs WHERE article_key=?',(key,)).fetchone()
                linked=self.paper_directory/already[0] if already else None
                if linked and linked.is_file() and Path(source_pdf).resolve()==linked.resolve():
                    copied=linked;created=False
                else:
                    copied=copy_pdf(Path(source_pdf),article['title'],key,self.paper_directory)
                    created=copied.resolve()!=Path(source_pdf).resolve()
                connection.execute("INSERT INTO article_pdfs(article_key,filename,linked_at,storage_location) VALUES(?,?,?,'data') "
                    "ON CONFLICT(article_key) DO UPDATE SET filename=excluded.filename,linked_at=excluded.linked_at,storage_location='data'",(key,copied.name,now))
            return key
        except Exception:
            if copied and locals().get('created'):
                copied.unlink(missing_ok=True)
            raise

    def favorite_articles(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT a.*, f.saved_at AS favorite_saved_at, f.tags, f.personal_note,
                       1 AS is_favorite, j.jif, j.jif_year
                FROM favorites AS f
                JOIN articles AS a ON a.article_key = f.article_key
                LEFT JOIN journals AS j ON j.issn = a.issn
                WHERE f.is_favorite = 1
                """
            ).fetchall()
            return self.attach_jif([dict(row) for row in rows])

    def list_custom_groups(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows=[dict(row) for row in connection.execute("SELECT * FROM favorite_groups ORDER BY name")]
        return self._ordered_cards(rows,'favorite_card_order')

    def create_favorite_group(self,name,keywords,rename_duplicate=False):
        name=name.strip();keywords=keywords.strip()
        if not name or not keywords or name=='未分组':raise ValueError('请输入分组名称与关键词，且不能使用“未分组”。')
        with self.connect() as connection:
            occupied={row[0].casefold() for row in connection.execute('SELECT name FROM favorite_groups')}
            if name.casefold() in occupied:
                if not rename_duplicate:raise sqlite3.IntegrityError('分组名称重复')
                base=name+'_重复';name=base;number=2
                while name.casefold() in occupied:name=f'{base}_{number}';number+=1
            now=utc_now()
            identifier=connection.execute('INSERT INTO favorite_groups(name,keywords,created_at,updated_at) VALUES(?,?,?,?)',(name,keywords,now,now)).lastrowid
        return identifier,name

    def transfer_favorites(self,article_keys,target_ids,move=False,source_group_id=None):
        keys=list(dict.fromkeys(article_keys));targets=list(dict.fromkeys(int(i) for i in target_ids))
        if not keys or not targets:raise ValueError('请选择文章及至少一个目标分组。')
        with self.connect() as connection:
            valid={row[0] for row in connection.execute('SELECT id FROM favorite_groups')}
            favorites={row[0] for row in connection.execute('SELECT article_key FROM favorites WHERE is_favorite=1')}
            if not set(targets)<=valid or not set(keys)<=favorites:raise ValueError('分组或收藏已发生变化，请刷新后重试。')
            if source_group_id is not None and source_group_id not in valid:raise ValueError('原分组已不存在。')
            if source_group_id in targets:raise ValueError('目标分组不能包含原分组。')
            if move:
                for key in keys:
                    if source_group_id is None:connection.execute('DELETE FROM favorite_group_articles WHERE article_key=?',(key,))
                    else:connection.execute('DELETE FROM favorite_group_articles WHERE article_key=? AND group_id=?',(key,source_group_id))
            connection.executemany('INSERT OR IGNORE INTO favorite_group_articles(group_id,article_key) VALUES(?,?)',[(group,key) for group in targets for key in keys])
        return len(keys)

    def replace_custom_groups(self, groups: list[dict[str, str]]) -> None:
        now = utc_now()
        with self.connect() as connection:
            retained = {int(g["id"]) for g in groups if g.get("id")}
            for old in connection.execute("SELECT id,profile_id FROM favorite_groups").fetchall():
                if old["id"] not in retained:
                    if old["profile_id"] is not None:
                        connection.execute("INSERT OR IGNORE INTO suppressed_profile_groups VALUES(?)", (old["profile_id"],))
                    connection.execute("DELETE FROM favorite_groups WHERE id=?", (old["id"],))
            for group in groups:
                if group.get("name", "").strip() and group.get("keywords", "").strip():
                    if group.get("id"):
                        origin = connection.execute("SELECT p.name FROM favorite_groups g JOIN search_profiles p "
                            "ON p.id=g.profile_id WHERE g.id=?", (group["id"],)).fetchone()
                        if origin and group["name"].strip() != origin[0]:
                            raise ValueError("分组名称与来源方案保持一致，请在关键词方案中改名。")
                        connection.execute("UPDATE favorite_groups SET name=?,keywords=?,updated_at=? WHERE id=?",
                                           (group["name"].strip(), group["keywords"].strip(), now, group["id"]))
                    else:
                        connection.execute("INSERT INTO favorite_groups(name,keywords,created_at,updated_at) VALUES(?,?,?,?)",
                                           (group["name"].strip(), group["keywords"].strip(), now, now))

    def classify_favorite(self, article: dict[str, Any]) -> str:
        return next((name for name, rows in self.grouped_favorites().items()
                     if any(a["article_key"] == article["article_key"] for a in rows)), "未分组")

    def _rename_profile_group(self, connection, profile_id: int, name: str) -> None:
        old = connection.execute("SELECT id FROM favorite_groups WHERE profile_id=?", (profile_id,)).fetchone()
        if not old:
            return
        collision = connection.execute("SELECT id FROM favorite_groups WHERE name=? AND id<>?", (name, old[0])).fetchone()
        if collision:
            connection.execute("INSERT OR IGNORE INTO favorite_group_articles SELECT ?,article_key FROM favorite_group_articles WHERE group_id=?", (collision[0], old[0]))
            connection.execute("DELETE FROM favorite_groups WHERE id=?", (old[0],))
            connection.execute("UPDATE favorite_groups SET profile_id=?,updated_at=? WHERE id=?", (profile_id, utc_now(), collision[0]))
        else:
            connection.execute("UPDATE favorite_groups SET name=?,updated_at=? WHERE id=?", (name, utc_now(), old[0]))

    def _ensure_profile_group(self, connection, profile_id: int, name: str, query: str) -> int:
        # An explicit new save may recreate a previously deleted group; mere searches may not.
        connection.execute("DELETE FROM suppressed_profile_groups WHERE profile_id=?", (profile_id,))
        self._rename_profile_group(connection, profile_id, name)
        existing = connection.execute("SELECT id FROM favorite_groups WHERE profile_id=?", (profile_id,)).fetchone()
        if existing:
            return existing[0]
        existing = connection.execute("SELECT id FROM favorite_groups WHERE name=?", (name,)).fetchone()
        if existing:
            connection.execute("UPDATE favorite_groups SET profile_id=?,keywords=? WHERE id=?", (profile_id, query, existing[0]))
            return existing[0]
        return connection.execute("INSERT INTO favorite_groups(name,keywords,created_at,updated_at,profile_id) VALUES(?,?,?,?,?)",
                                  (name, query, utc_now(), utc_now(), profile_id)).lastrowid

    def favorite_keys_for_profile(self, profile_id: int) -> set[str]:
        with self.connect() as connection:
            return {r[0] for r in connection.execute("SELECT ga.article_key FROM favorite_group_articles ga "
                "JOIN favorite_groups g ON g.id=ga.group_id JOIN favorites f ON f.article_key=ga.article_key "
                "WHERE g.profile_id=? AND f.is_favorite=1", (profile_id,))}

    def grouped_favorites(self) -> dict[str, list[dict[str, Any]]]:
        """A favorite can belong to several keyword-profile groups; no journal/field inference."""
        groups = self.list_custom_groups()
        result = {g["name"]: [] for g in groups}
        with self.connect() as connection:
            links = {}
            for row in connection.execute("SELECT group_id,article_key FROM favorite_group_articles"):
                links.setdefault(row["article_key"], set()).add(row["group_id"])
        for article in self.favorite_articles():
            matched = False
            for group in groups:
                if group["id"] in links.get(article["article_key"], set()):
                    result[group["name"]].append(article)
                    matched = True
            if not matched:
                result.setdefault("未分组", []).append(article)
        return result

    def attach_jif(self, articles: list[dict]) -> list[dict]:
        """Join metrics on canonical ISSN or electronic/print aliases, never fuzzy journal names."""
        if not articles:return articles
        with self._metric_lock:
            if self._metrics is None:
                self._metrics = {}
                self._metrics_names = {}
                for journal in self.list_journals():
                    if journal["jif"] is not None:
                        self._metrics_names[journal["title"].strip().casefold()] = journal
                        for alias in {journal["issn"], *(journal["aliases"] or "").split(";")}:
                            if alias:
                                self._metrics[alias.upper().replace("-", "")] = journal
                nc_asset = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets/nc_journals.json"
                if nc_asset.exists():
                    for row in json.loads(nc_asset.read_text(encoding="utf-8")):
                        metric = self._metrics.get(row["issn"].replace("-", ""))
                        if metric:
                            self._metrics_names[row["title"].strip().casefold()] = metric
        with self.connect() as connection:
            clause=' WHERE article_key IN ('+','.join('?' for _ in articles)+')' if 0<len(articles)<=500 else ''
            shared_metrics = {row['article_key']:dict(row) for row in connection.execute('SELECT * FROM shared_article_metrics'+clause,
                                tuple(a['article_key'] for a in articles) if clause else ())}
        selected_year=int(self.get_settings()['jif_year'])
        for article in articles:
            journal = self._metrics.get(str(article.get("issn") or "").upper().replace("-", ""))
            if journal is None and not article.get("issn"):
                journal = self._metrics_names.get(str(article.get("journal_title") or "").strip().casefold())
            if journal:
                article.update(jif=journal["jif"], jif_year=journal["jif_year"], jif_source=journal["source"])
            else:
                shared = shared_metrics.get(article['article_key']) or {}
                if shared.get('jif_year')!=selected_year:shared={}
                article.update(jif=shared.get('jif'), jif_year=shared.get('jif_year'), jif_source=shared.get('jif_source') or "")
        return articles

    def linked_pdf(self, article_key: str) -> Path | None:
        with self.connect() as connection:
            row = connection.execute("SELECT filename FROM article_pdfs WHERE article_key=?", (article_key,)).fetchone()
        if not row or Path(row[0]).name != row[0] or "/" in row[0] or "\\" in row[0]:
            return None
        path = self.paper_directory / row[0]
        return path if path.is_file() else None

    def migrate_legacy_pdfs(self) -> None:
        """Copy referenced 1.5 PDFs into the data folder, retaining all original files."""
        roots = [legacy_paper_directory()]
        if os.environ.get("LOCALAPPDATA"):
            roots.append(Path(os.environ["LOCALAPPDATA"]) / "Programs/OrangeBiomedReader/paper")
        with self.connect() as connection:
            rows = connection.execute("SELECT article_key,filename FROM article_pdfs WHERE storage_location='legacy'").fetchall()
        errors = []
        for row in rows:
            filename = row["filename"]
            if Path(filename).name != filename or "/" in filename or "\\" in filename:
                continue
            for root in roots:
                source = root / filename
                if source.is_file() and root.resolve() != self.paper_directory.resolve():
                    try:
                        # Keep the recorded title filename when migrating, including any suffix.
                        target = copy_pdf(source, source.stem, row["article_key"], self.paper_directory)
                        with self.connect() as connection:
                            connection.execute("UPDATE article_pdfs SET filename=?,storage_location='data' WHERE article_key=?", (target.name, row["article_key"]))
                    except (OSError, ValueError, sqlite3.Error) as error:
                        errors.append(str(error))
                    break
        if errors:
            self.save_settings({"pdf_migration_error": "；".join(errors)})

    def link_pdf(self, article_key: str, source: Path) -> Path:
        with self.connect() as connection:
            article = connection.execute("SELECT a.title FROM articles a JOIN favorites f ON a.article_key=f.article_key "
                "WHERE a.article_key=? AND f.is_favorite=1", (article_key,)).fetchone()
            if not article:
                raise ValueError("请先收藏文章，再关联本地 PDF。")
        target = copy_pdf(source, article[0], article_key, self.paper_directory)
        with self.connect() as connection:
            connection.execute("INSERT INTO article_pdfs(article_key,filename,linked_at,storage_location) VALUES(?,?,?,'data') ON CONFLICT(article_key) "
                "DO UPDATE SET filename=excluded.filename,linked_at=excluded.linked_at,storage_location='data'", (article_key, target.name, utc_now()))
        return target

    def set_profile_view(self, profile_id: int, view_mode: str) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE search_profiles SET view_mode=? WHERE id=?", (view_mode, profile_id))

    def cache_refreshed_at(self, profile_id: int) -> str:
        mode = self.profile_cache_mode(profile_id)
        with self.connect() as connection:
            row = connection.execute("SELECT refreshed_at FROM profile_cache_states WHERE profile_id=? AND source_mode=?", (profile_id, mode)).fetchone()
            return row[0] if row else "该来源尚未刷新"


    def card_position(self, profile_id, source_mode):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM card_positions WHERE profile_id=? AND source_mode=?", (profile_id, source_mode)).fetchone()
            return dict(row) if row else {"page": 0, "scroll_y": 0, "sort_mode": ""}

    def save_card_position(self, profile_id, source_mode, page, scroll_y, sort_mode):
        with self.connect() as connection:
            connection.execute("INSERT INTO card_positions VALUES(?,?,?,?,?) ON CONFLICT(profile_id,source_mode) DO UPDATE SET page=excluded.page,scroll_y=excluded.scroll_y,sort_mode=excluded.sort_mode",
                               (profile_id, source_mode, max(0, page), max(0, scroll_y), sort_mode))

    def mark_doi_visited(self, profile_id, source_mode, article_key, generation):
        with self.connect() as connection:
            row = connection.execute("SELECT refreshed_at FROM profile_cache_states WHERE profile_id=? AND source_mode=?", (profile_id, source_mode)).fetchone()
            current = row[0] if row else "该来源尚未刷新"
            if current != generation:
                return False  # A detail window from before a refresh cannot mark the new results.
            connection.execute("INSERT OR REPLACE INTO doi_visits VALUES(?,?,?,?)", (profile_id, source_mode, article_key, generation))
            return True

    def visited_dois(self, profile_id, source_mode):
        with self.connect() as connection:
            return {row[0] for row in connection.execute("SELECT article_key FROM doi_visits WHERE profile_id=? AND source_mode=?", (profile_id, source_mode))}
