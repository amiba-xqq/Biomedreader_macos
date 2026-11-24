"""Validate Olympus acquisitions assigned to named export groups."""

import re


def oir_files(root, recursive):
    candidates = root.rglob("*") if recursive else root.iterdir()
    return {path.relative_to(root).as_posix(): path
            for path in sorted(candidates, key=lambda item: (item.as_posix().casefold(), item.as_posix()))
            if path.suffix.lower() == ".oir" and path.is_file() and path.resolve().is_relative_to(root)}


def selected_groups(groups, files):
    if not isinstance(groups, list):
        raise ValueError("OIR 分组格式无效。")
    used_names, used_files = set(), set()
    result = []
    for group in groups:
        name = group["name"].strip()
        paths = group["files"]
        folded = name.casefold()
        if (not name or len(name) > 255 or name.endswith(".") or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
                or re.fullmatch(r"(?i:con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(\..*)?", name)):
            raise ValueError(f"分组名称不能作为 Windows 文件夹名：{name}")
        if folded in used_names:
            raise ValueError(f"分组名称重复：{name}")
        used_names.add(folded)
        if not isinstance(paths, list) or not paths:
            continue
        missing = [path for path in paths if path not in files]
        if missing:
            raise ValueError(f"OIR 文件已移动或不在当前扫描范围内：{missing[0]}。请重新设置分组。")
        if len(set(paths)) != len(paths) or used_files.intersection(paths):
            raise ValueError("同一个 OIR 文件只能归入一个分组。")
        used_files.update(paths)
        result.append((name, [files[path] for path in paths]))
    if not result:
        raise ValueError("请至少把一个 OIR 文件拖入分组。")
    return result
