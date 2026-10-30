"""PI/student role management. API keys never appear in student UI or exports."""
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QPainter,QColor
from PySide6.QtWidgets import (QWidget,QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QLineEdit,
    QPushButton,QComboBox,QFrame,QMessageBox,QTableWidget,QTableWidgetItem,QAbstractItemView,
    QScrollArea,QApplication,QPlainTextEdit)
from ai_ui import plain_label
from ai_store import PROVIDERS
from lab_service import LabAPI,LAB_PROVIDERS

class TokenChart(QWidget):
    def __init__(self,parent=None):super().__init__(parent);self.rows=[];self.setMinimumHeight(100)
    def set_rows(self,rows):self.rows=rows;self.setMinimumHeight(max(100,len(rows)*48+20));self.update()
    def paintEvent(self,event):
        painter=QPainter(self);painter.fillRect(self.rect(),QColor('#FFFAF4'))
        maximum=max([row['total_tokens'] for row in self.rows] or [1]) or 1
        for index,row in enumerate(self.rows):
            y=12+index*48;painter.setPen(QColor('#4A3424'))
            name=('PI · ' if row['role']=='pi' else '')+row['name']
            painter.drawText(12,y+15,painter.fontMetrics().elidedText(name,Qt.TextElideMode.ElideRight,150))
            width=max(1,int(max(1,self.width()-285)*row['total_tokens']/maximum))
            painter.fillRect(170,y,width,22,QColor('#F28A22' if row['role']=='pi' else '#F5B25A'))
            painter.drawText(self.width()-105,y+16,f"{row['total_tokens']:,}")
        painter.end()

class CredentialDialog(QDialog):
    def __init__(self,result,parent=None):
        super().__init__(parent);self.setWindowTitle('学生访问ID · 仅本次展示');self.resize(720,260)
        layout=QVBoxLayout(self);layout.addWidget(plain_label('请将此完整访问ID私下交给对应学生。它包含随机授权凭证，请像密码一样保管；服务端仅保存其哈希。关闭后不能再次查看，可由 PI 重置。'))
        text=QPlainTextEdit(result['access_id']);text.setReadOnly(True);layout.addWidget(text)
        row=QHBoxLayout();copy=QPushButton('复制完整访问ID');copy.clicked.connect(lambda:(QApplication.clipboard().setText(result['access_id']),QMessageBox.information(self,'复制成功','已复制，请通过私密方式交给对应学生。')));row.addWidget(copy)
        close=QPushButton('已保存，关闭');close.clicked.connect(self.accept);row.addWidget(close);layout.addLayout(row)

