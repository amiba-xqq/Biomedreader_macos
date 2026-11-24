"""Dispatch the bundled DDRimage workflows in a separate process."""

from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import csv
from datetime import datetime
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from shutil import copy2, copytree, rmtree
import sys
import traceback
import uuid

from ddrimage_review_data import REVIEW_DIR, REVIEW_INDEX, REVIEW_WORKFLOWS, ReviewRun, load_review
from ddrimage_output import (
    RESULTS_FOLDER, RESULT_MARKER, TEST_PREFIX, backup_owner, checked_path, generated_images, link_or_copy, metadata_root,
    plot_statistics, publish_files, published_path, stage_publication, tracked_files,
)

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
PAYLOAD = ROOT / "ddrimage" if getattr(sys, "frozen", False) else ROOT / "ddrimage_analysis"
WORKFLOWS = {
    "lif2tif.py": ("input_dir", "tif"),
    "nucleus_foci.py": ("input_dir", "foci"),
    "nuclear_intensity.py": ("input_dir", "intensity"),
    "DNA_fiber.py": ("parent_folder", "fiber"),
    "colony_formation.py": ("parent_dir", "colony"),
    "foci_plot.py": ("input_dir", ""),
    "intensity_plot.py": ("input_dir", ""),
    "fiber_plot.py": ("input_dir", ""),
}
PLOT_SETTINGS = f"{REVIEW_DIR}/plot.json"
PLOT_SCRIPTS = {"foci": "foci_plot.py", "intensity": "intensity_plot.py", "fiber": "fiber_plot.py"}
CONVERSION_EXTENSIONS = {"leica": {".lif", ".liff"}, "olympus": {".oir"}}


def available() -> bool:
    return (PAYLOAD / "lif2tif.py").is_file()


def prepare_output(source: Path, suffix: str) -> Path:
    stem = f"{source.name}_{suffix if suffix == 'tif' else 'DDRimage_' + suffix}"
    destination = source.with_name(stem)
    try:
        destination.mkdir()
    except FileExistsError:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        destination = source.with_name(f"{stem}_{timestamp}")
        destination.mkdir()
    return destination


def legacy_statistics_destination(source: Path, workflow: str) -> Path:
    return source.with_name(f"{source.name}_DDRimage_{workflow}")


def previous_statistics_destination(source: Path, workflow: str, *, test=False) -> Path:
    return source / RESULTS_FOLDER / (f"{workflow}-test" if test else workflow)


def plot_output_root(source, workflow):
    try:
        previous = json.loads((source / RESULT_MARKER).read_text(encoding="utf-8"))["identity"]
        raw = Path(previous["source"]).resolve(strict=True)
        if previous["workflow"] == workflow and source in (
                legacy_statistics_destination(raw, workflow),
                previous_statistics_destination(raw, workflow),
                previous_statistics_destination(raw, workflow, test=True)):
            return raw
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return source


@contextmanager
def statistics_lock(destination):
    from PySide6.QtCore import QLockFile

    guard = QLockFile(str(destination.with_name(f".{destination.name}.lock")))
    if not guard.tryLock(0):
        raise ValueError("另一项任务正在写入同一统计结果，请稍后再试。")
    try:
        yield
    finally:
        guard.unlock()


def result_files(folder: Path) -> set[str]:
    entries = set()
    for current, dirs, files in os.walk(folder, followlinks=False):
        root = Path(current)
        linked = [name for name in dirs if (root / name).is_symlink() or (root / name).is_junction()]
        dirs[:] = [name for name in dirs if name not in linked]
        for name in files + linked:
            path = root / name
            relative = path.relative_to(folder).as_posix()
            entries.add(f"link:{relative}" if path.is_symlink() or path.is_junction() else relative)
    return entries - {RESULT_MARKER}


