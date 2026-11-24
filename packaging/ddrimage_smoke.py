"""Run a native, packaged DDRimage job and verify cleanup with test fixtures."""

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

import numpy as np
from tifffile import imwrite

from ddrimage_output import TEST_PREFIX, clear_test_outputs


def verify(executable, workspace):
    source = workspace / "Images"
    group = source / "Group"
    group.mkdir(parents=True)
    image = group / "trace.tif"
    pixels = np.zeros((160, 160, 3), dtype=np.uint8)
    pixels[70:73, 36:74, 0] = 240
    pixels[70:73, 75:113, 1] = 240
    imwrite(image, pixels, photometric="rgb")
    original = image.read_bytes()
    job = workspace / "worker.json"
    for mode in ("all", "test"):
        job.write_text(json.dumps({
            "jobs": [{"script": "DNA_fiber.py", "label": "DNA fiber",
                      "overrides": {"parent_folder": str(source), "fiber_mode": mode}}],
        }), encoding="utf-8")
        result = subprocess.run([str(executable), "--ddrimage-worker", str(job)], timeout=120)
        assert result.returncode == 0, job.with_suffix(".log").read_text(encoding="utf-8")
    regular = group / "Group_fiber.csv"
    contents = regular.read_bytes()
    assert list(group.glob(TEST_PREFIX + "*"))
    clear_test_outputs(source, "fiber")
    assert not list(group.glob(TEST_PREFIX + "*"))
    assert regular.read_bytes() == contents
    assert image.read_bytes() == original


if __name__ == "__main__":
    with TemporaryDirectory() as folder:
        verify(Path(sys.argv[1]).resolve(strict=True), Path(folder).resolve())
    print("Packaged DDRimage worker and test-output cleanup passed")
