"""Publish generated files beside their inputs and isolate the window's backups."""

import json
import os
from pathlib import Path
import re
from shutil import copy2, rmtree
from uuid import uuid4

RESULTS_FOLDER = ".ddrimage-results"
METADATA_FOLDER = ".ddrimage-review"
RESULT_MARKER = ".ddrimage-statistics.json"
TEST_PREFIX = "DDRimage_测试_"


def metadata_root(source, workflow, *, test=False, plot=False):
    name = workflow + ("-plot" if plot else "-test" if test else "")
    return source / METADATA_FOLDER / name


def tracked_files(storage):
    try:
        data = json.loads((storage / RESULT_MARKER).read_text(encoding="utf-8"))
        return set(data["files"])
    except FileNotFoundError:
        return set()


def plot_statistics(source, workflow):
    for test in (False, True):
        storage = metadata_root(source, workflow, test=test)
        if (storage / RESULT_MARKER).is_file():
            return tracked_files(storage), test
    return None, False


def generated_images(source):
    root = source / METADATA_FOLDER
    return {source / name for record in root.glob(f"*/{RESULT_MARKER}") for name in tracked_files(record.parent)
            if Path(name).suffix.lower() in {".tif", ".tiff"}}


def published_path(relative, test):
    return relative.with_name(TEST_PREFIX + relative.name) if test else relative


def checked_path(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"结果路径超出母文件夹：{relative}")
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"结果路径含链接：{path}")
    return path


def link_or_copy(source, target):
    try:
        os.link(source, target)
    except OSError:
        copy2(source, target)


def backup_owner(owner):
    if not isinstance(owner, str) or not re.fullmatch(r"[0-9a-f]{32}", owner):
        raise ValueError("备份会话编号无效。")
    return owner


def clear_window_backups(source, owner):
    folder = checked_path(source, Path(RESULTS_FOLDER) / backup_owner(owner))
    if folder.is_dir():
        rmtree(folder)
    try:
        folder.parent.rmdir()
    except OSError:
        pass


def clear_test_outputs(source, workflow):
    storage = metadata_root(source, workflow, test=True)
    relative_storage = storage.relative_to(source)
    manifest = checked_path(source, relative_storage / RESULT_MARKER)
    if not manifest.is_file():
        return
    files = []
    for name in tracked_files(storage):
        relative = Path(name)
        if relative.is_relative_to(relative_storage) or relative.name.startswith(TEST_PREFIX):
            files.append(checked_path(source, relative))
    for path in files:
        path.unlink(missing_ok=True)
    manifest.unlink()
    for directory in sorted((p for p in storage.rglob("*") if p.is_dir() and not p.is_symlink()
                             and not p.is_junction()), key=lambda p: len(p.parts), reverse=True):
        if not any(directory.iterdir()):
            directory.rmdir()
    for directory in (storage, storage.parent):
        if not any(directory.iterdir()):
            directory.rmdir()


def publish_files(source, operations, scratch, owner):
    """Replace only staged or previously managed files, with rollback on failure."""
    targets = {checked_path(source, relative): file for relative, file in operations.items()}
    if any(target.exists() and not target.is_file() for target in targets):
        raise ValueError("结果文件名被同名目录占用。")
    targets = {target: file for target, file in targets.items()
               if file is None or not target.exists() or not target.samefile(file)}
    original = {target for target in targets if target.exists()}
    backup = None
    if original:
        backup = checked_path(source, Path(RESULTS_FOLDER) / backup_owner(owner) / uuid4().hex)
        backup.mkdir(parents=True)
        for target in sorted(original):
            previous = backup / target.relative_to(source)
            previous.parent.mkdir(parents=True, exist_ok=True)
            link_or_copy(target, previous)
    committed = []
    try:
        for target, file in targets.items():
            if file is not None:
                target.parent.mkdir(parents=True, exist_ok=True)
                file.replace(target)
            elif target.exists():
                target.unlink()
            committed.append(target)
    except OSError:
        for target in reversed(committed):
            if target in original:
                restored = scratch / uuid4().hex
                link_or_copy(backup / target.relative_to(source), restored)
                restored.replace(target)
            elif target.exists():
                target.unlink()
        raise


def stage_publication(source, stage, storage, *, test=False):
    """Map a workflow's staged files, keeping review data outside the backup folder."""
    files = {}
    for file in stage.rglob("*"):
        if file.is_file() and file.name != RESULT_MARKER:
            relative = file.relative_to(stage)
            if relative.parts[0] == METADATA_FOLDER:
                target = (storage / relative).relative_to(source)
            else:
                target = published_path(relative, test)
            files[target.as_posix()] = file
    previous = tracked_files(storage)
    stored_manifest = stage / RESULT_MARKER
    stored_manifest.write_text(json.dumps({"files": sorted(files)}, ensure_ascii=False), encoding="utf-8")
    return {**dict.fromkeys(sorted(previous - files.keys())), **files,
            (storage / RESULT_MARKER).relative_to(source).as_posix(): stored_manifest}
