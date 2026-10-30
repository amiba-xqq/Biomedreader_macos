import subprocess,tempfile,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'app'))
from mac_word_plugin import pack,unpack
exe=root/'dist/Biomed Reader.app/Contents/MacOS/BiomedReader'
with tempfile.TemporaryDirectory(prefix='BiomedReader-word-smoke-') as directory:
 p=Path(directory)/'request.txt';p.write_text(pack(['']),encoding='utf8')
 result=subprocess.run([str(exe),'--word-mac-command','styles',str(p)],capture_output=True,text=True,timeout=45,check=True)
 parts=unpack(result.stdout)
 assert parts[0]=='OK' and 'Cell' in parts[1:] and len(parts)>100,parts[:2]
print('Packaged native Word CLI and journal catalogue verified without opening a user library.')