class LoginDialog(QDialog):
    def __init__(self,manager,role,parent=None):
        super().__init__(parent);self.manager=manager;self.role=role;self.task=None
        self.setWindowTitle('PI 登录 / 建立课题组' if role=='pi' else '学生登录');self.resize(660,340)
        layout=QVBoxLayout(self)
        layout.addWidget(plain_label('PI Key 将通过 HTTPS 交给独立课题组服务，加密保存于服务端，由该服务代为调用模型。学生不会取得 PI Key。API 费用由 PI 承担；仅统计经课题组通道的调用。' if role=='pi' else '一个完整学生访问ID可使用PI开通的全部API平台；无需按平台另建ID，也无需API Key。绘图平台仅用于模型图；Codex本机登录不共享。'))
        form=QFormLayout();self.provider=QComboBox()
        for p,name in LAB_PROVIDERS.items():
            if p!='codex':self.provider.addItem(name,p)
        if role=='pi':form.addRow('服务商',self.provider)
        self.secret=QLineEdit();self.secret.setEchoMode(QLineEdit.EchoMode.Password);self.secret.setPlaceholderText('您的 API Key' if role=='pi' else 'PI 提供的完整访问ID');form.addRow('API Key' if role=='pi' else '学生访问ID',self.secret);layout.addLayout(form)
        self.extra_keys=[];self.extra_form=QFormLayout();layout.addLayout(self.extra_form)
        if role=='pi':
            add=QPushButton('＋ 同时绑定另一个服务商Key（可选）');add.clicked.connect(self.add_key_row);layout.addWidget(add)
        self.status=plain_label('',True);layout.addWidget(self.status);layout.addStretch();self.login=QPushButton('登录');self.login.clicked.connect(self.start);layout.addWidget(self.login)
        close=QPushButton('关闭');close.clicked.connect(self.reject);layout.addWidget(close)
    def start(self):
        if self.task:return
        key=self.secret.text().strip()
        if not key:QMessageBox.information(self,'请填写','请填写登录凭证。');return
        provider=self.provider.currentData();self.login.setEnabled(False);self.status.setText('正在验证并建立安全会话……网络连接失败时会尝试备用入口一次；此过程不调用生成模型。')
        additional=[(field.currentData(),secret.text().strip()) for field,secret in self.extra_keys if secret.text().strip()]
        if len({provider,*[p for p,k in additional]})!=1+len(additional):self.login.setEnabled(True);self.status.setText('每个服务商只需一个Key，请去除重复服务商。');return
        def work():
            result=LabAPI().login_pi(provider,key) if self.role=='pi' else LabAPI().login_student(key)
            failures=[]
            for p,k in additional:
                try:
                    gateway=result['identity'].get('gateway_url');identity=LabAPI(result['token'],gateway).save_key(p,k)['identity'];identity['gateway_url']=gateway;result['identity']=identity
                except ValueError:failures.append(LAB_PROVIDERS[p])
            result['partial_failures']=failures;return result
        self.task=self.manager.submit(self,work,'loaded','failed')
    def add_key_row(self):
        if self.task:return
        provider=QComboBox()
        for p,n in LAB_PROVIDERS.items():
            if p!='codex':provider.addItem(n,p)
        secret=QLineEdit();secret.setEchoMode(QLineEdit.EchoMode.Password);secret.setPlaceholderText('可选，留空不绑定');row=QHBoxLayout();row.addWidget(provider);row.addWidget(secret);self.extra_form.addRow(row);self.extra_keys.append((provider,secret))
    def loaded(self,result):
        for p,secret in self.extra_keys:secret.clear()
        if result.get('partial_failures'):QMessageBox.information(self,'部分服务商未绑定','主账户已登录。以下服务商未绑定成功，可在管理窗口重新配置：'+ '、'.join(result['partial_failures']))
        self.task=None;self.secret.clear();self.manager.store.save_lab_session(result['token'],result['identity']);self.manager.changed.emit();self.accept()
    def failed(self,message):self.task=None;self.login.setEnabled(True);self.status.setText(message)
    def reject(self):
        if self.task:self.status.setText('登录尚在处理，请等待结果。');return
        super().reject()

