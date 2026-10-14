"""A fresh process moves stores after the reader and Chromium release all handles."""
import ctypes
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from PySide6.QtCore import QProcess,QThreadPool,Qt
from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QProgressBar,QMessageBox


def start_reader(arguments=()):
    prefix=[] if getattr(sys,'frozen',False) else [str(Path(__file__).with_name('main.py'))]
    started=QProcess.startDetached(sys.executable,prefix+list(arguments))
    if not (started[0] if isinstance(started,tuple) else started):raise OSError('无法自动启动软件，请手动重新打开。')


def launch_data_switch(report):
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf8',prefix='BiomedReader-data-switch-',suffix='.json',delete=False) as file:
        path=Path(file.name)
        json.dump(dict(version=1,parent_pid=os.getpid(),created_at=time.time(),report=report),file,ensure_ascii=False)
    try:start_reader(('--complete-data-switch',str(path),str(os.getpid())))
    except Exception:
        path.unlink(missing_ok=True);raise


def wait_for_parent(pid):
    if os.name=='nt':
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=(wintypes.DWORD,wintypes.BOOL,wintypes.DWORD);kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes=(wintypes.HANDLE,wintypes.DWORD);kernel.WaitForSingleObject.restype=wintypes.DWORD
        kernel.CloseHandle.argtypes=(wintypes.HANDLE,)
        handle=kernel.OpenProcess(0x100000,False,pid)
        if not handle:
            if ctypes.get_last_error() in (87,1168):return
            raise OSError('无法确认旧程序是否退出，原目录未删除。')
        try:
            if kernel.WaitForSingleObject(handle,60000)!=0:raise TimeoutError('等待旧程序退出超时，原目录未删除。')
        finally:kernel.CloseHandle(handle)
    else:
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            try:os.kill(pid,0)
            except ProcessLookupError:return
            time.sleep(.1)
        raise TimeoutError('等待旧程序退出超时。')


def execute_switch(report,progress):
    from data_location import validate_data_directory,activate_location,relocate_data,finish_relocation
    if report['kind']=='link':
        activate_location(validate_data_directory(report['path']))
    elif report['kind']=='move':
        from database import Database
        source=validate_data_directory(report['source'])
        database=Database(source/'biomed_literature.sqlite3')
        prepared=relocate_data(database,report['path'],progress)
        progress('文件校验通过，正在切换路径并删除原目录……')
        finish_relocation(prepared)
    else:raise ValueError('未知的数据目录操作。')


def run_data_switch(application,plan_path,parent_pid):
    path=Path(plan_path).resolve()
    try:
        if path.parent!=Path(tempfile.gettempdir()).resolve() or not path.name.startswith('BiomedReader-data-switch-'):
            raise ValueError('无效的目录操作记录。')
        payload=json.loads(path.read_text(encoding='utf8'))
        if payload.get('version')!=1 or payload.get('parent_pid')!=parent_pid or not 0<=time.time()-payload['created_at']<300:
            raise ValueError('目录操作记录已过期或不一致。')
        wait_for_parent(parent_pid)
    except Exception as error:
        QMessageBox.warning(None,'数据目录操作未完成',str(error));return 1
    finally:
        if path.parent==Path(tempfile.gettempdir()).resolve() and path.name.startswith('BiomedReader-data-switch-'):
            path.unlink(missing_ok=True)
    from ui import BackgroundOperation
    class ProgressDialog(QDialog):
        allow_close=False
        def closeEvent(self,event):
            if self.allow_close:super().closeEvent(event)
            else:event.ignore()
    dialog=ProgressDialog();dialog.setWindowTitle('刊禾 · 本地数据目录')
    dialog.setWindowFlags(Qt.WindowType.Dialog|Qt.WindowType.CustomizeWindowHint|Qt.WindowType.WindowTitleHint)
    dialog.resize(620,160);layout=QVBoxLayout(dialog)
    label=QLabel('正在处理本地数据，请等待完成后自动重新打开软件……');label.setWordWrap(True)
    bar=QProgressBar();bar.setRange(0,0);layout.addWidget(label);layout.addWidget(bar)
    operation=BackgroundOperation(lambda progress:execute_switch(payload['report'],progress))
    operation.signals.progress.connect(label.setText)
    def completed(result):
        dialog.allow_close=True;dialog.close()
        application.quit()
    def failed(message):
        QMessageBox.warning(dialog,'数据目录操作未完成',message)
        dialog.allow_close=True;dialog.close()
        application.quit()
    operation.signals.finished.connect(completed);operation.signals.failed.connect(failed)
    application.setQuitOnLastWindowClosed(False);dialog.show()
    QThreadPool.globalInstance().start(operation);application.exec()
    try:start_reader()
    except Exception as error:QMessageBox.information(None,'请重新打开软件',str(error))
    return 0
