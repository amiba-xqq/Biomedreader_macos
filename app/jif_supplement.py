"""Fill only missing annual metrics from the user's source workbooks.

No journals are added, no existing annual value is replaced, and identifiers
must resolve to one local journal. The 2023 source has names only; its identifier
bridge comes from exact matching names in the two later source tables.
"""
import math
import re
import unicodedata
from collections import defaultdict


def issn_key(value):
    text=re.sub(r'[\s-]','',str(value or '')).upper()
    return text if re.fullmatch(r'\d{7}[\dX]',text) else ''


def title_key(value):
    text=unicodedata.normalize('NFKC',str(value or '')).casefold().replace('&',' and ')
    return ''.join(character for character in text if character.isalnum())


def apply_supplement(connection,payload,stamp):
    aliases=defaultdict(set);titles=defaultdict(set)
    for row in connection.execute('SELECT issn,title,aliases FROM journals'):
        canonical=row['issn']
        for alias in {canonical,*str(row['aliases'] or '').split(';')}:
            if issn_key(alias):aliases[issn_key(alias)].add(canonical)
        titles[title_key(row['title'])].add(canonical)
    candidates=defaultdict(list);unmatched=ambiguous=0
    for row in payload['records']:
        identifiers=row.get('issns') or []
        matches=set().union(*(aliases.get(issn_key(value),set()) for value in identifiers)) if identifiers else set()
        if not matches and not identifiers:matches=titles.get(title_key(row['title']),set())
        if len(matches)!=1:
            if matches:ambiguous+=1
            else:unmatched+=1
            continue
        value=row['jif'];year=row['year']
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
            raise ValueError('内置补充指标包含无效的JIF值。')
        candidates[(next(iter(matches)),year)].append(row)
    counts=defaultdict(int);kept=conflicts=0
    for (issn,year),rows in sorted(candidates.items()):
        if len({row['jif'] for row in rows})!=1:
            conflicts+=1;continue
        row=rows[0]
        old=connection.execute('SELECT jif FROM journal_metrics WHERE issn=? AND year=?',(issn,year)).fetchone()
        if old is not None and old['jif'] is not None:
            kept+=1;continue
        source=f"用户表格：{row['file']} / {row['sheet']} / {row['column']} / 第{row['row']}行"
        connection.execute('INSERT INTO journal_metrics VALUES(?,?,?,?,?) ON CONFLICT(issn,year) DO UPDATE SET '
            'jif=excluded.jif,source=excluded.source,updated_at=excluded.updated_at WHERE journal_metrics.jif IS NULL',
            (issn,year,row['jif'],source,stamp))
        counts[year]+=1
    return dict(added=dict(counts),kept=kept,unmatched=unmatched,ambiguous=ambiguous,conflicts=conflicts)
