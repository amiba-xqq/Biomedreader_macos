"""Reference-aware collection. Never modify search checkpoints or PDF files."""
import json

def history_keys(*documents):
    keys=set();stack=list(documents)
    while stack:
        value=stack.pop()
        if isinstance(value,list):stack.extend(value)
        elif isinstance(value,dict):
            if isinstance(value.get('article_key'),str):keys.add(value['article_key'])
            for name,item in value.items():
                if name in ('evidence_notes','notes') and isinstance(item,dict):keys.update(k for k in item if isinstance(k,str))
                if name=='article_keys' and isinstance(item,list):keys.update(k for k in item if isinstance(k,str))
                if isinstance(item,(dict,list)):stack.append(item)
    return keys

def index_history(c,identifier,inputs,result):
    c.execute('DELETE FROM ai_history_article_refs WHERE history_id=?',(identifier,))
    c.executemany('INSERT OR IGNORE INTO ai_history_article_refs VALUES(?,?)',[(identifier,key) for key in history_keys(inputs,result)])
    c.execute('INSERT OR IGNORE INTO ai_history_ref_states VALUES(?)',(identifier,))

def backfill_histories(c):
    # Parse a legacy history only once; subsequent collections use indexed refs.
    rows=c.execute('SELECT id,input_json,result_json FROM ai_histories WHERE id NOT IN (SELECT history_id FROM ai_history_ref_states)').fetchall()
    for row in rows:
        try:inputs=json.loads(row['input_json']);result=json.loads(row['result_json'])
        except (ValueError,TypeError):raise ValueError('存在无法解析的AI历史记录，已停止清理并保留数据，请先备份。') from None
        index_history(c,row['id'],inputs,result)

def preserve_favorites(c):
    c.execute("INSERT OR IGNORE INTO ai_summary_links SELECT s.article_key,'favorites',NULL FROM ai_summaries s JOIN favorites f ON f.article_key=s.article_key WHERE f.is_favorite=1")

