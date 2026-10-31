import subprocess,tempfile,sys,json
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'app'))
from mac_word_plugin import pack,unpack
exe=root/'dist/Biomed Reader.app/Contents/MacOS/BiomedReader'
with tempfile.TemporaryDirectory(prefix='BiomedReader-word-smoke-') as directory:
 p=Path(directory)/'request.txt';p.write_text(pack(['']),encoding='utf8')
 result=subprocess.run([str(exe),'--word-mac-command','styles',str(p)],capture_output=True,text=True,timeout=45,check=True)
 parts=unpack(result.stdout)
 expected=json.loads((root/'app/assets/csl/word_catalogue.json').read_text(encoding='utf8'))
 assert parts==['OK',*expected],(parts[:2],len(parts),len(expected))
print('Packaged native Word CLI and journal catalogue verified without opening a user library.')

from mac_word_plugin import compile_bridge,run_bridge
with tempfile.TemporaryDirectory(prefix='BiomedReader-script-smoke-') as folder:
    script=Path(folder)/'BiomedReader.scpt'
    compile_bridge(root/'app/assets/mac_word/BiomedReader.applescript',script,exe,Path.home())
    assert run_bridge(script,'ping',pack(['']))==['OK','PONG']
    assert run_bridge(script,'styles',pack(['']))==['OK',*expected]
    assert run_bridge(script,'invalid-action',pack(['']))[0]=='ERROR'
print('Real compiled AppleScript handler -> frozen executable -> framed response verified.')
