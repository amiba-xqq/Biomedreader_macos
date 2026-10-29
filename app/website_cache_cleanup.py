"""Collect expired/orphan website resume HTML, never article or refresh checkpoints."""
import re,sqlite3,shutil,time
from contextlib import closing
from urllib.parse import urlsplit,unquote
from website_progress import WebsiteProgress

def page_keyword(url):
    try:
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.netloc not in ('www.biorxiv.org','www.medrxiv.org') or not parsed.path.startswith('/search/'):return None
        value=unquote(parsed.path[len('/search/'):]);match=re.search(r'\s+jcode:(?:biorxiv|medrxiv)\s+',value)
        if not match:return None
        value=value[:match.start()]
        if value.startswith(('abstract_title:','text_abstract_title:')):value=value.split(':',1)[1].replace('+',' ')
        return ' '.join(value.split()).casefold()
    except (TypeError,ValueError):return None

def clean_website_cache(database,progress=lambda text:None,now=None):
    path=database.path.parent/'website_progress.sqlite3';report=dict(exists=path.exists(),before=0,after=0,removed_pages=0,retained_pages=0,warnings=[])
    if not path.exists():return report
    if path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction()) or path.resolve().parent!=database.path.parent.resolve():raise ValueError('官网进度缓存路径异常，已保留文件。')
    report['before']=path.stat().st_size
    if shutil.disk_usage(path.parent).free<report['before']*2+64*1024**2:raise ValueError('官网缓存压缩所需磁盘空间不足，已保留文件。')
    active=set()
    for profile in database.list_profiles():
        value=profile.get('preprint_query_expression');value=(profile.get('query_expression') or '') if value is None else value
        active.add(' '.join(value.split()).casefold())
    cutoff=(time.time() if now is None else now)-WebsiteProgress.lifetime
    progress('正在清理官网检索的过期分页和已删除方案的临时进度……')
    with closing(sqlite3.connect(path,timeout=30)) as c:
        c.execute('PRAGMA busy_timeout=30000');c.execute('PRAGMA trusted_schema=OFF')
        if not {'job','url','head','saved','html'}.issubset({r[1] for r in c.execute('PRAGMA table_info(website_pages)')}):raise ValueError('官网进度缓存结构未知，已保留文件。')
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name='website_pages'").fetchone():raise ValueError('官网进度缓存包含未知触发器，已保留文件。')
        if c.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('官网进度缓存校验未通过，已保留文件。')
        try:
            c.execute('BEGIN IMMEDIATE');after=None
            while True:
                cursor=c.execute('SELECT rowid,url,head,saved FROM website_pages'+(' WHERE rowid>?' if after is not None else '')+' ORDER BY rowid LIMIT 300',() if after is None else (after,));rows=cursor.fetchall();cursor.close()
                if not rows:break
                for identifier,url,head,saved in rows:
                    key=page_keyword(url)
                    # Unknown URLs are preserved while fresh: no heuristic deletion.
                    expired=not isinstance(saved,(int,float)) or saved<cutoff
                    if expired or not head or (key is not None and key not in active):
                        c.execute('DELETE FROM website_pages WHERE rowid=?',(identifier,));report['removed_pages']+=1
                    else:report['retained_pages']+=1
                after=rows[-1][0]
            c.commit()
        except Exception:c.rollback();raise
        progress('正在压缩官网检索进度缓存……')
        try:
            c.execute('PRAGMA wal_checkpoint(TRUNCATE)');c.execute('VACUUM');c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            if c.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('官网缓存压缩后校验未通过，请保留文件。')
        except sqlite3.Error:report['warnings'].append('官网无用分页已清理，但压缩未完成；请关闭其它软件窗口后重试。')
    report['after']=path.stat().st_size
    return report
