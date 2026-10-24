"""Exercise the frozen PDF parser and resources against a generated fixture."""
import json,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'app'),str(root/'tests')]
from test_citation_pdf_21 import make_pdf
directory=root/'build/pdf-smoke';directory.mkdir(parents=True,exist_ok=True)
fixture=directory/'fixture.pdf';report=directory/'report.json';make_pdf(fixture)
subprocess.run([str(root/'dist/Biomed Reader.app/Contents/MacOS/BiomedReader'),'--pdf-self-test',str(report),str(fixture)],check=True,timeout=60)
data=json.loads(report.read_text(encoding='utf8'))
assert data['passed']
assert data['files'][0]['title']=='A complete scientific article title continuing on its second line'
print('Frozen PDF parsing and complete wrapped title verified.')