class LabManagementDialog(QDialog):
    def __init__(self,manager,parent=None):
        super().__init__(parent);self.manager=manager;self.task=None;self.rows=[];self.token=manager.store.lab_token()
        self.identity=manager.store.lab_state()['identity'];self.is_pi=self.identity['role']=='pi'
        self.setWindowTitle('课题组用量与学生管理' if self.is_pi else '我的课题组用量');self.resize(1000,800)
        layout=QVBoxLayout(self);layout.addWidget(plain_label('仅统计经过课题组服务的确认 Token，包含所有设备和模型。个人 Key 直连不在此统计。未确认响应可能已计费，请以服务商账单为准；此处不是账单余额。',True))
        self.status=plain_label();layout.addWidget(self.status)
        scroll=QScrollArea();scroll.setWidgetResizable(True);self.chart=TokenChart();scroll.setWidget(self.chart);layout.addWidget(scroll,1)
        self.table=QTableWidget(0,6);self.table.setHorizontalHeaderLabels(['姓名 / 角色','user_id','输入 Token','输出 Token','总 Token','状态'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection);self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);layout.addWidget(self.table,1)
        row=QHBoxLayout();refresh=QPushButton('刷新用量');refresh.clicked.connect(self.reload);row.addWidget(refresh)
        if self.is_pi:
            for title,action in [('新增学生',self.add_student),('停用学生',lambda:self.action('disable')),('启用学生',lambda:self.action('enable')),('重置访问ID',lambda:self.action('reset')),('配置课题组Key',self.configure_key)]:
                button=QPushButton(title);button.clicked.connect(action);row.addWidget(button)
        layout.addLayout(row)
        if self.is_pi:
            delete=QPushButton('删除学生信息');delete.setProperty('secondary',True);delete.clicked.connect(lambda:self.action('delete'));layout.addWidget(delete)
        close=QPushButton('关闭');close.clicked.connect(self.reject);layout.addWidget(close);self.reload()
    def submit(self,work,done='loaded'):
        if self.task:return False
        self.status.setText('正在处理……');self.task=self.manager.submit(self,work,done,'failed');return True
    def reload(self):self.submit(lambda:LabAPI(self.token).usage())
    def loaded(self,rows):
        self.task=None;self.rows=rows;self.chart.set_rows(rows);self.table.setRowCount(len(rows))
        for index,row in enumerate(rows):
            values=[row['name']+' / '+row['role'],row['user_id'],f"{row['input_tokens']:,}",f"{row['output_tokens']:,}",f"{row['total_tokens']:,}",'可用' if row['enabled'] else '已停用']
            for col,value in enumerate(values):self.table.setItem(index,col,QTableWidgetItem(value))
        self.table.resizeColumnsToContents();self.status.setText('已更新 · 未确认用量的调用：'+str(sum(row['unknown_calls'] for row in rows)))
    def failed(self,message):self.task=None;self.status.setText(message)
    def add_student(self):
        from PySide6.QtWidgets import QInputDialog
        if self.task:return
        name,ok=QInputDialog.getText(self,'新增学生','学生姓名或备注（最长80字符）')
        if ok and name.strip():self.submit(lambda:LabAPI(self.token).create_student(name.strip()),'credential_loaded')
    def credential_loaded(self,result):self.task=None;CredentialDialog(result,self).exec();self.reload()
    def selected(self):
        index=self.table.currentRow()
        if index<0 or self.rows[index]['role']!='student':QMessageBox.information(self,'请选择学生','请先选择一个学生行。');return None
        return self.rows[index]
    def action(self,action):
        if self.task:return
        row=self.selected()
        if not row:return
        if QMessageBox.question(self,'确认操作','对 '+row['name']+' 执行 '+{'enable':'启用','disable':'停用','reset':'重置访问ID（旧ID和会话立即失效）','delete':'永久删除学生信息（访问ID与会话立即失效）'}[action]+'？')!=QMessageBox.StandardButton.Yes:return
        self.submit(lambda:LabAPI(self.token).member_action(row['user_id'],action),'credential_loaded' if action=='reset' else 'action_loaded')
    def action_loaded(self,result):self.task=None;self.reload()
    def configure_key(self):
        if self.task:return
        dialog=QDialog(self);dialog.setWindowTitle('新增 / 更换课题组服务商Key');layout=QFormLayout(dialog);provider=QComboBox()
        for p,name in LAB_PROVIDERS.items():
            if p!='codex':provider.addItem(name,p)
        secret=QLineEdit();secret.setEchoMode(QLineEdit.EchoMode.Password);layout.addRow('服务商',provider);layout.addRow('API Key',secret)
        save=QPushButton('确认提交服务端');save.clicked.connect(dialog.accept);layout.addRow(save)
        if dialog.exec()==QDialog.DialogCode.Accepted and secret.text().strip():
            p=provider.currentData();key=secret.text().strip();secret.clear();self.submit(lambda:LabAPI(self.token).save_key(p,key),'key_loaded')
    def key_loaded(self,result):
        self.task=None;self.manager.store.save_lab_session(self.token,result['identity']);self.manager.changed.emit();self.status.setText('服务商已更新，现有学生ID继续有效；请在对应模型卡片设置中选择模型。绘图平台仅用于模型图。')

