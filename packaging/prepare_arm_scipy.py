"""Use the ABI-compatible macOS 11 Fortran runtimes from the pinned NumPy wheel."""
from importlib.metadata import distribution,version
import shutil
import subprocess

assert version('numpy')=='1.26.4' and version('scipy')=='1.13.1'

def output(*arguments):
    return subprocess.check_output(arguments,text=True)

def runtime(package,name):
    return distribution(package).locate_file(f'{package}/.dylibs/{name}')

for name in ('libgfortran.5.dylib','libquadmath.0.dylib'):
    compatible = runtime('numpy',name)
    destination = runtime('scipy',name)
    original_symbols = set(output('nm','-arch','arm64','-gjU',str(destination)).splitlines())
    replacement_symbols = set(output('nm','-arch','arm64','-gjU',str(compatible)).splitlines())
    assert original_symbols and original_symbols <= replacement_symbols,name
    original_id = output('otool','-D',str(destination)).splitlines()[-1]
    shutil.copyfile(compatible,destination)
    subprocess.run(['install_name_tool','-id',original_id,str(destination)],check=True)
    subprocess.run(['codesign','--force','--sign','-',str(destination)],check=True)

from scipy import linalg,stats

assert abs(linalg.det([[1.0,2.0],[3.0,4.0]])+2.0)<1e-10
assert stats.ttest_rel([1,2,4],[2,4,7]).pvalue < 1
assert stats.tukey_hsd([1,2,3],[3,4,5]).pvalue.shape==(2,2)
print('Compatible arm64 Fortran runtimes loaded; LAPACK and statistics checks passed.')