def publish_statistics(previous: Path, staged: Path, identity: dict, *, keep_existing=False) -> None:
    tracked = result_files(staged)
    if previous.is_symlink() or previous.is_junction():
        raise ValueError(f"统计结果目录是链接，请移到其他位置后重试：{previous}")
    if not previous.exists():
        write_manifest(staged, identity, tracked)
        staged.rename(previous)
        return
    if not previous.is_dir():
        raise ValueError(f"统计结果路径被文件占用：{previous}")
    try:
        old = json.loads((previous / RESULT_MARKER).read_text(encoding="utf-8"))
        extras = result_files(previous) - set(old["files"])
        tagged = old["identity"] == identity and not extras
        if keep_existing:
            tracked -= extras
    except (OSError, KeyError, TypeError, ValueError):
        tagged = False
    write_manifest(staged, identity, tracked)
    backup = previous.with_name(f"{previous.name}_backup_{uuid.uuid4().hex[:8]}")
    previous.rename(backup)
    try:
        staged.rename(previous)
    except OSError:
        if not previous.exists():
            backup.rename(previous)
        raise
    if tagged:
        try:
            rmtree(backup)
        except OSError as error:
            print(f"新结果已写入。部分旧结果仍在 {backup}：{error}", flush=True)
    else:
        print(f"旧目录含旧版结果或额外文件，已保存在 {backup}", flush=True)


def write_manifest(staged, identity, tracked):
    manifest = {"identity": identity, "files": sorted(tracked)}
    temp = staged / f"{RESULT_MARKER}.{uuid.uuid4().hex}.tmp"
    temp.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    temp.replace(staged / RESULT_MARKER)


def run_script(script: str, parameters: dict, destination: Path, review=None, preview=None, *, inplace=False) -> None:
    path = PAYLOAD / script
    before, sep, after = path.read_text(encoding="utf-8").partition("# %% [cell 2]")
    if not sep:
        raise ValueError(f"脚本缺少参数单元：{script}")
    input_key, workflow = WORKFLOWS[script]
    outputs = generated_images(Path(parameters[input_key]).resolve()) if workflow in REVIEW_WORKFLOWS else set()
    namespace = {"__name__": "__main__", "__file__": str(path), "_review": review,
                 "_test_preview": preview, "_in_place": inplace, "_generated_images": outputs,
                 "_ui_parameters": parameters, "ddrimage_output": destination}
    exec(compile(before + "\nglobals().update(_ui_parameters)\n" + sep + after,
                 str(path), "exec"), namespace)


def read_parameters(overrides):
    parameters = dict(overrides)
    for name, value in parameters.items():
        if not name.isidentifier() or name.startswith("_"):
            raise ValueError(f"无效参数：{name}")
        if isinstance(value, dict) and "__path__" in value:
            parameters[name] = Path(value["__path__"])
        elif isinstance(value, dict) and "__tuple__" in value:
            parameters[name] = tuple(value["__tuple__"])
    return parameters