def collect_unused(c,candidates=None):
    """Run inside the caller's transaction; a failed FK check rolls everything back."""
    summaries_before=c.execute('SELECT COUNT(*) FROM ai_summaries').fetchone()[0]
    backfill_histories(c);preserve_favorites(c)
    c.execute('DELETE FROM ai_finder_cards WHERE history_id NOT IN (SELECT id FROM ai_histories)')
    # Also collect legacy interrupted deletions made before FK enforcement.
    # Dead conversation indexes must not keep attachment/evidence articles live.
    c.execute('DELETE FROM ai_history_article_refs WHERE history_id NOT IN (SELECT id FROM ai_histories)')
    c.execute('DELETE FROM ai_history_ref_states WHERE history_id NOT IN (SELECT id FROM ai_histories)')
    c.execute('DELETE FROM ai_summary_links WHERE profile_id IS NOT NULL AND profile_id NOT IN (SELECT id FROM search_profiles)')
    c.execute("DELETE FROM ai_summary_links WHERE context='favorites' AND article_key NOT IN (SELECT article_key FROM favorites WHERE is_favorite=1)")
    c.execute('CREATE TEMP TABLE cache_report_refs(profile_id INTEGER,article_key TEXT,PRIMARY KEY(profile_id,article_key))')
    try:
        for table,column in [('ai_reports','report_json'),('ai_new_batches','batch_json')]:
            c.execute(f"INSERT OR IGNORE INTO cache_report_refs SELECT r.profile_id,j.value FROM {table} r,json_each(r.{column},'$.keys') j WHERE j.type='text'")
        c.execute("DELETE FROM ai_summary_links WHERE context LIKE 'report:%' AND NOT EXISTS (SELECT 1 FROM cache_report_refs r WHERE r.profile_id=ai_summary_links.profile_id AND r.article_key=ai_summary_links.article_key)")
    finally:c.execute('DROP TABLE cache_report_refs')
    orphan_summaries=c.execute('SELECT article_key FROM ai_summaries WHERE article_key NOT IN (SELECT article_key FROM ai_summary_links) AND article_key NOT IN (SELECT article_key FROM favorites WHERE is_favorite=1)').fetchall()
    c.executemany('INSERT INTO ai_content_epochs VALUES(?,1) ON CONFLICT(article_key) DO UPDATE SET epoch=epoch+1',[(row[0],) for row in orphan_summaries])
    c.executemany('DELETE FROM ai_summaries WHERE article_key=?',[(row[0],) for row in orphan_summaries])
    c.execute('CREATE TEMP TABLE cache_live_keys(article_key TEXT PRIMARY KEY)')
    c.execute('CREATE TEMP TABLE cache_garbage_keys(article_key TEXT PRIMARY KEY)')
    try:
        for sql in [
            'SELECT article_key FROM favorites WHERE is_favorite=1',
            'SELECT article_key FROM favorite_group_articles',
            'SELECT article_key FROM profile_results',
            'SELECT article_key FROM profile_articles',
            'SELECT article_key FROM article_pdfs',
            'SELECT article_key FROM ai_summary_links',
            'SELECT article_key FROM ai_history_article_refs',
            'SELECT article_key FROM ai_finder_cards']:
            c.execute('INSERT OR IGNORE INTO cache_live_keys '+sql)
        for table,column in [('ai_reports','report_json'),('ai_new_batches','batch_json')]:
            c.execute(f"INSERT OR IGNORE INTO cache_live_keys SELECT j.value FROM {table} r,json_each(r.{column},'$.keys') j WHERE j.type='text'")
        # Report summaries being prepared for the current batch stay live even
        # before the user saves the report window.
        c.execute("INSERT OR IGNORE INTO cache_live_keys SELECT article_key FROM ai_summary_links WHERE context LIKE 'report:%'")
        if candidates is None:c.execute('INSERT INTO cache_garbage_keys SELECT article_key FROM articles WHERE article_key NOT IN (SELECT article_key FROM cache_live_keys)')
        else:
            c.executemany('INSERT OR IGNORE INTO cache_garbage_keys VALUES(?)',[(key,) for key in set(candidates)])
            c.execute('DELETE FROM cache_garbage_keys WHERE article_key IN (SELECT article_key FROM cache_live_keys) OR article_key NOT IN (SELECT article_key FROM articles)')
        count=c.execute('SELECT COUNT(*) FROM cache_garbage_keys').fetchone()[0]
        c.execute('INSERT INTO ai_content_epochs SELECT article_key,1 FROM cache_garbage_keys WHERE true ON CONFLICT(article_key) DO UPDATE SET epoch=epoch+1')
        for table in ('doi_visits','profile_hidden_articles','profile_new_articles','favorites'):
            c.execute(f'DELETE FROM {table} WHERE article_key IN (SELECT article_key FROM cache_garbage_keys)')
        c.execute('DELETE FROM articles WHERE article_key IN (SELECT article_key FROM cache_garbage_keys)')
        return dict(articles=count,summaries=summaries_before-c.execute('SELECT COUNT(*) FROM ai_summaries').fetchone()[0])
    finally:
        c.execute('DROP TABLE cache_garbage_keys');c.execute('DROP TABLE cache_live_keys')

def release_report(c,profile_id,new_keys):
    """Retire only previous report-owned summaries; favorites retain their copy."""
    preserve_favorites(c);context='report:'+str(profile_id)
    old=c.execute('SELECT report_json FROM ai_reports WHERE profile_id=?',(profile_id,)).fetchone()
    legacy=set(json.loads(old[0]).get('keys') or []) if old else set()
    owned={row[0] for row in c.execute('SELECT article_key FROM ai_summary_links WHERE context=?',(context,))}
    obsolete=(legacy|owned)-set(new_keys)
    for key in obsolete:
        had=c.execute('SELECT 1 FROM ai_summaries WHERE article_key=?',(key,)).fetchone()
        c.execute('DELETE FROM ai_summary_links WHERE article_key=? AND (context=? OR (context=? AND ?=1))',(key,context,'profile:'+str(profile_id),int(key in legacy)))
        if had and not c.execute('SELECT 1 FROM ai_summaries WHERE article_key=?',(key,)).fetchone():
            c.execute('INSERT INTO ai_content_epochs VALUES(?,1) ON CONFLICT(article_key) DO UPDATE SET epoch=epoch+1',(key,))
    return obsolete
