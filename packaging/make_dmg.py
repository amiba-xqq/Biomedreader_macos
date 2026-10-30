"""Bounded retries for transient disk-image device contention on hosted runners."""
import subprocess,sys,time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
source=(root/'build/dmg').resolve();target=(root/'outputs'/sys.argv[1]).resolve()
assert source.is_relative_to(root) and target.parent==root/'outputs'
assert target.suffix=='.dmg'
for attempt in range(4):
    command=['hdiutil','create','-fs','HFS+','-volname','Biomed Reader','-srcfolder',str(source),'-format','UDZO','-ov',str(target)]
    result=subprocess.run(command,capture_output=True,text=True)
    print(result.stdout,flush=True);print(result.stderr,flush=True)
    if result.returncode==0:break
    if 'Resource busy' not in result.stderr or attempt==3:raise SystemExit(result.returncode)
    # hdiutil cleans up its temporary mount/device after returning. Allow that
    # cleanup to finish; do not detach any unrelated volume on the runner.
    time.sleep(10*(attempt+1))
subprocess.run(['hdiutil','verify',str(target)],check=True)
