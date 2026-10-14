"""Fail release if a bundled binary requires a macOS version later than Monterey."""
import re,subprocess,sys,plistlib
from pathlib import Path
app=Path(sys.argv[1]);plist=plistlib.loads((app/'Contents/Info.plist').read_bytes())
assert plist['LSMinimumSystemVersion']=='12.0'
magic={b'\xfe\xed\xfa\xce',b'\xce\xfa\xed\xfe',b'\xfe\xed\xfa\xcf',b'\xcf\xfa\xed\xfe',b'\xca\xfe\xba\xbe',b'\xbe\xba\xfe\xca'}
count=0
for path in app.rglob('*'):
    if not path.is_file() or path.is_symlink():continue
    with path.open('rb') as file:header=file.read(4)
    if header not in magic:continue
    output=subprocess.run(['vtool','-show-build',str(path)],capture_output=True,text=True,check=True).stdout
    versions=re.findall(r'^\s*(?:minos|version)\s+(\d+)\.(\d+)',output,re.M)
    assert versions,(path,'No deployment target')
    assert all((int(major),int(minor))<=(12,0) for major,minor in versions),(path,versions)
    count+=1
assert count>20,count
print(f'{count} Mach-O binaries verified for macOS 12 deployment target.')
