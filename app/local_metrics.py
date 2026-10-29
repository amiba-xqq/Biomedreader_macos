"""Non-blocking directory-size accounting, with no traversal through reparse points."""
import os
from pathlib import Path
from PySide6.QtCore import QObject,QRunnable,Signal,QThreadPool

def directory_size(root):
    total=0;skipped=0;pending=[Path(root)]
    while pending:
        directory=pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink() or getattr(entry,'is_junction',lambda:False)():continue
                        if entry.is_dir(follow_symlinks=False):pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):total+=entry.stat(follow_symlinks=False).st_size
                    except OSError:skipped+=1
        except OSError:skipped+=1
    return total,skipped

def size_label(size):return f'{size/1000000000:.1f} GB' if size>=1000000000 else f'{size/1000000:.1f} MB'

class Signals(QObject):finished=Signal(object)
class SizeTask(QRunnable):
    def __init__(self,path):super().__init__();self.path=path;self.signals=Signals()
    def run(self):self.signals.finished.emit(directory_size(self.path))

def start_size_scan(page):
    if getattr(page,'size_task',None):return
    page.data_size.setText('正在计算…');page.size_task=SizeTask(page.database.path.parent)
    def done(result):
        import shiboken6
        total,skipped=result
        if shiboken6.isValid(page.data_size):page.data_size.setText(size_label(total)+('（部分文件不可读）' if skipped else ''))
        page.size_task=None
    page.size_task.signals.finished.connect(done);QThreadPool.globalInstance().start(page.size_task)
