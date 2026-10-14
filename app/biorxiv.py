"""Official bioRxiv metadata API, with resumable pages and local keyword matching.

The public API exposes dates, not website full-text search. We therefore never send
keywords to an undocumented endpoint or silently use another literature index.
"""
from datetime import date, timedelta
import json
import html
import re
import sqlite3
import time
from pathlib import Path

API_ROOT = "https://api.biorxiv.org/details/biorxiv"


def plain_text(value):
    """Flatten occasional inline markup before both matching and displaying metadata."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", str(value or ""))).split())


def keyword_predicate(expression):
    """Compile AND/OR/NOT, parentheses, quoted phrases and trailing * wildcards.

    Adjacent terms mean AND. Field qualifiers are rejected because their meaning
    would differ from PubMed; an unsupported expression must never look successful.
    """
    if re.search(r'[\[\]:]', expression):
        raise ValueError("bioRxiv 仅支持标题和摘要关键词；请移除 PubMed 字段标签、冒号等限定符。")
    pattern = r'"[^"]+"|\(|\)|\bAND\b|\bOR\b|\bNOT\b|[^\s()"]+'
    tokens = re.findall(pattern, expression, flags=re.I)
    if not tokens or expression.count('"') % 2:
        raise ValueError("bioRxiv 关键词表达式为空或引号不成对。")
    position = 0

    def primary():
        nonlocal position
        if position >= len(tokens):
            raise ValueError("bioRxiv 关键词表达式缺少检索词。")
        token = tokens[position]
        position += 1
        if token.upper() == "NOT":
            child = primary()
            return lambda text: not child(text)
        if token == "(":
            child = either()
            if position >= len(tokens) or tokens[position] != ")":
                raise ValueError("bioRxiv 关键词表达式括号不成对。")
            position += 1
            return child
        if token.upper() in ("AND", "OR") or token == ")":
            raise ValueError("bioRxiv 关键词运算符的位置不正确。")
        term = token.strip('"').casefold()
        if "*" in term[:-1] or "?" in term or term == "*":
            raise ValueError("bioRxiv 通配符仅支持词尾 *。")
        matcher = re.compile(r"(?<!\w)" + re.escape(term.rstrip("*")) + (r"\w*" if term.endswith("*") else r"(?!\w)"))
        return lambda text: bool(matcher.search(text))

    def both():
        nonlocal position
        children = [primary()]
        while position < len(tokens) and tokens[position].upper() not in ("OR", ")"):
            if tokens[position].upper() == "AND":
                position += 1
            children.append(primary())
        return lambda text: all(child(text) for child in children)

    def either():
        nonlocal position
        children = [both()]
        while position < len(tokens) and tokens[position].upper() == "OR":
            position += 1
            children.append(both())
        return lambda text: any(child(text) for child in children)

    predicate = either()
    if position != len(tokens):
        raise ValueError("bioRxiv 关键词表达式有多余括号。")
    return predicate


def seed_daily_snapshots(connection, day):
    """Reuse complete 1.8 monthly downloads instead of downloading those days again.

    Incomplete or internally inconsistent pages are never treated as complete days.
    The original raw pages remain available for historical monthly searches.
    """
    prefix=f"{API_ROOT}/{day.replace(day=1)}/"
    intervals={}
    for url,payload,fetched in connection.execute('SELECT url,payload,fetched FROM pages WHERE url LIKE ?', (prefix+'%',)):
        try:
            start,end,cursor,_=url[len(API_ROOT)+1:].split('/')
            if start==end:continue
            intervals.setdefault((start,end),{})[int(cursor)]=(json.loads(payload),fetched)
        except (ValueError,TypeError):continue
    for (start,end),pages in sorted(intervals.items(),reverse=True):
        cursor=0;records=[];timestamps=[];expected=None
        while cursor in pages:
            payload,fetched=pages[cursor]
            message=(payload.get('messages') or [{}])[0]
            if message.get('status') not in ('ok','no papers found') or message.get('total') is None:break
            total=int(message['total']);batch=payload.get('collection') or []
            if expected is not None and total!=expected:break
            expected=total;records.extend(batch);timestamps.append(fetched)
            cursor+=len(batch)
            if cursor>=total:
                by_day={}
                for record in records:
                    by_day.setdefault(record.get('date'),[]).append(record)
                current,last=date.fromisoformat(start),date.fromisoformat(end)
                while current<=last:
                    rows=by_day.get(str(current),[])
                    snapshot=dict(messages=[dict(status='ok',total=len(rows))],collection=rows)
                    connection.execute('INSERT INTO day_snapshots VALUES(?,?,?) ON CONFLICT(day) DO UPDATE SET payload=excluded.payload,fetched=excluded.fetched WHERE excluded.fetched>day_snapshots.fetched',
                                       (str(current),json.dumps(snapshot),min(timestamps)))
                    current+=timedelta(days=1)
                connection.commit()
                return
            if not batch:break


def rolling_start(years, today=None):
    """Local calendar anniversaries: a leap-day cutoff falls on February 28."""
    today = today or date.today()
    year = today.year - max(1, int(years))
    if year < 2013:
        return date(2013, 1, 1)
    try:
        return today.replace(year=year)
    except ValueError:
        return date(year, 2, 28)


def search_metadata(session, expression, year_from, year_to, limit, cache_path, notify, requested=None,
                    recent_years=2, start_date=None):
    """Walk recent days then historical months; limit counts unique preprints.

    Each successful page is persisted immediately, including incomplete scans.
    Today is refreshed, recent closed days are cached for an hour, historical
    intervals for 30 days. Failed requests never become empty cached pages. Publication date
    here is the API's posted-version date, not the later peer-reviewed paper date.
    """
    predicate = keyword_predicate(expression)
    today = date.today()
    start = max(date(max(2013, year_from or 2013), 1, 1), rolling_start(recent_years, today))
    if start_date:
        start = max(start, start_date)
    end = min(today, date(year_to or today.year, 12, 31))
    if start > end:
        return []
    path = Path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.execute("CREATE TABLE IF NOT EXISTS pages (url TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched REAL NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS day_snapshots (day TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched REAL NOT NULL)")
    found, seen = [], set()
    scanned = 0
    last_progress = 0
    recent_start = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    seeded_months=set()
    try:
        while end >= start:
            # Finishing a whole current month before testing the candidate budget caused
            # dozens of needless calls. Daily windows allow a newest-first early stop.
            month_start = end if end >= recent_start else max(start, end.replace(day=1))
            if month_start==end and end!=today:
                month_key=end.replace(day=1)
                if month_key not in seeded_months and not connection.execute('SELECT 1 FROM day_snapshots WHERE day=?',(str(end),)).fetchone():
                    seed_daily_snapshots(connection,end)
                    seeded_months.add(month_key)
            cursor = 0
            month_records = {}
            while True:
                if time.monotonic() - last_progress >= 0.15:
                    notify(f"bioRxiv：扫描 {month_start}—{end}，已检查 {scanned} 个版本，匹配 {len(found)} 篇；优先最近日期，复用本地历史缓存。")
                    last_progress=time.monotonic()
                url = f"{API_ROOT}/{month_start}/{end}/{cursor}/json"
                cached = connection.execute("SELECT payload,fetched FROM pages WHERE url=?", (url,)).fetchone()
                if cursor==0 and month_start==end:
                    snapshot=connection.execute('SELECT payload,fetched FROM day_snapshots WHERE day=?',(str(end),)).fetchone()
                    if snapshot and (not cached or snapshot[1]>cached[1]):cached=snapshot
                age = (today - end).days
                lifetime = 0 if age == 0 else 3600 if age <= 2 else 30 * 86400
                # The last refresh day may have gained posts after that search completed.
                # Incremental windows revalidate all their days, including older closed days.
                if cached and start_date is None and time.time() - cached[1] < lifetime:
                    payload = json.loads(cached[0])
                else:
                    if requested:
                        requested()
                    response = session.get(url, timeout=(10, 60))
                    response.raise_for_status()
                    payload = response.json()
                    messages = payload.get("messages") or []
                    if not messages or messages[0].get("status") not in ("ok", "no papers found", "no posts found"):
                        raise ValueError(f"bioRxiv API 返回异常：{str(messages)[:240]}")
                    if messages[0].get('status') in ('no papers found','no posts found'):
                        payload['collection']=[]
                        messages[0]['total']=0
                    elif 'collection' not in payload:
                        raise ValueError('bioRxiv API 缺少文章列表，已停止而非当作空结果。')
                    connection.execute("INSERT OR REPLACE INTO pages VALUES(?,?,?)", (url, json.dumps(payload), time.time()))
                    connection.commit()
                    time.sleep(0.35)
                records = payload.get("collection") or []
                # The API page order is not promised newest-first. Finish an interval before
                # applying the candidate cutoff, and keep the highest version per DOI.
                for raw in records:
                    doi = str(raw.get("doi") or "").lower().strip()
                    if not doi:
                        continue
                    old = month_records.get(doi)
                    if old is None or int(raw.get("version") or 1) > int(old.get("version") or 1):
                        month_records[doi] = raw
                scanned += len(records)
                cursor += len(records)
                message = (payload.get("messages") or [{}])[0]
                if records and message.get("total") is None:
                    raise ValueError("bioRxiv API 未提供分页总数，已停止，避免将不完整结果当作完整检索。")
                total = int(message.get("total") or 0)
                if not records or cursor >= total:
                    break
            for raw in sorted(month_records.values(), key=lambda r: (r.get("date", ""), r.get("doi", "")), reverse=True):
                doi = str(raw["doi"]).lower().strip()
                if doi in seen:
                    continue
                seen.add(doi)
                if predicate((plain_text(raw.get("title")) + "\n" + plain_text(raw.get("abstract"))).casefold()):
                    found.append(raw)
            if limit and len(found) >= limit:
                return found[:limit]
            end = month_start - timedelta(days=1)
        return found
    finally:
        connection.close()
