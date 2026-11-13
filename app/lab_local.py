"""Protected, account-isolated lab credentials and rolling local usage.

These are account records, never disposable article/assistant caches. Full
data-directory deletion removes them; ordinary unused-cache collection does not.
"""
import base64,hashlib,json
from datetime import datetime,date
from lab_activity import year_days

def install(c):
    c.execute('CREATE TABLE IF NOT EXISTS ai_lab_accounts(owner TEXT PRIMARY KEY,identity_json TEXT NOT NULL,encrypted_credentials TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS ai_lab_daily(owner TEXT NOT NULL,day TEXT NOT NULL,input_tokens INTEGER NOT NULL,output_tokens INTEGER NOT NULL,total_tokens INTEGER NOT NULL,calls INTEGER NOT NULL,unknown_calls INTEGER NOT NULL,PRIMARY KEY(owner,day))')
    c.execute('CREATE TABLE IF NOT EXISTS ai_lab_call_owners(call_id INTEGER PRIMARY KEY,owner TEXT NOT NULL,day TEXT NOT NULL)')
    from lab_usage_sources import install as install_sources
    install_sources(c)

def encrypt(value):
    from ai_store import _protect
    return base64.b64encode(_protect(json.dumps(value,ensure_ascii=False).encode('utf8'))).decode('ascii')

def decrypt(value):
    from ai_store import _protect
    try:return json.loads(_protect(base64.b64decode(value,validate=True),True).decode('utf8'))
    except Exception:raise ValueError('无法在当前系统用户下读取课题组密钥缓存，请重新登录。') from None

def owner_for(role,keys,student_id=''):
    material=student_id if role=='student' else json.dumps(sorted(keys.items()),ensure_ascii=True,separators=(',',':'))
    return role+':'+hashlib.sha256(material.encode('utf8')).hexdigest()

def save_account(database,identity,keys,student_id='',owner=None,members=None,daily=None):
    from ai_cancel import check_cancelled
    check_cancelled()
    identity=dict(identity);owner=owner or owner_for(identity['role'],keys,student_id)
    identity['local_owner']=owner;now=datetime.now().astimezone().isoformat()
    with database.connect() as c:
        install(c);exists=c.execute('SELECT 1 FROM ai_lab_accounts WHERE owner=?',(owner,)).fetchone()
        c.execute('INSERT INTO ai_lab_accounts VALUES(?,?,?,?,?) ON CONFLICT(owner) DO UPDATE SET identity_json=excluded.identity_json,encrypted_credentials=excluded.encrypted_credentials,updated_at=excluded.updated_at',
                  (owner,json.dumps(identity,ensure_ascii=False),encrypt(dict(keys=keys,student_id=student_id,members=members or [])),now,now))
        # Import legacy gateway usage just once. Subsequent calls are counted
        # locally, so another login cannot double-count a server snapshot.
        if not exists and daily:
            for row in daily:
                if row['user_id']==identity['user_id']:
                    c.execute('INSERT OR IGNORE INTO ai_lab_daily VALUES(?,?,?,?,?,?,?)',(owner,row['day'],*[int(row.get(k) or 0) for k in ('input_tokens','output_tokens','total_tokens','calls','unknown_calls')]))
        if not exists:
            c.execute('INSERT OR IGNORE INTO ai_lab_models SELECT ?,provider,model,enabled,models_json FROM ai_lab_models WHERE owner=?',(owner,identity['user_id']))
        prune(c)
    return identity

def account(database,owner):
    with database.connect() as c:
        install(c);row=c.execute('SELECT * FROM ai_lab_accounts WHERE owner=?',(owner,)).fetchone()
    if not row:return None
    return dict(owner=owner,identity=json.loads(row['identity_json']),**decrypt(row['encrypted_credentials']))

def active_account(store):
    state=store.lab_state()
    return account(store.database,state['identity'].get('local_owner','')) if state and state['active'] else None

def prune(c,today=None):
    cutoff=year_days(today)[0][0]
    c.execute('DELETE FROM ai_lab_daily WHERE day<?',(cutoff,))
    c.execute('DELETE FROM ai_call_usage WHERE id IN (SELECT call_id FROM ai_lab_call_owners WHERE day<?)',(cutoff,))
    c.execute('DELETE FROM ai_lab_call_owners WHERE day<?',(cutoff,))
    c.execute('DELETE FROM ai_lab_daily_sources WHERE day<?',(cutoff,))
    c.execute('DELETE FROM ai_lab_source_days WHERE day<?',(cutoff,))

def record(database,owner,usage,outcome,provider=None):
    if not owner:return
    usage=usage or {};number=lambda k:max(0,int(usage.get(k) or 0))
    with database.connect() as c:
        install(c);prune(c)
        from lab_usage_sources import record as record_source
        record_source(c,owner,date.today().isoformat(),provider,(number('input_tokens'),number('output_tokens'),number('total_tokens'),1,int(not usage.get('known'))))
        c.execute('''INSERT INTO ai_lab_daily VALUES(?,?,?,?,?,?,?) ON CONFLICT(owner,day) DO UPDATE SET
          input_tokens=input_tokens+excluded.input_tokens,output_tokens=output_tokens+excluded.output_tokens,
          total_tokens=total_tokens+excluded.total_tokens,calls=calls+1,unknown_calls=unknown_calls+excluded.unknown_calls''',
          (owner,date.today().isoformat(),number('input_tokens'),number('output_tokens'),number('total_tokens'),1,int(not usage.get('known'))))

def activity(database,identity):
    with database.connect() as c:
        install(c);prune(c)
        from lab_usage_sources import rows as source_rows
        rows=[dict(row,user_id=identity['user_id']) for row in source_rows(c,identity.get('local_owner',''))]
    return dict(days=year_days(),daily=rows)

def credentials(store):
    state=store.lab_state()
    value=account(store.database,state['identity'].get('local_owner','')) if state else None
    return value.get('keys',{}) if value else {}
