from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from importlib.metadata import distribution, version
import shutil

assert version('numpy') == '2.2.6' and version('scipy') == '1.13.1'


def output(*arguments):
    return subprocess.check_output(arguments, text=True)


# Reuse the same Monterey-compatible Fortran binaries as 2.2.2. The
# OIR reader needs NumPy 2, whose wheel no longer supplies both libraries.
with TemporaryDirectory() as folder:
    subprocess.run([sys.executable, '-m', 'pip', 'download', '--only-binary=:all:',
                    '--no-deps', '--platform', 'macosx_11_0_arm64', '-d', folder,
                    'numpy==1.26.4'], check=True)
    with ZipFile(next(Path(folder).glob('numpy-*.whl'))) as wheel:
        for name in ('libgfortran.5.dylib', 'libquadmath.0.dylib'):
            compatible = Path(wheel.extract('numpy/.dylibs/' + name, folder))
            destination = distribution('scipy').locate_file('scipy/.dylibs/' + name)
            original_symbols = set(output('nm', '-arch', 'arm64', '-gjU', str(destination)).splitlines())
            replacement_symbols = set(output('nm', '-arch', 'arm64', '-gjU', str(compatible)).splitlines())
            assert original_symbols and original_symbols <= replacement_symbols, name
            original_id = output('otool', '-D', str(destination)).splitlines()[-1]
            shutil.copyfile(compatible, destination)
            subprocess.run(['install_name_tool', '-id', original_id, str(destination)], check=True)
            subprocess.run(['codesign', '--force', '--sign', '-', str(destination)], check=True)

from scipy import linalg, stats

assert abs(linalg.det([[1., 2.], [3., 4.]]) + 2.) < 1e-10
assert stats.ttest_rel([1, 2, 4], [2, 4, 7]).pvalue < 1
assert stats.tukey_hsd([1, 2, 3], [3, 4, 5]).pvalue.shape == (2, 2)
print('Compatible arm64 Fortran runtimes loaded; LAPACK and statistics checks passed.')
