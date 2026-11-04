"""Persistent per-role daily login allowance; unrelated to disposable caches."""
from datetime import date

def install(c):
    c.execute('CREATE TABLE IF NOT EXISTS ai_lab_login_daily(role TEXT NOT NULL,day TEXT NOT NULL,used INTEGER NOT NULL,PRIMARY KEY(role,day))')
    c.execute('CREATE TABLE IF NOT EXISTS ai_lab_login_events(id TEXT PRIMARY KEY,role TEXT NOT NULL,day TEXT NOT NULL)')

def remaining(database,role,today=None):
    day=(today or date.today()).isoformat()
    with database.connect() as c:
        install(c);row=c.execute('SELECT used FROM ai_lab_login_daily WHERE role=? AND day=?',(role,day)).fetchone()
    return max(0,20-(row[0] if row else 0))

def success(database,role,identifier,server_used=0,today=None):
    day=(today or date.today()).isoformat()
    with database.connect() as c:
        c.execute('BEGIN IMMEDIATE');install(c)
        if c.execute('SELECT 1 FROM ai_lab_login_events WHERE id=?',(identifier,)).fetchone():return
        row=c.execute('SELECT used FROM ai_lab_login_daily WHERE role=? AND day=?',(role,day)).fetchone()
        used=min(20,max((row[0] if row else 0)+1,int(server_used or 0)))
        c.execute('INSERT INTO ai_lab_login_daily VALUES(?,?,?) ON CONFLICT(role,day) DO UPDATE SET used=excluded.used',(role,day,used))
        c.execute('INSERT INTO ai_lab_login_events VALUES(?,?,?)',(identifier,role,day))
        c.execute('DELETE FROM ai_lab_login_events WHERE day<?',(day,))

def exhausted(database,role):
    with database.connect() as c:
        install(c);c.execute('INSERT INTO ai_lab_login_daily VALUES(?,?,20) ON CONFLICT(role,day) DO UPDATE SET used=20',(role,date.today().isoformat()))
