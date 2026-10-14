"""Print only native exception type and sanitized function names from CI crashes.

Never emit report contents, paths, user identifiers, environment or addresses.
"""
import json,re,time,subprocess,sys
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
# Hosted runners may not save .ips reports. Capture the debugger in memory and
# emit only function names, never raw output, addresses, paths or variables.
root=Path(__file__).resolve().parents[1]
result=subprocess.run(['/usr/bin/lldb','--batch','-o','run','-o','bt','--',sys.executable,str(root/'app/main.py'),'--browser-self-test'],cwd=root/'build/source-smoke',capture_output=True,text=True,timeout=90)
for line in result.stdout.splitlines():
    if 'frame #' not in line or '`' not in line:continue
    name=line.split('`',1)[1].split(' at ',1)[0]
    print('Native frame:',re.sub(r'[^A-Za-z0-9_:+(),*<> .~=-]','?',name)[:220])
