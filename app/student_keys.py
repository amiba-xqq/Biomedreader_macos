"""Startup-only, login-anchored 15-day expiry and cancellable key renewal."""
import json,threading
from datetime import datetime,timedelta

def clear_keys(database,owner):
    from lab_local import account,encrypt
    cached=account(database,owner)
    if not cached or cached['identity'].get('role')!='student':return False
    with database.connect() as c:
        c.execute('UPDATE ai_lab_accounts SET encrypted_credentials=? WHERE owner=?',(encrypt(dict(keys={},student_id=cached.get('student_id',''),members=cached.get('members',[]))),owner))
    return bool(cached.get('keys'))

def logged_in(database,identity,now=None):
    if identity.get('role')!='student':return
    value=(now or datetime.now().astimezone()).isoformat()
    with database.connect() as c:
        install(c);c.execute('INSERT INTO ai_lab_key_expiry VALUES(?,?,0) ON CONFLICT(owner) DO UPDATE SET login_at=excluded.login_at,last_period=0',(identity['local_owner'],value))

def install(c):c.execute('CREATE TABLE IF NOT EXISTS ai_lab_key_expiry(owner TEXT PRIMARY KEY,login_at TEXT NOT NULL,last_period INTEGER NOT NULL)')

def expire_on_startup(store,now=None):
    state=store.lab_state()
    if not state or state['identity'].get('role')!='student':return False
    owner=state['identity'].get('local_owner')
    if not owner:return False
    now=(now or datetime.now()).astimezone()
    with store.database.connect() as c:
        install(c);row=c.execute('SELECT login_at,last_period FROM ai_lab_key_expiry WHERE owner=?',(owner,)).fetchone()
        if row:value,period=row
        else:
            from student_policy import install as legacy_install
            legacy_install(c);old=c.execute('SELECT last_login_at FROM ai_lab_student_checks WHERE owner=?',(owner,)).fetchone()
            if not old:old=c.execute('SELECT updated_at FROM ai_lab_accounts WHERE owner=?',(owner,)).fetchone()
            value=old[0] if old else now.isoformat();period=0
            c.execute('INSERT INTO ai_lab_key_expiry VALUES(?,?,0)',(owner,value))
    try:anchor=datetime.fromisoformat(value).astimezone()
    except (ValueError,TypeError):return False
    due=max(0,(now-anchor)//timedelta(days=15))
    if due<=period:return False
    removed=clear_keys(store.database,owner)
    with store.database.connect() as c:c.execute('UPDATE ai_lab_key_expiry SET last_period=? WHERE owner=?',(due,owner))
    return removed

_locks={};_guard=threading.Lock()
def ensure(store):
    """Called in an AI worker, never in a GUI config getter."""
    from lab_local import account,save_account
    from lab_service import LabAPI,StudentDisabledError
    from student_policy import disable_cached,DISABLED_MESSAGE
    from ai_cancel import check_cancelled
    state=store.lab_state()
    if not state or not state['active'] or state['identity'].get('role')!='student':return
    identity=state['identity'];owner=identity.get('local_owner')
    if identity.get('disabled'):raise ValueError(DISABLED_MESSAGE)
    if not owner:raise ValueError('请重新登录学生ID以同步账户。')
    with _guard:lock=_locks.setdefault((str(store.database.path),owner),threading.Lock())
    while not lock.acquire(timeout=.1):check_cancelled()
    try:
        check_cancelled();cached=account(store.database,owner) or {}
        if cached.get('keys'):return
        access=cached.get('student_id')
        if not access:raise ValueError('本地登录凭证不可用，请重新登录学生ID。')
        gateway=identity.get('gateway_url')
        try:data=LabAPI(gateway=gateway).renew_student(access)
        except StudentDisabledError as error:
            disable_cached(store.database,owner,error.identity);raise
        check_cancelled();current=store.lab_state()
        if not current or not current['active'] or current['fingerprint']!=state['fingerprint'] or current['identity'].get('local_owner')!=owner or current['identity'].get('gateway_url')!=gateway:raise InterruptedError('账户或入口已变化，未保存迟到的密钥。')
        if not data.get('keys'):raise ValueError('课题组暂未返回可用Key，请联系PI检查配置。')
        updated=dict(identity);updated.update(data['identity']);updated.update(gateway_url=gateway)
        updated=save_account(store.database,updated,data['keys'],access,owner=owner,members=cached.get('members',[]))
        with store.database.connect() as c:c.execute("UPDATE ai_lab_session SET identity_json=? WHERE id=1 AND json_extract(identity_json,'$.local_owner')=?",(json.dumps(updated,ensure_ascii=False),owner))
    finally:lock.release()
