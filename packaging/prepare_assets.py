import gzip,hashlib,json,importlib.metadata,shutil
from pathlib import Path
root=Path(__file__).resolve().parents[1]
for path in (root/'app/assets').glob('*.json.gz'):
    output=path.with_suffix('');data=gzip.decompress(path.read_bytes())
    json.loads(data);output.write_bytes(data)
    print(output.name,hashlib.sha256(data).hexdigest())
for name in ('pdfplumber','pdfminer.six','pypdfium2','cryptography','citeproc-py','lxml'):
    distribution=importlib.metadata.distribution(name)
    for relative in distribution.files or ():
        if not any(part.lower().startswith(('license','copying','notice','authors')) for part in relative.parts):continue
        source=Path(distribution.locate_file(relative))
        if not source.is_file():continue
        destination=root/'app/assets/third_party_licenses/macos-runtime'/name/relative
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,destination)
