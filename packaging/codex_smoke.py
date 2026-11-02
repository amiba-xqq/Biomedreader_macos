"""Exercise the packaged app and native Codex RPC with an empty private home.

No account login, inference request, user credentials or user library are used.
"""
import json,subprocess,tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
executable=root/'dist/Biomed Reader.app/Contents/MacOS/BiomedReader'
with tempfile.TemporaryDirectory(prefix='biomed-codex-smoke-') as folder:
    subprocess.run([str(executable),'--codex-self-test',folder],timeout=90,check=True)
    report=json.loads((Path(folder)/'codex-report.json').read_text())
    assert report['passed'] and report['account_is_empty']
print('Frozen app resolved the bundled native Codex and completed initialize/account/read without login or inference.')
