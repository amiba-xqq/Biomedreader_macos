"""Complete local-data ZIP backups with consistent snapshots of live SQLite files."""
import os
import sys
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path
from contextlib import closing
from urllib.parse import quote


def default_data_directory():
    if sys.platform=='darwin':return Path.home()/'Library'/'Application Support'/'OrangeReader'/'BiomedLiterature'
    local=Path(os.environ.get('LOCALAPPDATA') or Path.home()/'AppData'/'Local')
    return local/'OrangeReader'/'BiomedLiterature'


def backup_data_directory(database,destination,progress=lambda message:None):
    root=database.path.parent.resolve();target=Path(destination).resolve()
    if target.suffix.lower()!='.zip':raise ValueError('请保存为ZIP压缩包。')
    if target.is_relative_to(root):raise ValueError('请把备份保存到本地数据目录之外，避免备份包含自身。')
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.biomed-backup-',dir=target.parent) as temporary:
        staging=Path(temporary);main_snapshot=staging/'main.sqlite3'
        progress('正在创建数据库一致性快照……')
        with database.connect() as source,closing(sqlite3.connect(main_snapshot)) as output:
            source.backup(output)
        files=[];folders=[]
        def failed(error):raise error
        for directory,subdirectories,names in os.walk(root,onerror=failed,followlinks=False):
            parent=Path(directory);folders.append(parent)
            for name in subdirectories+names:
                path=parent/name
                if path.is_symlink() or path.is_junction():raise ValueError(f'数据目录中有链接文件，请先移出后备份：{path.relative_to(root)}')
            files.extend(parent/name for name in sorted(names))
        sqlite_files={database.path.resolve()}
        for path in files:
            if path.name in ('LOCK','SingletonLock','SingletonSocket','SingletonCookie','lockfile'):continue
            with path.open('rb') as stream:
                if stream.read(16)==b'SQLite format 3\x00':sqlite_files.add(path.resolve())
        archive_path=staging/'backup.zip';skipped=[];count=0;last_notice=0
        with zipfile.ZipFile(archive_path,'w',zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as archive:
            for folder in folders:
                relative=folder.relative_to(root)
                archive.writestr('BiomedLiterature/'+('' if relative==Path('.') else relative.as_posix()+'/'),b'')
            for index,path in enumerate(files):
                relative=path.relative_to(root).as_posix()
                if path.name in ('LOCK','SingletonLock','SingletonSocket','SingletonCookie','lockfile') or any(
                    path==Path(str(sqlite_path)+suffix) for sqlite_path in sqlite_files for suffix in ('-wal','-shm','-journal')):
                    skipped.append(relative);continue
                if time.monotonic()-last_notice>.1 or index==len(files)-1:
                    progress(f'正在备份 {index+1}/{len(files)}：{relative}');last_notice=time.monotonic()
                content=path
                if path.resolve()==database.path.resolve():content=main_snapshot
                elif path.resolve() in sqlite_files:
                    content=staging/f'sqlite-{index}.sqlite3';deadline=time.monotonic()+60
                    def check(*_):
                        if time.monotonic()>deadline:raise TimeoutError('数据库正忙，请稍后重试备份。')
                    uri='file:'+quote(path.as_posix(),safe='/:')+'?mode=ro'
                    with closing(sqlite3.connect(uri,uri=True,timeout=10)) as source,closing(sqlite3.connect(content)) as output:
                        source.backup(output,pages=256,progress=check,sleep=.1)
                before=content.stat();archive.write(content,'BiomedLiterature/'+relative);after=content.stat()
                if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                    raise ValueError(f'备份时文件发生变化，请稍后重试：{relative}')
                count+=1
        with zipfile.ZipFile(archive_path) as archive:
            if archive.testzip():raise ValueError('备份压缩包校验失败。')
        os.replace(archive_path,target)
    return dict(path=str(target),files=count,skipped=len(skipped))
