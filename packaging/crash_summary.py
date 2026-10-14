"""Print only native exception type and sanitized function names from CI crashes.

Never emit report contents, paths, user identifiers, environment or addresses.
"""
import json,re,time,subprocess,sys,sysconfig
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
trace_code="""import sys,runpy
from pathlib import Path
main=Path(sys.argv[1]);sys.path.insert(0,str(main.parent));sys.argv=[str(main),'--browser-self-test']
def trace(frame,event,arg):
    if event=='line' and frame.f_code.co_filename==str(main):print('TRACE_LINE',frame.f_lineno,flush=True)
    return trace
sys.settrace(trace);runpy.run_path(str(main),run_name='__main__')
"""
trace=subprocess.run([sys.executable,'-X','faulthandler','-c',trace_code,str(root/'app/main.py')],cwd=root/'build/source-smoke',capture_output=True,text=True,timeout=60)
print('Last application lines:',re.findall(r'TRACE_LINE (\d+)',trace.stdout)[-20:])
for number,name in re.findall(r'File "[^"]+", line (\d+), in ([A-Za-z0-9_<>]+)',trace.stderr):
    print('Python function:',name,'line',number)
prefix=Path(sysconfig.get_config_var('PYTHONFRAMEWORKPREFIX') or sys.prefix)
framework=prefix/'Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/Python'
executable=str(framework) if framework.exists() else sys.executable
result=subprocess.run(['/usr/bin/lldb','--batch','-o','settings set target.process.stop-on-exec false','-o','settings set target.disable-aslr false','-o','run','-o','bt','--',executable,str(root/'app/main.py'),'--browser-self-test'],cwd=root/'build/source-smoke',capture_output=True,text=True,timeout=90)
for line in result.stdout.splitlines():
    if 'frame #' not in line or '`' not in line:continue
    name=line.split('`',1)[1].split(' at ',1)[0]
    print('Native frame:',re.sub(r'[^A-Za-z0-9_:+(),*<> .~=-]','?',name)[:220])
