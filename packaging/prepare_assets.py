import gzip,hashlib,json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
for path in (root/'app/assets').glob('*.json.gz'):
    output=path.with_suffix('');data=gzip.decompress(path.read_bytes())
    json.loads(data);output.write_bytes(data)
    print(output.name,hashlib.sha256(data).hexdigest())
