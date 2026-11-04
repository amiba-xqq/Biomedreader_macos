"""Protected cached student access IDs and explicit export/reset UI."""
from PySide6.QtWidgets import QVBoxLayout,QLabel,QPlainTextEdit,QHBoxLayout,QPushButton,QApplication,QMessageBox
from lab_ui import CloudDialog
from lab_local import encrypt,decrypt,account
from lab_service import LabAPI

def install(c):c.execute('CREATE TABLE IF NOT EXISTS ai_lab_member_ids(owner TEXT NOT NULL,user_id TEXT NOT NULL,encrypted_id TEXT NOT NULL,PRIMARY KEY(owner,user_id))')
def cache_id(database,owner,user_id,access):
    with database.connect() as c:
        install(c);c.execute('INSERT INTO ai_lab_member_ids VALUES(?,?,?) ON CONFLICT(owner,user_id) DO UPDATE SET encrypted_id=excluded.encrypted_id',(owner,user_id,encrypt(dict(access_id=access))))
def cached_id(database,owner,user_id):
    with database.connect() as c:install(c);row=c.execute('SELECT encrypted_id FROM ai_lab_member_ids WHERE owner=? AND user_id=?',(owner,user_id)).fetchone()
    return decrypt(row[0])['access_id'] if row else None

class ExportIDDialog(CloudDialog):
    def __init__(self,manager,member,parent=None):
        super().__init__(parent);self.manager=manager;self.member=member;self.task=None;self.state=manager.store.lab_state();self.identity=self.state['identity'];self.owner=self.identity.get('local_owner','');self.value=None
        self.setWindowTitle('导出PI用户ID' if member['role']=='pi' else '导出学生完整访问ID');self.resize(720,330)
        layout=QVBoxLayout(self);self.status=QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        self.text=QPlainTextEdit();self.text.setReadOnly(True);layout.addWidget(self.text,1)
        row=QHBoxLayout();self.copy=QPushButton('复制ID');self.copy.setEnabled(False);self.copy.clicked.connect(self.copy_value);row.addWidget(self.copy)
        self.reset=QPushButton('重置并导出新ID');self.reset.clicked.connect(self.reset_id);self.reset.hide();row.addWidget(self.reset)
        close=QPushButton('关闭');close.clicked.connect(self.reject);row.addWidget(close);layout.addLayout(row)
        if member['role']=='pi':self.loaded(dict(access_id=member['user_id']));return
        value=cached_id(manager.store.database,self.owner,member['user_id'])
        if self.identity['role']=='student' and member['user_id']==self.identity['user_id']:value=(account(manager.store.database,self.owner) or {}).get('student_id')
        if value:self.loaded(dict(access_id=value));return
        if self.identity['role']!='pi':self.status.setText('未缓存完整访问ID，请联系PI。');return
        self.status.setText('正在读取完整访问ID，可随时关闭……');self.task=manager.submit(self,lambda:self.api().member_access(member['user_id']),'loaded','failed')
    def api(self):return LabAPI(self.manager.store.lab_token(),self.identity.get('gateway_url'))
    def loaded(self,data):
        if getattr(self,'_closed',False):return
        current=self.manager.store.lab_state()
        if not current or current['identity'].get('local_owner')!=self.owner:self.failed('账户已切换，请重新打开。');return
        self.task=None;self.value=data.get('access_id')
        if self.value:
            if self.member['role']=='student':cache_id(self.manager.store.database,self.owner,self.member['user_id'],self.value)
            self.text.setPlainText(self.value);self.copy.setEnabled(True);self.reset.hide()
            self.status.setText('这是PI用户编号；PI登录仍使用API Key。' if self.member['role']=='pi' else '这是完整学生访问ID，请像密码一样私密保管。')
        else:
            self.status.setText('旧记录仅保存了授权凭证的哈希，无法还原完整访问ID。可由PI确认重置后生成新ID；旧ID与其云端会话会立即失效。')
            self.reset.setVisible(self.identity['role']=='pi')
    def copy_value(self):
        current=self.manager.store.lab_state()
        if not current or current['identity'].get('local_owner')!=self.owner:self.failed('账户已切换，请重新打开。');return
        if self.value:QApplication.clipboard().setText(self.value);QMessageBox.information(self,'复制成功','已复制，可以粘贴。')
    def reset_id(self):
        if self.task or self.identity['role']!='pi':return
        current=self.manager.store.lab_state()
        if not current or current['identity'].get('local_owner')!=self.owner:self.failed('账户已切换，请重新打开。');return
        if QMessageBox.question(self,'确认重置访问ID','重置会使旧访问ID和旧云端会话立即失效，是否继续？',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        self.task=self.manager.submit(self,lambda:self.api().member_action(self.member['user_id'],'reset'),'loaded','failed')
    def failed(self,message):
        if not getattr(self,'_closed',False):self.task=None;self.status.setText(message)
