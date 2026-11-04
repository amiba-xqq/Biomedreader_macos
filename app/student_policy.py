"""Local calendar expiry and authoritative student revocation.

No network polling is added to direct AI calls. A remote disable is acted on
when the gateway verifies it; offline copies cannot receive instant revocation.
"""
import json,uuid
from datetime import datetime

DISABLED_MESSAGE='该学生ID已被PI停用，无法使用课题组AI模型。仍可查看本地用量，请联系PI。'

def install(c):
    c.execute('CREATE TABLE IF NOT EXISTS ai_lab_student_checks(owner TEXT PRIMARY KEY,last_login_at TEXT NOT NULL,checked_at TEXT NOT NULL)')

def disable_cached(database,owner,remote_identity=None):
    """Clear only this student's encrypted key collection; retain all usage."""
    from lab_local import account,encrypt
    cached=account(database,owner)
    if not cached:return
    identity=dict(cached['identity']);identity.update(remote_identity or {});identity.update(disabled=True,local_owner=owner)
    with database.connect() as c:
        c.execute('UPDATE ai_lab_accounts SET identity_json=?,encrypted_credentials=? WHERE owner=?',
                  (json.dumps(identity,ensure_ascii=False),encrypt(dict(keys={},student_id=cached.get('student_id',''),members=cached.get('members',[]))),owner))
        row=c.execute('SELECT identity_json FROM ai_lab_session WHERE id=1').fetchone()
        if row and json.loads(row[0]).get('local_owner')==owner:
            c.execute('UPDATE ai_lab_session SET identity_json=? WHERE id=1',(json.dumps(identity,ensure_ascii=False),))

def activate_disabled(store,access,remote_identity):
    from lab_local import owner_for,account,save_account
    owner=owner_for('student',{},access);cached=account(store.database,owner)
    identity=dict((cached or {}).get('identity') or {})
    identity.update(remote_identity or {});identity.update(role='student',disabled=True)
    identity.setdefault('user_id',access.split('.')[0]);identity.setdefault('name','已停用学生');identity.setdefault('providers',[])
    identity=save_account(store.database,identity,{},access,owner=owner,members=(cached or {}).get('members',[]))
    store.save_lab_session('disabled-local-'+uuid.uuid4().hex,identity)
    return identity
