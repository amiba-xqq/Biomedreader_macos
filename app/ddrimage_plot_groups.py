"""Keep DDRimage plot controls and CSV columns in the same group order."""

import csv
from pathlib import Path

from ddrimage_output import plot_statistics


SUMMARY_PATTERNS = {
    "foci": ("*_foci_number.csv",),
    "intensity": ("*_integrated_density.csv",),
    "fiber": ("DNA_replication_speed.csv", "Fiber_length_ratio.csv"),
}


def plot_folders(root, workflow):
    folders = {p.name: p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")}
    files, _ = plot_statistics(root, workflow)
    if files is not None:
        groups = {Path(name).parent.name for name in files
                  if len(Path(name).parts) == 2 and Path(name).suffix == ".csv"}
        folders = {name: folder for name, folder in folders.items() if name in groups}
    for pattern in SUMMARY_PATTERNS[workflow]:
        for table in sorted(root.glob(pattern)):
            try:
                with table.open(encoding="utf-8-sig", newline="") as stream:
                    names = next(csv.reader(stream), [])
            except (OSError, UnicodeError, csv.Error):
                continue
            if len(names) == len(folders) and set(names) == folders.keys():
                return [folders[name] for name in names]
    return [folders[name] for name in sorted(folders, key=lambda name: (name.casefold(), name))]