class LabPanel(QFrame):
    def __init__(self,manager,parent=None):
        super().__init__(parent);self.manager=manager;self.task=None;layout=QVBoxLayout(self)
        row=QHBoxLayout();row.addWidget(QLabel('调用通道'));self.mode=QComboBox();self.mode.addItems(['个人 API Key 直连','课题组授权通道']);row.addWidget(self.mode);row.addStretch();layout.addLayout(row)
        self.status=plain_label('',True);layout.addWidget(self.status);self.available_buttons={}
        for role,title,description in [('pi','PI · 课题组管理','使用自己的 API Key 建立课题组，创建学生访问ID，管理权限并查看累计 Token 柱状图。'),('student','学生 · 课题组授权','使用 PI 提供的访问ID登录，不需要 PI 的 API Key。也可切回个人通道，使用自己的 Key。')]:
            card=QFrame();card.setObjectName('LabRoleCard');card.setStyleSheet('QFrame#LabRoleCard {background:#FFF8EB;border:1px solid #EDCEAA;border-radius:12px;}');box=QVBoxLayout(card);heading=plain_label(title);heading.setStyleSheet('font-weight:700;color:#B45309;');box.addWidget(heading);box.addWidget(plain_label(description,True));controls=QHBoxLayout()
            login=QPushButton('PI登录' if role=='pi' else '学生登录');login.clicked.connect(lambda checked=False,r=role:self.login(r));controls.addWidget(login)
            manage=QPushButton('学生与用量管理' if role=='pi' else '我的用量');manage.clicked.connect(lambda checked=False,r=role:self.manage(r));controls.addWidget(manage)
            available=QPushButton('可用AI模型');available.clicked.connect(self.available_models);controls.addWidget(available);self.available_buttons[role]=available
            controls.addStretch();box.addLayout(controls);layout.addWidget(card)
        logout=self.logout_button=QPushButton('退出课题组登录');logout.setProperty('secondary',True);logout.clicked.connect(self.logout);logout_row=QHBoxLayout();logout_row.addWidget(logout);logout_row.addStretch();layout.addLayout(logout_row)
        self.mode.currentIndexChanged.connect(self.change_mode);manager.changed.connect(self.reload);self.reload()
    def reload(self):
        state=self.manager.store.lab_state();self.mode.blockSignals(True);self.mode.setCurrentIndex(1 if state and state['active'] else 0);self.mode.blockSignals(False)
        self.logout_button.setVisible(bool(state))
        for role,button in self.available_buttons.items():button.setVisible(bool(state and state['identity']['role']==role))
        self.status.setText(('已登录：'+state['identity']['name']+' / '+state['identity']['role']+' · '+('课题组通道已启用' if state['active'] else '当前使用个人 Key')) if state else '尚未登录课题组。个人模型设置独立保留。')
    def showEvent(self,event):
        super().showEvent(event)
        if self.manager.store.lab_state() and not self.task:
            token=self.manager.store.lab_token();self.task=self.manager.submit(self,lambda:LabAPI(token).me(),'identity_loaded','identity_failed')
    def identity_loaded(self,identity):
        self.task=None;state=self.manager.store.lab_state()
        if not state or identity.get('user_id')!=state['identity'].get('user_id'):return
        identity['gateway_url']=state['identity'].get('gateway_url','')
        import json
        with self.manager.store.database.connect() as c:c.execute('UPDATE ai_lab_session SET identity_json=? WHERE id=1',(json.dumps(identity,ensure_ascii=False),))
        self.manager.changed.emit()
    def identity_failed(self,message):self.task=None;self.status.setText('授权信息暂未刷新：'+message)
    def change_mode(self,index):
        try:self.manager.store.use_lab(index==1);self.manager.changed.emit()
        except ValueError as error:QMessageBox.information(self,'请先登录',str(error));self.reload()
    def available_models(self):
        from lab_models_ui import AvailableModelsDialog
        AvailableModelsDialog(self.manager,self).exec()
    def login(self,role):LoginDialog(self.manager,role,self).exec()
    def manage(self,role):
        state=self.manager.store.lab_state()
        if not state or state['identity']['role']!=role:QMessageBox.information(self,'请先登录','请先使用对应的 PI 或学生账户登录。');return
        LabManagementDialog(self.manager,self).exec()
    def logout(self):
        if not self.manager.store.lab_state() or self.task:return
        token=self.manager.store.lab_token();self.task=self.manager.submit(self,lambda:LabAPI(token).logout(),'logged_out','logout_failed')
    def logged_out(self,result):self.task=None;self.manager.store.logout_lab();self.manager.changed.emit()
    def logout_failed(self,message):
        self.task=None;self.manager.store.logout_lab();self.manager.changed.emit();QMessageBox.information(self,'已清除本机登录','本机登录已清除，远端会话未能确认撤销，将在到期后失效。')
