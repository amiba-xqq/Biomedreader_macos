"""Local PDF copies belong beside the user's database; only basenames are stored."""
from pathlib import Path
import hashlib
import re
import shutil
import sys


def legacy_paper_directory() -> Path:
    root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return root / "paper"


def default_paper_directory(data_directory: Path) -> Path:
    return Path(data_directory) / "paper"


def copy_pdf(source: Path, title: str, article_key: str, directory: Path) -> Path:
    source = Path(source)
    if source.suffix.lower() != ".pdf" or not source.is_file():
        raise ValueError("请选择已经下载好的 PDF 文件。")
    with source.open("rb") as stream:
        if b"%PDF-" not in stream.read(1024):
            raise ValueError("所选文件不是有效的 PDF 文件。")
    directory.mkdir(parents=True, exist_ok=True)
    # Windows disallows these characters, trailing dots, and reserved device basenames.
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .")[:150].rstrip(" .") or "未命名文章"
    if sys.platform=='darwin':name=name.encode('utf8')[:210].decode('utf8',errors='ignore').rstrip(' .') or '未命名文章'
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
        name = "_" + name
    target = directory / (name + ".pdf")
    if target.exists() and source.resolve() == target.resolve():
        return target
    suffix = hashlib.sha256(article_key.encode("utf-8")).hexdigest()[:8]
    index = 0
    while target.exists():
        index += 1
        target = directory / f"{name} ({suffix}{'-' + str(index) if index > 1 else ''}).pdf"
        if source.resolve() == target.resolve():
            return target
    # Reserve an unused filename exclusively, so concurrent copies never overwrite a PDF.
    try:
        with target.open("xb") as destination, source.open("rb") as original:
            shutil.copyfileobj(original, destination)
    except Exception:
        # Only remove a file this invocation created; never touch the selected original.
        if target.exists() and sys.exc_info()[0] is not FileExistsError:
            target.unlink(missing_ok=True)
        raise
    return target
