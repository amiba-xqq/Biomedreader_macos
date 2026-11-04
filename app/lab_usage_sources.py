"""Provider buckets that preserve legacy totals without guessing their source."""
FIELDS=('input_tokens','output_tokens','total_tokens','calls','unknown_calls')

def install(c):
    c.execute('CREATE TABLE IF NOT EXISTS ai_lab_daily_sources(owner TEXT NOT NULL,day TEXT NOT NULL,provider TEXT NOT NULL,input_tokens INTEGER NOT NULL,output_tokens INTEGER NOT NULL,total_tokens INTEGER NOT NULL,calls INTEGER NOT NULL,unknown_calls INTEGER NOT NULL,PRIMARY KEY(owner,day,provider))')
    c.execute('CREATE TABLE IF NOT EXISTS ai_lab_source_days(owner TEXT NOT NULL,day TEXT NOT NULL,PRIMARY KEY(owner,day))')
    c.execute('CREATE INDEX IF NOT EXISTS ai_lab_call_owner_day ON ai_lab_call_owners(owner,day)')

def prepare_day(c,owner,day):
    install(c)
    if c.execute('SELECT 1 FROM ai_lab_source_days WHERE owner=? AND day=?',(owner,day)).fetchone():return
    old=c.execute('SELECT input_tokens,output_tokens,total_tokens,calls,unknown_calls FROM ai_lab_daily WHERE owner=? AND day=?',(owner,day)).fetchone()
    if old:
        known=c.execute('''SELECT u.provider,SUM(u.input_tokens),SUM(u.output_tokens),SUM(u.total_tokens),COUNT(*),SUM(u.usage_known=0)
          FROM ai_call_usage u JOIN ai_lab_call_owners a ON a.call_id=u.id
          WHERE a.owner=? AND a.day=? GROUP BY u.provider''',(owner,day)).fetchall()
        sums=[sum(row[i+1] for row in known) for i in range(5)]
        if any(sums[i]>old[i] for i in range(5)):
            # Partial old writes cannot be attributed reliably. Keep the
            # canonical daily aggregate, rather than inflate it or guess.
            known=[];sums=[0]*5
        for row in known:c.execute('INSERT OR IGNORE INTO ai_lab_daily_sources VALUES(?,?,?,?,?,?,?,?)',(owner,day,*row))
        rest=[old[i]-sums[i] for i in range(5)]
        if any(rest):c.execute('INSERT OR IGNORE INTO ai_lab_daily_sources VALUES(?,?,?,?,?,?,?,?)',(owner,day,'legacy',*rest))
    c.execute('INSERT OR IGNORE INTO ai_lab_source_days VALUES(?,?)',(owner,day))

def record(c,owner,day,provider,values):
    prepare_day(c,owner,day)
    c.execute('''INSERT INTO ai_lab_daily_sources VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(owner,day,provider) DO UPDATE SET
      input_tokens=input_tokens+excluded.input_tokens,output_tokens=output_tokens+excluded.output_tokens,
      total_tokens=total_tokens+excluded.total_tokens,calls=calls+excluded.calls,unknown_calls=unknown_calls+excluded.unknown_calls''',
      (owner,day,provider or 'legacy',*values))

def rows(c,owner):
    install(c)
    for row in c.execute('SELECT day FROM ai_lab_daily WHERE owner=? AND day NOT IN (SELECT day FROM ai_lab_source_days WHERE owner=?)',(owner,owner)).fetchall():prepare_day(c,owner,row[0])
    return [dict(row) for row in c.execute('SELECT day,provider,input_tokens,output_tokens,total_tokens,calls,unknown_calls FROM ai_lab_daily_sources WHERE owner=? ORDER BY day,provider',(owner,))]
