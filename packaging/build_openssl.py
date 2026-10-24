"""Build the current Homebrew OpenSSL source for Monterey instead of linking its macOS-15 bottle."""
import hashlib,json,os,re,shutil,subprocess,tarfile,urllib.parse,urllib.request
from pathlib import Path
formula=json.loads(subprocess.check_output(['brew','info','--json=v2','openssl@3']))['formulae'][0]
release=formula['urls']['stable'];url=release['url'];expected=release['checksum']
assert urllib.parse.urlparse(url).hostname in ('github.com','www.openssl.org','openssl.org','openssl-library.org')
assert re.fullmatch(r'[0-9a-f]{64}',expected)
work=Path(os.environ['RUNNER_TEMP'])/'biomed-openssl-build';work.mkdir(parents=True,exist_ok=True)
archive=work/'source.tar.gz';urllib.request.urlretrieve(url,archive)
assert hashlib.sha256(archive.read_bytes()).hexdigest()==expected,'OpenSSL source checksum mismatch'
with tarfile.open(archive) as package:package.extractall(work,filter='data')
source=next(p for p in work.iterdir() if p.is_dir() and (p/'Configure').is_file())
prefix=Path(os.environ['RUNNER_TEMP'])/'biomed-openssl-macos12'
environment=dict(os.environ,MACOSX_DEPLOYMENT_TARGET='12.0',CFLAGS='-mmacosx-version-min=12.0',LDFLAGS='-mmacosx-version-min=12.0')
subprocess.run(['perl','Configure','darwin64-x86_64-cc','no-shared','no-tests','--prefix='+str(prefix)],cwd=source,env=environment,check=True)
subprocess.run(['make','-j3'],cwd=source,env=environment,check=True)
subprocess.run(['make','install_sw'],cwd=source,env=environment,check=True)
licenses=Path(__file__).resolve().parents[1]/'app/assets/third_party_licenses/openssl-source'
licenses.mkdir(parents=True,exist_ok=True)
for path in source.glob('LICENSE*'):
    if path.is_file():shutil.copyfile(path,licenses/path.name)
(licenses/'SOURCE.txt').write_text(url+'\nSHA256 '+expected+'\nDeployment target macOS 12.0; static build\n',encoding='utf8')
with open(os.environ['GITHUB_ENV'],'a',encoding='utf8') as stream:
    stream.write('OPENSSL_DIR='+str(prefix)+'\nOPENSSL_STATIC=1\n')
print('Static OpenSSL built with macOS 12.0 deployment target; verified source SHA256 '+expected)
