"""Print only native exception type and sanitized function names from CI crashes.

Never emit report contents, paths, user identifiers, environment or addresses.
"""
import json,re,time
from pathlib import Path
for report in (Path.home()/'Library/Logs/DiagnosticReports').glob('*.ips'):
    if time.time()-report.stat().st_mtime>900:continue
    try:
        content=report.read_text()
        metadata,body=content.split('\n',1)
        data=json.loads(body)
        if data.get('procName') not in ('BiomedReader','Python','QtWebEngineProcess'):continue
        exception=data.get('exception',{})
        print('Native exception:',re.sub(r'[^A-Za-z0-9_ ]','',exception.get('type','unknown')))
        for thread in data.get('threads',[]):
            if not thread.get('triggered'):continue
            for frame in thread.get('frames',[])[:25]:
                symbol=frame.get('symbol','unnamed native frame')
                print(re.sub(r'[^A-Za-z0-9_:+(),*<> .~=-]','?',symbol)[:220])
    except (OSError,ValueError):print('Crash summary unavailable')
