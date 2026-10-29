"""Rebuildable browsing metadata, deliberately independent of search checkpoints."""
import threading
from collections import OrderedDict

FIELDS=('article_key','relevance_score','doi','pmid','publication_date','publication_year','source_name','website_rank','types_json','website_date_filtered')
PATHS=('doi','pmid','publication_date','publication_year','source_name','website_rank','publication_types','website_date_filtered')
EXTRACTS=','.join("json_extract({row}.article_json,'$."+path+"')" for path in PATHS)

def install_schema(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS reading_metadata (
      profile_id INTEGER NOT NULL,source_mode TEXT NOT NULL,article_key TEXT NOT NULL,relevance_score REAL,
      doi TEXT,pmid TEXT,publication_date TEXT,publication_year INTEGER,source_name TEXT,website_rank INTEGER,
      types_json TEXT,website_date_filtered INTEGER,
      PRIMARY KEY(profile_id,source_mode,article_key),
      FOREIGN KEY(profile_id,source_mode,article_key) REFERENCES profile_results(profile_id,source_mode,article_key) ON DELETE CASCADE ON UPDATE CASCADE)''')
    connection.execute('''CREATE TABLE IF NOT EXISTS reading_index_states (
      profile_id INTEGER NOT NULL REFERENCES search_profiles(id) ON DELETE CASCADE,source_mode TEXT NOT NULL,
      revision INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(profile_id,source_mode))''')
    columns='profile_id,source_mode,'+','.join(FIELDS)
    values='NEW.profile_id,NEW.source_mode,NEW.article_key,NEW.relevance_score,'+EXTRACTS.format(row='NEW')
    upsert='INSERT INTO reading_metadata('+columns+') VALUES('+values+') ON CONFLICT(profile_id,source_mode,article_key) DO UPDATE SET '+','.join(name+'=excluded.'+name for name in FIELDS[1:])+';'
    for event,suffix,condition in [
        ('INSERT','insert',''),
        ('UPDATE OF article_json,relevance_score,article_key,profile_id,source_mode','update',
         'WHEN OLD.article_json IS NOT NEW.article_json OR OLD.relevance_score IS NOT NEW.relevance_score OR OLD.article_key IS NOT NEW.article_key OR OLD.profile_id IS NOT NEW.profile_id OR OLD.source_mode IS NOT NEW.source_mode')]:
        connection.execute('CREATE TRIGGER IF NOT EXISTS reading_metadata_'+suffix+' AFTER '+event+' ON profile_results '+condition+' BEGIN '+upsert+
            ' UPDATE reading_index_states SET revision=revision+1 WHERE profile_id=NEW.profile_id AND source_mode=NEW.source_mode; END')
    connection.execute('''CREATE TRIGGER IF NOT EXISTS reading_metadata_delete AFTER DELETE ON profile_results BEGIN
      DELETE FROM reading_metadata WHERE profile_id=OLD.profile_id AND source_mode=OLD.source_mode;
      UPDATE reading_index_states SET revision=revision+1 WHERE profile_id=OLD.profile_id AND source_mode=OLD.source_mode;
      END''')

def revision(connection,profile_id,source_mode):
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reading_index_states'").fetchone():return None
    row=connection.execute('SELECT revision FROM reading_index_states WHERE profile_id=? AND source_mode=?',(profile_id,source_mode)).fetchone()
    if row:return row[0]
    # Only a first visit backfills JSON. The transaction serializes it with
    # streamed updates; the completion flag is committed with all index rows.
    connection.execute('BEGIN IMMEDIATE')
    row=connection.execute('SELECT revision FROM reading_index_states WHERE profile_id=? AND source_mode=?',(profile_id,source_mode)).fetchone()
    if row:return row[0]
    connection.execute('DELETE FROM reading_metadata WHERE profile_id=? AND source_mode=?',(profile_id,source_mode))
    connection.execute('INSERT INTO reading_metadata(profile_id,source_mode,'+','.join(FIELDS)+') SELECT r.profile_id,r.source_mode,r.article_key,r.relevance_score,'+
        EXTRACTS.format(row='r')+' FROM profile_results r WHERE r.profile_id=? AND r.source_mode=?',(profile_id,source_mode))
    connection.execute('INSERT INTO reading_index_states VALUES(?,?,0)',(profile_id,source_mode))
    return 0

def metadata(connection,profile_id,source_mode):
    return [dict(row) for row in connection.execute('SELECT '+','.join(FIELDS)+' FROM reading_metadata WHERE profile_id=? AND source_mode=?',(profile_id,source_mode))]

class ReadingOrderCache:
    """Small, session-only cache. Revision/filter keys prevent stale ordering."""
    def __init__(self):self.values=OrderedDict();self.lock=threading.RLock()
    def get(self,key):
        with self.lock:
            value=self.values.get(key)
            if value is not None:self.values.move_to_end(key)
            return value
    def put(self,key,value):
        with self.lock:
            self.values[key]=value;self.values.move_to_end(key)
            while len(self.values)>4:self.values.popitem(last=False)
