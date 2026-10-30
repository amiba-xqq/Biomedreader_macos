"""Saved AI outputs and lightweight report references, independent of search baselines."""
import json
from datetime import datetime,timezone

def now():return datetime.now(timezone.utc).isoformat()
def install_schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS ai_histories(id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,title TEXT NOT NULL,input_json TEXT NOT NULL,result_json TEXT NOT NULL,created_at TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS ai_summaries(article_key TEXT PRIMARY KEY REFERENCES articles(article_key) ON DELETE CASCADE,
        result_json TEXT NOT NULL,created_at TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS ai_summary_links(article_key TEXT NOT NULL REFERENCES ai_summaries(article_key) ON DELETE CASCADE,
        context TEXT NOT NULL,profile_id INTEGER REFERENCES search_profiles(id) ON DELETE CASCADE,PRIMARY KEY(article_key,context))''')
    c.execute('''CREATE TABLE IF NOT EXISTS ai_content_epochs(article_key TEXT PRIMARY KEY,epoch INTEGER NOT NULL DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS ai_reports(profile_id INTEGER PRIMARY KEY REFERENCES search_profiles(id) ON DELETE CASCADE,
        report_json TEXT NOT NULL,created_at TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS ai_new_batches(profile_id INTEGER PRIMARY KEY REFERENCES search_profiles(id) ON DELETE CASCADE,batch_json TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS ai_finder_cards(history_id INTEGER NOT NULL REFERENCES ai_histories(id) ON DELETE CASCADE,article_key TEXT NOT NULL,article_json TEXT NOT NULL,PRIMARY KEY(history_id,article_key))''')
    c.execute('CREATE TABLE IF NOT EXISTS ai_history_article_refs(history_id INTEGER NOT NULL REFERENCES ai_histories(id) ON DELETE CASCADE,article_key TEXT NOT NULL,PRIMARY KEY(history_id,article_key))')
    c.execute('CREATE TABLE IF NOT EXISTS ai_history_ref_states(history_id INTEGER PRIMARY KEY REFERENCES ai_histories(id) ON DELETE CASCADE)')
    c.execute('DROP TRIGGER IF EXISTS ai_remove_orphan_summary')
    c.execute('''CREATE TRIGGER IF NOT EXISTS ai_remove_orphan_summary AFTER DELETE ON ai_summary_links BEGIN
        DELETE FROM ai_summaries WHERE article_key=OLD.article_key AND NOT EXISTS
            (SELECT 1 FROM ai_summary_links WHERE article_key=OLD.article_key) AND NOT EXISTS
            (SELECT 1 FROM favorites WHERE article_key=OLD.article_key AND is_favorite=1); END''')
    for operation in ('INSERT','UPDATE OF is_favorite'):
        suffix='insert' if operation=='INSERT' else 'update'
        c.execute(f'''CREATE TRIGGER IF NOT EXISTS ai_keep_favorite_{suffix} AFTER {operation} ON favorites WHEN NEW.is_favorite=1 BEGIN
            INSERT OR IGNORE INTO ai_summary_links SELECT article_key,'favorites',NULL FROM ai_summaries WHERE article_key=NEW.article_key; END''')
    c.execute('''CREATE TRIGGER IF NOT EXISTS ai_cancel_favorite AFTER UPDATE OF is_favorite ON favorites
        WHEN OLD.is_favorite=1 AND NEW.is_favorite=0 BEGIN
        DELETE FROM ai_summaries WHERE article_key=NEW.article_key;
        INSERT INTO ai_content_epochs VALUES(NEW.article_key,1) ON CONFLICT(article_key) DO UPDATE SET epoch=epoch+1; END''')

class AIRepository:
    def __init__(self,database):self.database=database
    def save_history(self,kind,title,inputs,result,identifier=None):
        from cache_cleanup import index_history
        with self.database.connect() as c:
            if identifier is not None and c.execute('SELECT 1 FROM ai_histories WHERE id=? AND kind=?',(identifier,kind)).fetchone():
                c.execute('UPDATE ai_histories SET title=?,input_json=?,result_json=?,created_at=? WHERE id=?',(title[:300],json.dumps(inputs,ensure_ascii=False),json.dumps(result,ensure_ascii=False),now(),identifier));index_history(c,identifier,inputs,result);return identifier
            if identifier is not None:raise ValueError('此AI对话已删除，未恢复已删除的缓存。')
            cursor=c.execute('INSERT INTO ai_histories(kind,title,input_json,result_json,created_at) VALUES(?,?,?,?,?)',
                (kind,title[:300],json.dumps(inputs,ensure_ascii=False),json.dumps(result,ensure_ascii=False),now()))
            index_history(c,cursor.lastrowid,inputs,result);return cursor.lastrowid
    def histories(self,kind):
        with self.database.connect() as c:return [dict(row) for row in c.execute('SELECT id,title,created_at FROM ai_histories WHERE kind=? ORDER BY created_at DESC,id DESC',(kind,))]
    def history(self,identifier):
        with self.database.connect() as c:row=c.execute('SELECT * FROM ai_histories WHERE id=?',(identifier,)).fetchone()
        return {**dict(row),'inputs':json.loads(row['input_json']),'result':json.loads(row['result_json'])} if row else None
    def delete_history(self,identifier):
        from cache_cleanup import backfill_histories,collect_unused
        with self.database.connect() as c:
            backfill_histories(c);keys={r[0] for r in c.execute('SELECT article_key FROM ai_history_article_refs WHERE history_id=?',(identifier,))}
            keys.update(r[0] for r in c.execute('SELECT article_key FROM ai_finder_cards WHERE history_id=?',(identifier,)))
            c.execute('DELETE FROM ai_histories WHERE id=?',(identifier,));collect_unused(c,keys)
    def replace_finder_cards(self,identifier,articles):
        with self.database.connect() as c:
            if not c.execute('SELECT 1 FROM ai_histories WHERE id=?',(identifier,)).fetchone():return False
            c.execute('DELETE FROM ai_finder_cards WHERE history_id=?',(identifier,))
            c.executemany('INSERT INTO ai_finder_cards VALUES(?,?,?)',[(identifier,row['article_key'],json.dumps(row,ensure_ascii=False)) for row in articles])
    def finder_cards(self,identifier):
        with self.database.connect() as c:return [json.loads(row[0]) for row in c.execute('SELECT article_json FROM ai_finder_cards WHERE history_id=?',(identifier,))]
    def replace_finder_conversation(self,identifier,inputs,result,articles):
        """Atomically delete a turn and its unshared cache, protecting favorites.

        Updating the reverse-reference index before collection is essential:
        otherwise deleted evidence would still keep its own cache alive.
        """
        from cache_cleanup import index_history,collect_unused
        with self.database.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if not c.execute("SELECT 1 FROM ai_histories WHERE id=? AND kind='finder'",(identifier,)).fetchone():
                raise ValueError('此对话已删除。')
            old={r[0] for r in c.execute('SELECT article_key FROM ai_history_article_refs WHERE history_id=?',(identifier,))}
            old.update(r[0] for r in c.execute('SELECT article_key FROM ai_finder_cards WHERE history_id=?',(identifier,)))
            c.execute('UPDATE ai_histories SET input_json=?,result_json=?,created_at=? WHERE id=?',
                      (json.dumps(inputs,ensure_ascii=False),json.dumps(result,ensure_ascii=False),now(),identifier))
            index_history(c,identifier,inputs,result)
            c.execute('DELETE FROM ai_finder_cards WHERE history_id=?',(identifier,))
            c.executemany('INSERT INTO ai_finder_cards VALUES(?,?,?)',[(identifier,a['article_key'],json.dumps(a,ensure_ascii=False)) for a in articles])
            collect_unused(c,old)
    def clear_finder_cards(self,identifier):
        with self.database.connect() as c:c.execute('DELETE FROM ai_finder_cards WHERE history_id=?',(identifier,))
    def epoch(self,key):
        with self.database.connect() as c:row=c.execute('SELECT epoch FROM ai_content_epochs WHERE article_key=?',(key,)).fetchone()
        return row[0] if row else 0
    def summary(self,key):
        with self.database.connect() as c:row=c.execute('SELECT result_json FROM ai_summaries WHERE article_key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else None
    def save_summary(self,key,result,context,epoch=None):
        with self.database.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            profile_id=int(context.split(':')[1]) if context.startswith(('profile:','report:')) else None
            if profile_id is not None and not c.execute('SELECT 1 FROM search_profiles WHERE id=?',(profile_id,)).fetchone():raise ValueError('关键词方案已删除，未保存迟到的 AI 结果。')
            if context=='favorites' and not c.execute('SELECT 1 FROM favorites WHERE article_key=? AND is_favorite=1',(key,)).fetchone():raise ValueError('文章已取消收藏，未保存迟到的 AI 结果。')
            row=c.execute('SELECT epoch FROM ai_content_epochs WHERE article_key=?',(key,)).fetchone()
            if epoch is not None and (row[0] if row else 0)!=epoch:raise ValueError('文章总结已删除或文章已取消收藏，未恢复旧请求的结果。')
            c.execute('INSERT INTO ai_summaries VALUES(?,?,?) ON CONFLICT(article_key) DO UPDATE SET result_json=excluded.result_json,created_at=excluded.created_at',
                (key,json.dumps(result,ensure_ascii=False),now()))
            c.execute('INSERT OR IGNORE INTO ai_summary_links VALUES(?,?,?)',(key,context,profile_id))
            from cache_cleanup import preserve_favorites
            preserve_favorites(c)
    def delete_summary(self,key):
        with self.database.connect() as c:
            c.execute('DELETE FROM ai_summaries WHERE article_key=?',(key,))
            c.execute('INSERT INTO ai_content_epochs VALUES(?,1) ON CONFLICT(article_key) DO UPDATE SET epoch=epoch+1',(key,))
    def report(self,profile_id):
        with self.database.connect() as c:row=c.execute('SELECT report_json FROM ai_reports WHERE profile_id=?',(profile_id,)).fetchone()
        return json.loads(row[0]) if row else None
    def save_batch(self,profile_id,batch):
        with self.database.connect() as c:
            if c.execute('SELECT 1 FROM search_profiles WHERE id=?',(profile_id,)).fetchone():
                c.execute('INSERT INTO ai_new_batches VALUES(?,?) ON CONFLICT(profile_id) DO UPDATE SET batch_json=excluded.batch_json',(profile_id,json.dumps(batch,ensure_ascii=False)))
    def batch(self,profile_id):
        with self.database.connect() as c:row=c.execute('SELECT batch_json FROM ai_new_batches WHERE profile_id=?',(profile_id,)).fetchone()
        return json.loads(row[0]) if row else None
    def save_report(self,profile_id,report):
        if not report.get('keys'):return False
        with self.database.connect() as c:
            if not c.execute('SELECT 1 FROM search_profiles WHERE id=?',(profile_id,)).fetchone():return False
            from cache_cleanup import release_report
            release_report(c,profile_id,report['keys'])
            c.execute('INSERT INTO ai_reports VALUES(?,?,?) ON CONFLICT(profile_id) DO UPDATE SET report_json=excluded.report_json,created_at=excluded.created_at',
                (profile_id,json.dumps(report,ensure_ascii=False),now()))
        return True

    def retire_previous_report(self,profile_id,new_keys):
        if not new_keys:return
        from cache_cleanup import release_report
        with self.database.connect() as c:
            release_report(c,profile_id,new_keys)
            c.execute('DELETE FROM ai_reports WHERE profile_id=?',(profile_id,))
