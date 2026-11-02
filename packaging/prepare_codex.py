"""Bundle the unmodified official CLI with pinned checksums, never user auth."""
import hashlib,json,platform,subprocess,tarfile,time
from pathlib import Path
import requests

root=Path(__file__).resolve().parents[1]
version='rust-v0.166.0'
architectures={
    'arm64':('aarch64','c48824462fc57681eafb0925d97b11b589010475613904f570b42fea53e240d5'),
    'x86_64':('x86_64','db0c38713c36199b12e7c8d1629386c35a4ce8f99e1c9ce72f1725a74df67b57'),
}
arch,digest=architectures[platform.machine()]
name=f'codex-{arch}-apple-darwin'
folder=root/'build/codex-runtime';folder.mkdir(parents=True,exist_ok=True)
archive=folder/'download.tar.gz'
url=f'https://github.com/openai/codex/releases/download/{version}/{name}.tar.gz'
for attempt in range(3):
    try:
        with requests.get(url,stream=True,timeout=(30,120)) as response:
            response.raise_for_status()
            with archive.open('wb') as file:
                for chunk in response.iter_content(1024*1024):file.write(chunk)
        if hashlib.sha256(archive.read_bytes()).hexdigest()!=digest:raise ValueError('Official Codex archive checksum mismatch')
        break
    except (requests.RequestException,OSError):
        if attempt==2:raise
        time.sleep(2**attempt)
with tarfile.open(archive) as package:
    member=package.getmember(name)
    if not member.isfile():raise ValueError('Official Codex payload is not a regular file')
    # Extract the single expected executable, never arbitrary archive paths.
    target=folder/'codex';target.write_bytes(package.extractfile(member).read());target.chmod(0o755)
archive.unlink()
subprocess.run([str(target),'--version'],check=True,timeout=30)
(folder/'manifest.json').write_text(json.dumps(dict(version=version,architecture=arch,archive_sha256=digest,source=url)),encoding='utf8')
licenses=root/'app/assets/third_party_licenses/codex';licenses.mkdir(parents=True,exist_ok=True)
for name in ('LICENSE','NOTICE'):
    response=requests.get(f'https://raw.githubusercontent.com/openai/codex/{version}/{name}',timeout=30)
    response.raise_for_status();(licenses/name).write_bytes(response.content)
print('Official Codex CLI verified and prepared for native bundling.')