def save_index(directory, index):
    path = directory / REVIEW_INDEX
    path.parent.mkdir(exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def merge_csv(old, patch, affected):
    with old.open(encoding="utf-8-sig", newline="") as stream:
        header, *original = csv.reader(stream)
    with patch.open(encoding="utf-8-sig", newline="") as stream:
        updated_header, *updated = csv.reader(stream)
    if header != updated_header:
        raise ValueError(f"统计表列与原结果不匹配：{old.name}")
    by_image = {}
    for row in updated:
        by_image.setdefault(row[0], []).append(row)
    combined = []
    for row in original:
        key = row[0]
        if key in affected:
            combined.extend(by_image.pop(key, []))
        else:
            combined.append(row)
    for rows in by_image.values():
        combined.extend(rows)
    with patch.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(combined)


def refresh_previous_plot(staged, workflow):
    path = staged / PLOT_SETTINGS
    if not path.is_file():
        return
    settings = json.loads(path.read_text(encoding="utf-8"))
    parameters = read_parameters(settings["overrides"])
    parameters.update(input_dir=staged, show_plots=False)
    # These files may be linked to the published copy. Detach them before the
    # existing plot scripts open the upstream summary tables and PDFs for writing.
    for file in staged.iterdir():
        if file.is_file() and file.suffix.lower() in {".csv", ".pdf"}:
            detached = file.with_name(f".{file.name}.{uuid.uuid4().hex}.tmp")
            copy2(file, detached)
            detached.replace(file)
    run_script(PLOT_SCRIPTS[workflow], parameters, staged)


def apply_review(request, owner, preview_file):
    result = Path(request["result"]).resolve(strict=True)
    index = load_review(result)
    if request["revision"] != index["revision"]:
        raise ValueError("统计结果已更新，请关闭矫正窗口后重新打开。")
    script = index["script"]
    input_key, workflow = WORKFLOWS[script]
    source = Path(index["source"]).resolve(strict=True)
    if index.get("in_place"):
        return apply_live_review(request, result, index, source, workflow, owner, preview_file)
    if workflow not in REVIEW_WORKFLOWS or result not in (
            previous_statistics_destination(source, workflow), legacy_statistics_destination(source, workflow)):
        raise ValueError("统计目录与原始工作流不一致。")
    edits = request["edits"]
    if not isinstance(edits, dict) or not edits.keys() <= index["frames"].keys():
        raise ValueError("矫正记录与原图不一致。")
    old_edits = index["edits"]
    keys = edits.keys() | old_edits.keys()
    images = {index["frames"][key]["image"] for key in keys if edits.get(key) != old_edits.get(key)}
    if not images:
        return result
    review = ReviewRun(source, result, index, edits, images)
    review.verify_sources(frame for frame in review.frames.values() if frame["image"] in images)
    if any(name.startswith("link:") for name in result_files(result)):
        raise ValueError("结果目录含文件夹链接或符号链接，请移除后重试。")
    parameters = read_parameters(index["overrides"])
    parameters[input_key] = source
    affected = {}
    for image in images:
        path = Path(image)
        affected.setdefault(path.parent, set()).add(path.stem)
    with TemporaryDirectory(prefix=".ddrimage-review-", dir=result.parent) as temp:
        patch = Path(temp) / "patch"
        patch.mkdir()
        run_script(script, parameters, patch, review)
        staged = Path(temp) / "results"
        with statistics_lock(result):
            if request["revision"] != load_review(result)["revision"]:
                raise ValueError("统计结果已被其他窗口更新，请重新打开。")
            copytree(result, staged, copy_function=link_or_copy)
            for file in patch.rglob("*"):
                if file.is_file():
                    relative = file.relative_to(patch)
                    target = staged / relative
                    if file.suffix == ".csv":
                        merge_csv(target, file, affected[relative.parent])
                    file.replace(target)
            index.update(edits=edits, revision=uuid.uuid4().hex)
            save_index(staged, index)
            refresh_previous_plot(staged, workflow)
            publish_statistics(result, staged, {"source": str(source), "workflow": workflow}, keep_existing=True)
    print(f"矫正完成：重算 {len(images)} 张图片，已更新各子文件夹的统计 CSV。", flush=True)
    return result


def apply_live_review(request, storage, index, source, workflow, owner, preview_file):
    test = bool(index["test"])
    if storage != metadata_root(source, workflow, test=test):
        raise ValueError("矫正记录与母文件夹不一致。")
    edits = request["edits"]
    original = index["edits"]
    if not isinstance(edits, dict) or not edits.keys() <= index["frames"].keys():
        raise ValueError("矫正记录与原图不一致。")
    images = {index["frames"][key]["image"] for key in edits.keys() | original.keys()
              if edits.get(key) != original.get(key)}
    if not images:
        return source
    affected = {}
    for image in map(Path, images):
        affected.setdefault(image.parent, set()).add(image.stem)
    script = index["script"]
    with statistics_lock(source):
        if request["revision"] != load_review(storage)["revision"]:
            raise ValueError("统计结果已更新，请重新打开矫正窗口。")
        review = ReviewRun(source, storage, index, edits, images)
        review.verify_sources(frame for frame in review.frames.values() if frame["image"] in images)
        parameters = read_parameters(index["overrides"])
        parameters[WORKFLOWS[script][0]] = source
        with TemporaryDirectory(prefix=".ddrimage-work-", dir=source) as temp:
            scratch = Path(temp)
            patch, staged = scratch / "patch", scratch / "statistics"
            patch.mkdir()
            staged.mkdir()
            preview = {} if test and workflow == "colony" else None
            run_script(script, parameters, patch, review, preview, inplace=True)
            relative_storage = storage.relative_to(source)
            for name in tracked_files(storage):
                actual = checked_path(source, name)
                relative = Path(name)
                if relative.is_relative_to(relative_storage):
                    relative = relative.relative_to(relative_storage)
                elif test:
                    relative = relative.with_name(relative.name.removeprefix(TEST_PREFIX))
                if workflow == "colony" and any(
                        re.fullmatch(re.escape(stem) + r"(?:_ROI\d+|_roi_check)?_colony\.tif", relative.name)
                        for stem in affected.get(relative.parent, ())):
                    continue
                target = staged / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                link_or_copy(actual, target)
            for file in patch.rglob("*"):
                if not file.is_file():
                    continue
                relative = file.relative_to(patch)
                target = staged / relative
                if file.suffix == ".csv":
                    merge_csv(target, file, affected[relative.parent])
                target.parent.mkdir(parents=True, exist_ok=True)
                file.replace(target)
            index.update(edits=edits, revision=uuid.uuid4().hex, calibration=review.calibration)
            save_index(staged, index)
            operations = stage_publication(source, staged, storage, test=test)
            plot_storage = metadata_root(source, workflow, plot=True)
            plot_record = plot_storage / PLOT_SETTINGS
            if workflow in PLOT_SCRIPTS and plot_record.is_file():
                settings = json.loads(plot_record.read_text(encoding="utf-8"))
                if bool(settings["test"]) == test:
                    for name in tracked_files(plot_storage):
                        path = Path(name)
                        if len(path.parts) == 1 and path.suffix == ".csv":
                            link_or_copy(checked_path(source, path), staged / path)
                    chart_stage = scratch / "plot"
                    chart_stage.mkdir()
                    render_live_plot(PLOT_SCRIPTS[workflow], staged, chart_stage, settings)
                    operations.update(stage_publication(source, chart_stage, plot_storage))
            publish_files(source, operations, scratch, owner)
            write_test_preview(preview_file, preview)
    print(f"矫正完成：重算 {len(images)} 张图片，已更新统计 CSV。", flush=True)
    return source


def render_live_plot(script, source, stage, settings):
    parameters = read_parameters(settings["overrides"])
    parameters.update(input_dir=source, show_plots=False)
    run_script(script, parameters, stage, inplace=True)
    path = stage / PLOT_SETTINGS
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")


def write_test_preview(path, preview):
    if preview:
        preview["images"] = [[title, published_path(Path(file), True).as_posix()]
                             for title, file in preview["images"]]
        path.write_text(json.dumps(preview, ensure_ascii=False), encoding="utf-8")


def run_plot_job(script, source, destination, job, owner, preview_file):
    workflow = next(name for name, plot_script in PLOT_SCRIPTS.items() if plot_script == script)
    managed, test_plot = plot_statistics(source, workflow)
    with statistics_lock(destination), TemporaryDirectory(prefix=".ddrimage-work-", dir=destination) as temp:
        scratch = Path(temp)
        stage = scratch / "charts"
        stage.mkdir()
        settings = {"overrides": job["overrides"], "test": test_plot}
        if managed is not None:
            render_live_plot(script, source, stage, settings)
        else:
            params = read_parameters(job["overrides"])
            params.update(input_dir=source, show_plots=False)
            run_script(script, params, stage)
        previews = [[path.stem, path.name] for path in sorted(stage.glob("*.pdf"))]
        operations = stage_publication(destination, stage, metadata_root(destination, workflow, plot=True))
        publish_files(destination, operations, scratch, owner)
        if managed is None and destination == source and (source / REVIEW_INDEX).is_file():
            index = load_review(source)
            if PLOT_SCRIPTS.get(WORKFLOWS[index["script"]][1]) == script:
                path = source / PLOT_SETTINGS
                draft = path.with_suffix(".tmp")
                draft.write_text(json.dumps({"overrides": job["overrides"]}), encoding="utf-8")
                draft.replace(path)
        if previews:
            preview_file.write_text(json.dumps({"plot": True, "pages": 1, "images": previews},
                                             ensure_ascii=False), encoding="utf-8")


def run(config_path: Path) -> int:
    with config_path.with_suffix(".log").open("w", encoding="utf-8", buffering=1) as log:
        with redirect_stdout(log), redirect_stderr(log):
            try:
                execute(config_path)
                return 0
            except Exception:
                traceback.print_exc()
                return 1


def execute(config_path: Path) -> None:
    if not available():
        raise RuntimeError("DDRimage 未安装。")
    if getattr(sys, "frozen", False):
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(PAYLOAD / "packages"))
    os.environ["MPLBACKEND"] = "Agg"
    os.environ["MPLCONFIGDIR"] = str(config_path.parent / "matplotlib")

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    owner = backup_owner(payload.get("backup_id", uuid.uuid4().hex))
    preview_file = config_path.with_suffix(".preview.json")
    if "correction" in payload:
        result = apply_review(payload["correction"], owner, preview_file)
        config_path.with_suffix(".result.json").write_text(json.dumps(str(result)), encoding="utf-8")
        return
    for job in payload["jobs"]:
        script = job["script"]
        input_key, suffix = WORKFLOWS[script]
        parameters = read_parameters(job["overrides"])
        source = Path(parameters[input_key]).resolve(strict=True)
        if not source.is_dir():
            raise ValueError("请选择一个输入文件夹。")
        mode = REVIEW_WORKFLOWS.get(suffix, "mode")
        test = parameters.get(mode, "all" if suffix == "intensity" else "test") == "test"
        if suffix == "tif":
            kind = parameters.get("source_format", "leica")
            extensions = CONVERSION_EXTENSIONS[kind]
            candidates = source.rglob("*") if parameters.get("recursive") else source.iterdir()
            if not any(path.is_file() and path.suffix.lower() in extensions for path in candidates):
                raise ValueError(f"输入目录内没有所选格式的文件（{' / '.join(sorted(extensions))}）。")
            destination = prepare_output(source, suffix)
            parameters["output_dir"] = destination
        else:
            destination = source
        print(f"步骤：{job['label']}\n结果目录：{destination}", flush=True)
        if suffix not in {"", "tif"}:
            storage = metadata_root(source, suffix, test=test)
            with TemporaryDirectory(prefix=".ddrimage-work-", dir=source) as staging:
                staged = Path(staging) / "results"
                staged.mkdir()
                review = ReviewRun(source, staged) if suffix in REVIEW_WORKFLOWS and (not test or suffix == "colony") else None
                preview = {} if test and suffix in REVIEW_WORKFLOWS else None
                run_script(script, parameters, staged, review, preview, inplace=True)
                if review and review.frames:
                    save_index(staged, {"source": str(source), "script": script, "overrides": job["overrides"],
                                        "frames": review.frames, "edits": {}, "revision": uuid.uuid4().hex,
                                        "in_place": True, "test": test, "calibration": review.calibration})
                with statistics_lock(source):
                    operations = stage_publication(source, staged, storage, test=test)
                    publish_files(source, operations, Path(staging), owner)
                write_test_preview(preview_file, preview)
        elif script in PLOT_SCRIPTS.values():
            workflow = next(name for name, plot_script in PLOT_SCRIPTS.items() if plot_script == script)
            destination = plot_output_root(source, workflow)
            run_plot_job(script, source, destination, job, owner, preview_file)
        else:
            run_script(script, parameters, destination)
        ready = config_path.with_suffix(".result.json")
        ready.write_text(json.dumps(str(destination), ensure_ascii=False), encoding="utf-8")
    print("全部步骤已完成。", flush=True)
