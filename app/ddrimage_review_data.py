"""Reproducible per-image inputs and edits for DDRimage review."""

from hashlib import sha256
from pathlib import Path
import json

import numpy as np


REVIEW_DIR = ".ddrimage-review"
REVIEW_INDEX = f"{REVIEW_DIR}/index.json"
REVIEW_WORKFLOWS = {"foci": "foci_mode", "intensity": "foci_mode", "fiber": "fiber_mode", "colony": "mode"}


def stamp(path):
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns]


def nucleus_labels(original, edit):
    excluded = edit.get("excluded", [])
    groups = edit.get("merged", [])
    lookup = np.arange(int(original.max()) + 1, dtype=np.int32)
    ids = set(np.unique(original)) - {0}
    used = set()
    for group in groups:
        members = set(group)
        if len(members) < 2 or not members <= ids or members & used:
            raise ValueError("合并标记与原核轮廓不匹配。")
        lookup[group] = min(group)
        used.update(members)
    if not set(excluded) <= ids or any(len(set(group) & set(excluded)) not in (0, len(group)) for group in groups):
        raise ValueError("排除标记与原核轮廓不匹配。")
    lookup[excluded] = 0
    labels = lookup[original]
    # Fill only a one-pixel watershed seam between different selected fragments.
    for group in groups:
        if group[0] in excluded:
            continue
        target = min(group)
        for axis in (0, 1):
            ids_axis = np.swapaxes(original, 0, axis)
            out = np.swapaxes(labels, 0, axis)
            a, b = ids_axis[:-2], ids_axis[2:]
            seam = (ids_axis[1:-1] == 0) & (out[1:-1] == 0) & (a != b)
            seam &= (lookup[a] == target) & (lookup[b] == target)
            out[1:-1][seam] = target
    return labels


def neighboring_nuclei(labels, first, second):
    """Compare centroid distance with the larger maximum Feret diameter."""
    from math import dist
    from skimage.measure import regionprops

    a, b = regionprops(np.where(labels == first, 1, np.where(labels == second, 2, 0)))
    return dist(a.centroid, b.centroid) <= max(a.feret_diameter_max, b.feret_diameter_max)


class ReviewRun:
    def __init__(self, source, directory, index=None, edits=None, images=None):
        self.source = source
        self.directory = directory
        self.frames = {} if index is None else index["frames"]
        self.edits = edits or {}
        self.images = images
        self.calibration = None if index is None else index.get("calibration")

    def relative(self, path):
        return path.relative_to(self.source).as_posix()

    def includes(self, path):
        return self.images is None or self.relative(path) in self.images

    def image_path(self, relative):
        path = (self.source / relative).resolve(strict=True)
        if not path.is_relative_to(self.source) or not path.is_file():
            raise ValueError(f"原图路径无效：{relative}")
        return path

    def verify_sources(self, frames):
        expected = {name: value for frame in frames for name, value in frame["sources"].items()}
        for name, value in expected.items():
            if stamp(self.image_path(name)) != value:
                raise ValueError(f"原图已修改，请重新运行全部图像统计：{name}")

    def frame_key(self, path, page):
        return sha256(f"{self.relative(path)}\0{page}".encode()).hexdigest()[:32]

    def record_frame(self, path, page, sources, **data):
        key = self.frame_key(path, page)
        self.frames[key] = {
            "image": self.relative(path), "page": page,
            "sources": {self.relative(source): stamp(source) for source in sources},
            **data,
        }
        return key

    def nuclei(self, path, page, dapi_path, sources, segment):
        key = self.frame_key(path, page)
        if self.images is None:
            original = segment()
            cached = f"{REVIEW_DIR}/{key}.npz"
            (self.directory / REVIEW_DIR).mkdir(exist_ok=True)
            np.savez_compressed(self.directory / cached, labels=original)
            self.record_frame(path, page, sources, display=self.relative(dapi_path), labels=cached)
        else:
            with np.load(self.directory / self.frames[key]["labels"], allow_pickle=False) as data:
                original = data["labels"]
        return nucleus_labels(original, self.edits[key]) if self.edits.get(key) else original

    def fibers(self, path, pairs, reds, greens, maximum):
        key = self.frame_key(path, 0)
        boxes = [[reds[r]["corners"].tolist(), greens[g]["corners"].tolist()] for r, g, *_ in pairs]
        if self.images is None:
            self.record_frame(path, 0, [path], display=self.relative(path), boxes=boxes, maximum=maximum)
        else:
            original = self.frames[key]["boxes"]
            if len(boxes) != len(original) or (boxes and not np.allclose(boxes, original)):
                raise ValueError("DNA Fiber 配对与上次统计不一致，请重新完整统计。")
        excluded = set(self.edits.get(key, {}).get("excluded", []))
        if any(index < 1 or index > len(pairs) for index in excluded):
            raise ValueError("DNA Fiber 配对标记已失效。")
        return [pair for index, pair in enumerate(pairs, 1) if index not in excluded]


def load_review(result):
    return json.loads((result / REVIEW_INDEX).read_text(encoding="utf-8"))
