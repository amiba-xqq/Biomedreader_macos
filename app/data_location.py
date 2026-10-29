"""Validate existing stores and finalize relocation after runtime handles close."""
import os
import shutil
import time
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from contextlib import closing
from urllib.parse import quote
from PySide6.QtCore import QSettings
from data_backup import default_data_directory,backup_data_directory


def dedicated_directory(path):
    root=Path(path).resolve()
    home=Path.home().resolve()
    protected={home,home/'Desktop',home/'Documents',home/'Downloads',home/'AppData',home/'Library',home/'Library'/'Application Support',Path('/Applications'),Path(root.anchor)}
    if root in protected:
        raise ValueError('请选择专门保存本软件数据的文件夹，不能选择磁盘根目录或桌面等公共目录。')
    return root


def validate_data_directory(path,database_name='biomed_literature.sqlite3'):
    """Inspect without Database.initialize(), so an invalid selection is never populated."""
    root=dedicated_directory(path)
    database=root/database_name
    if not root.is_dir() or not database.is_file():
        raise ValueError('文件结构不正确：所选文件夹内需要直接包含 biomed_literature.sqlite3 数据库。请不要选择其上级目录或paper文件夹。')
    if (root/'paper').exists() and not (root/'paper').is_dir():
        raise ValueError('文件结构不正确：paper 必须是文件夹。')
    required={'settings':{'key','value'},'search_profiles':{'id','name','query_expression'},
        'articles':{'article_key','title','doi'},'journals':{'issn','title'},
        'favorites':{'article_key'},'journal_scopes':{'id','name','issns_json'}}
    try:
        uri='file:'+quote(database.as_posix(),safe='/:')+'?mode=ro'
        with closing(sqlite3.connect(uri,uri=True,timeout=10)) as connection:
            if connection.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('数据库完整性检查未通过。')
            for table,columns in required.items():
                present={row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
                if not columns.issubset(present):raise ValueError('缺少本软件需要的数据表或字段：'+table)
    except (sqlite3.Error,OSError) as error:
        raise ValueError('无法读取有效的 Biomed Reader 数据库：'+str(error)) from error
    return root


def active_data_directory():
    configured=QSettings('OrangeReader','BiomedLiterature').value('data_directory','')
    if not configured:return default_data_directory()
    root=Path(configured)
    if not root.exists():
        # After explicit full-library deletion, retain the user's chosen location
        # and allow a clean library on the next launch.
        cleared=QSettings('OrangeReader','BiomedLiterature').value('cleared_data_directory','')
        if cleared and Path(cleared)==root:return root
    if not (root/'biomed_literature.sqlite3').is_file():
        raise ValueError(f'已配置的数据目录不可用，请检查磁盘或文件夹：{root}')
    return root


def relocate_data(database,destination,progress=lambda message:None):
    source=dedicated_directory(database.path.parent);target=dedicated_directory(destination)
    validate_move_paths(source,target)
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.biomed-relocate-',dir=target.parent) as temporary:
        staging=Path(temporary);archive_path=staging/'data.zip'
        backup_data_directory(database,archive_path,progress)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(staging/'unpacked')
            for item in archive.infolist():
                path=staging/'unpacked'/item.filename
                if not item.is_dir() and path.stat().st_size!=item.file_size:
                    raise ValueError('迁移文件大小校验失败，未切换目录。')
        root=staging/'unpacked'/'BiomedLiterature'
        with closing(sqlite3.connect(root/database.path.name)) as connection:
            if connection.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('数据库完整性校验失败，未切换目录。')
        if target.exists():target.rmdir()
        os.replace(root,target)
    return dict(path=str(target),source=str(source),database_name=database.path.name)


def validate_move_paths(source,target):
    source=dedicated_directory(source);target=dedicated_directory(target)
    if target==source or target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError('新目录与原目录不能相同，也不能互相包含。')
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError('请选择空文件夹，避免覆盖已有文件。')
    return dict(path=str(target),source=str(source),kind='move')


def activate_location(path):
    settings=QSettings('OrangeReader','BiomedLiterature')
    old=settings.value('data_directory','')
    settings.setValue('data_directory',str(path));settings.sync()
    if settings.status()!=QSettings.Status.NoError:
        settings.setValue('data_directory',old);settings.sync()
        raise OSError('无法保存新的数据目录设置；原数据目录仍保留。')


def finish_relocation(report,activate=activate_location):
    """Called after the window/browser are destroyed; never delete before validation/switch."""
    source=dedicated_directory(report['source']);target=dedicated_directory(report['path'])
    if source==target or source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError('迁移路径无效，未删除原目录。')
    validate_data_directory(target,report.get('database_name','biomed_literature.sqlite3'))
    activate(target)
    # Windows may briefly retain Chromium cookie/cache handles after the view closes.
    # The new complete store is active before any deletion; a cleanup error cannot
    # leave the application pointed at a partly deleted old database.
    error=None
    for _ in range(20):
        try:
            if source.exists():shutil.rmtree(source)
            return target
        except OSError as caught:
            error=caught;time.sleep(.5)
    raise OSError(f'已切换到新目录 {target}，但原目录仍有文件被占用，未能完全删除：{source}。请关闭其它软件实例后清理。详细原因：{error}')
