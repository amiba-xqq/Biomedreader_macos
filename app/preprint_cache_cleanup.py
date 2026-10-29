"""Reference-aware pruning of legacy API metadata without claiming full pages."""
import json,re,sqlite3,shutil
from contextlib import closing

PARTIAL_FLAG='_biomed_partial'

def normalize_doi(value):
    value=str(value or '').strip().casefold()
    for prefix in ('https://doi.org/','http://doi.org/','https://dx.doi.org/','doi:','biorxiv:','medrxiv:'):
        if value.startswith(prefix):value=value[len(prefix):]
    value=re.sub(r'v\d+$','',value)
    return value if re.fullmatch(r'10\.\d{4,9}/[^\s<>"?#]+',value) else ''

def live_dois(database):
    """Run after main collection; include saved AI-only evidence, not just favorites."""
    result=set()
    with database.connect() as c:
        for row in c.execute("SELECT doi FROM articles WHERE doi<>''"):
            doi=normalize_doi(row[0])
            if doi:result.add(doi)
        for row in c.execute('SELECT article_key FROM ai_history_article_refs'):
            doi=normalize_doi(row[0])
            if doi:result.add(doi)
        for table,columns in [('ai_histories',('input_json','result_json')),('ai_finder_cards',('article_json',))]:
            for column in columns:
                for row in c.execute(f"SELECT j.atom FROM {table} h,json_tree(h.{column}) j WHERE j.key='doi' AND j.type='text'"):
                    doi=normalize_doi(row[0])
                    if doi:result.add(doi)
    return result

def cache_rows(connection,table,key):
    # Close every read cursor before mutating its table; only one raw page is
    # held in memory, including when a legacy daily snapshot is very large.
    after=None
    while True:
        cursor=connection.execute(f'SELECT rowid,{key},payload FROM {table}'+(' WHERE rowid>?' if after is not None else '')+' ORDER BY rowid LIMIT 1',() if after is None else (after,))
        row=cursor.fetchone();cursor.close()
        if row is None:return
        after=row[0];yield row[1],row[2]

def clean_preprint_cache(database,progress=lambda text:None):
    path=database.path.parent/'biorxiv_metadata.sqlite3'
    report=dict(exists=False,before=0,after=0,removed_entries=0,removed_pages=0,trimmed_pages=0,warnings=[])
    if not path.exists():return report
    if path.is_symlink() or path.is_junction() or path.resolve().parent!=database.path.parent.resolve():raise ValueError('预印本缓存路径是链接或超出本地数据目录，未处理。')
    if not path.is_file():raise ValueError('预印本缓存路径不是普通文件，未处理。')
    report.update(exists=True,before=path.stat().st_size,after=path.stat().st_size)
    if shutil.disk_usage(path.parent).free<report['before']*2+64*1024*1024:raise ValueError('预印本缓存压缩所需磁盘空间不足，未开始清理该文件。')
    progress('正在核对预印本缓存与现有方案、收藏、AI对话和报告的引用……');keep=live_dois(database)
    with closing(sqlite3.connect(path,timeout=30)) as c:
        c.execute('PRAGMA busy_timeout=30000');c.execute('PRAGMA trusted_schema=OFF')
        tables={row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        known=tables.intersection({'pages','day_snapshots'})
        if not known:raise ValueError('预印本缓存结构无法识别，已保留文件。')
        for table,key in [('pages','url'),('day_snapshots','day')]:
            if table not in known:continue
            if not {key,'payload','fetched'}.issubset({r[1] for r in c.execute(f'PRAGMA table_info({table})')}):raise ValueError('预印本缓存表结构不匹配，已保留文件。')
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name=?",(table,)).fetchone():raise ValueError('预印本缓存包含未知触发器，已保留文件。')
        if c.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('预印本缓存完整性校验未通过，已保留文件。')
        try:
            c.execute('BEGIN IMMEDIATE')
            for table,key in [('pages','url'),('day_snapshots','day')]:
                if table not in known:continue
                for identifier,raw in cache_rows(c,table,key):
                    try:payload=json.loads(raw)
                    except (TypeError,ValueError):raise ValueError('预印本缓存含无法解析的数据，已撤销该文件本次清理。') from None
                    if not isinstance(payload,dict) or not isinstance(payload.get('collection'),list) or any(not isinstance(r,dict) for r in payload['collection']):raise ValueError('预印本缓存文章列表格式异常，已撤销该文件本次清理。')
                    records=payload['collection']
                    # Records without valid identifiers cannot be proved unused.
                    retained=[r for r in records if not normalize_doi(r.get('doi')) or normalize_doi(r.get('doi')) in keep]
                    report['removed_entries']+=len(records)-len(retained)
                    if not retained:
                        c.execute(f'DELETE FROM {table} WHERE {key}=?',(identifier,));report['removed_pages']+=1
                    elif len(retained)!=len(records):
                        payload['collection']=retained;payload[PARTIAL_FLAG]=True
                        c.execute(f'UPDATE {table} SET payload=? WHERE {key}=?',(json.dumps(payload,ensure_ascii=False,separators=(',',':')),identifier));report['trimmed_pages']+=1
            c.commit()
        except Exception:c.rollback();raise
        progress('正在压缩预印本元数据缓存并回收磁盘空间……')
        try:
            c.execute('PRAGMA wal_checkpoint(TRUNCATE)');c.execute('VACUUM');c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            if c.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('预印本缓存压缩后校验未通过，请保留文件并联系开发者。')
        except sqlite3.Error:report['warnings'].append('预印本无用记录已清理，但压缩未完成；可能有其它程序占用，可关闭其它窗口后重试。')
    report['after']=path.stat().st_size
    return report
