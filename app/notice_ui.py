"""A landscape, non-blocking incremental-search completion notice."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QPlainTextEdit
from interface_scale import scaled

class NoNewArticlesDialog(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent);self.setWindowTitle('无新文章');self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose,True);self.resize(scaled(560),scaled(220));self.setMinimumSize(scaled(500),scaled(190));self.setMaximumHeight(scaled(320))
        layout=QVBoxLayout(self);layout.setContentsMargins(24,20,24,18);row=QHBoxLayout();icon=QLabel('✓');icon.setStyleSheet('font-size:32pt;color:#518A73;padding:10px;');row.addWidget(icon)
        text=QVBoxLayout();title=QLabel('本次增量刷新没有发现新文章');title.setWordWrap(True);title.setStyleSheet('font-size:14pt;font-weight:700;color:#254451;');text.addWidget(title)
        note=QLabel('缓存文章和上次已保存的AI报告已保留。');note.setWordWrap(True);note.setStyleSheet('color:#75848D;');text.addWidget(note);row.addLayout(text,1);layout.addLayout(row)
        self.details=QPlainTextEdit();self.details.setReadOnly(True);self.details.setMaximumHeight(scaled(56));self.details.setStyleSheet('border:none;background:#F4F7F8;color:#5C707B;');layout.addWidget(self.details)
        buttons=QHBoxLayout();buttons.addStretch();ok=QPushButton('知道了');ok.clicked.connect(self.accept);buttons.addWidget(ok);layout.addLayout(buttons)
    def informativeText(self):return self.details.toPlainText()
    def setInformativeText(self,text):self.details.setPlainText(text.strip())
