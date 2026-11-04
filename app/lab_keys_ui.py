"""PI-only local credential copy. Never put a real key in visible widget text."""
from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QTableWidget,QTableWidgetItem,QPushButton,QApplication,QMessageBox,QHeaderView,QAbstractItemView
from lab_service import LAB_PROVIDERS

class LabKeysDialog(QDialog):
    def __init__(self,manager,parent=None):
        super().__init__(parent);self.manager=manager;self.setWindowTitle('PI · 本地API keys');self.resize(720,430)
        state=manager.store.lab_state();self.owner=(state or {}).get('identity',{}).get('local_owner')
        layout=QVBoxLayout(self);note=QLabel('仅读取当前PI的本地加密缓存，不联网。密钥不在窗口中显示；复制后可粘贴到您自己的文档查看，请私密保管。');note.setWordWrap(True);layout.addWidget(note)
        self.table=QTableWidget(0,3);self.table.setHorizontalHeaderLabels(['服务商','本地密钥','操作']);header=self.table.horizontalHeader();header.setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents);header.setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch);header.setSectionResizeMode(2,QHeaderView.ResizeMode.ResizeToContents);layout.addWidget(self.table,1)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.status=QLabel();layout.addWidget(self.status);close=QPushButton('关闭');close.clicked.connect(self.reject);layout.addWidget(close)
        if not state or state['identity'].get('role')!='pi':self.status.setText('此功能仅限PI。');return
        from lab_local import account
        keys=(account(manager.store.database,self.owner) or {}).get('keys',{})
        self.table.setRowCount(len(keys))
        for i,provider in enumerate(keys):
            self.table.setItem(i,0,QTableWidgetItem(LAB_PROVIDERS.get(provider,provider)));self.table.setItem(i,1,QTableWidgetItem('••••••••••••'))
            button=QPushButton('复制API Key');button.clicked.connect(lambda checked=False,p=provider:self.copy_key(p));self.table.setCellWidget(i,2,button)
        self.table.resizeRowsToContents();self.status.setText('' if keys else '当前PI尚无本地Key缓存，请先在管理窗口同步。')
    def copy_key(self,provider):
        state=self.manager.store.lab_state()
        if not state or state['identity'].get('role')!='pi' or state['identity'].get('local_owner')!=self.owner:
            self.status.setText('当前账户已变化，请关闭并重新打开。');return
        from lab_local import account
        key=(account(self.manager.store.database,self.owner) or {}).get('keys',{}).get(provider)
        if not key:self.status.setText('此平台的本地Key已不存在，请重新同步。');return
        QApplication.clipboard().setText(key);QMessageBox.information(self,'复制成功','已复制，可以粘贴到您自己的文档查看。请私密保管。')
