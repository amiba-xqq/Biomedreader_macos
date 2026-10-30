"""An explicit local-file drop target; dropping never starts an AI request."""
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget, QListWidgetItem
from ai_finder import SUPPORTED


class ManuscriptDropList(QListWidget):
    def __init__(self, reviewer):
        super().__init__(reviewer)
        self.reviewer=reviewer
        self.setAcceptDrops(True)
        self.setMinimumHeight(75)
        self.setMaximumHeight(150)
        self.setStyleSheet('QListWidget {background:#FFFCF7;border:1px dashed #D9B68E;border-radius:10px;padding:8px;}')
        self.setToolTip('将 PDF、DOCX 或图片拖到此处，或点击上方按钮选择文件；仅添加路径，点击审阅后才读取和发送。')
        self.refresh()

    def refresh(self):
        self.clear()
        if not self.reviewer.files:
            item=QListWidgetItem('将待审稿件拖到这里 · PDF / DOCX / 图片等科研资料')
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.addItem(item)
        for value in self.reviewer.files:
            item=QListWidgetItem(Path(value).name)
            item.setToolTip(value)
            self.addItem(item)

    def add_paths(self, paths):
        if self.reviewer.task:return
        rejected=[]
        known={str(Path(p).resolve()).casefold() for p in self.reviewer.files}
        for value in paths:
            path=Path(value)
            if not path.is_file() or path.suffix.lower() not in SUPPORTED:
                rejected.append(path.name);continue
            resolved=str(path.resolve())
            if resolved.casefold() not in known:
                self.reviewer.files.append(resolved);known.add(resolved.casefold())
        self.refresh()
        self.reviewer.status.setText('已选 '+str(len(self.reviewer.files))+' 个文件：'+'；'.join(Path(p).name for p in self.reviewer.files)+
                                    ('；未添加不支持或不存在的文件：'+'；'.join(rejected) if rejected else ''))

    def dragEnterEvent(self,event):
        if not self.reviewer.task and event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):event.acceptProposedAction()
        else:event.ignore()

    def dragMoveEvent(self,event):self.dragEnterEvent(event)

    def dropEvent(self,event):
        if self.reviewer.task:event.ignore();return
        self.add_paths([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]);event.acceptProposedAction()
