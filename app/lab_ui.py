"""PI/student role management. API keys never appear in student UI or exports."""
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import (QWidget,QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QLineEdit,
    QPushButton,QComboBox,QFrame,QMessageBox,QTableWidget,QTableWidgetItem,QAbstractItemView,
    QScrollArea,QApplication,QPlainTextEdit)
from ai_ui import plain_label
from ai_store import PROVIDERS
from lab_service import LabAPI,LAB_PROVIDERS,LAB_URL,LAB_CUSTOM_URL
from lab_activity import TokenActivity,year_days
from datetime import date
from lab_login_budget import remaining,success,exhausted

class CloudDialog(QDialog):
    """Close immediately, cancel the task, and never apply late callbacks."""
    def cancel_pending(self):
        self._closed=True
        if getattr(self,'task',None):self.manager.cancel(self.task);self.task=None
    def reject(self):self.cancel_pending();super().reject()
    def closeEvent(self,event):self.cancel_pending();super().closeEvent(event)

class CredentialDialog(QDialog):
    def __init__(self,result,parent=None):
        super().__init__(parent);self.setWindowTitle('学生完整访问ID');self.resize(720,260)
        layout=QVBoxLayout(self);layout.addWidget(plain_label('请将此完整访问ID私下交给对应学生，像密码一样保管。PI可通过“导出ID”再次查看；重置后旧ID与云端会话立即失效。'))
        text=QPlainTextEdit(result['access_id']);text.setReadOnly(True);layout.addWidget(text)
        row=QHBoxLayout();copy=QPushButton('复制完整访问ID');copy.clicked.connect(lambda:(QApplication.clipboard().setText(result['access_id']),QMessageBox.information(self,'复制成功','已复制，请通过私密方式交给对应学生。')));row.addWidget(copy)
        close=QPushButton('已保存，关闭');close.clicked.connect(self.accept);row.addWidget(close);layout.addLayout(row)

class LoginDialog(CloudDialog):
    def __init__(self,manager,role,parent=None):
        super().__init__(parent);self.manager=manager;self.role=role;self.task=None
        self.setWindowTitle('PI 登录 / 建立课题组' if role=='pi' else '学生登录');self.resize(660,340)
        layout=QVBoxLayout(self)
        layout.addWidget(plain_label('会连接到 Cloudflare，可能需要科学上网。PI和学生每天各可登录20次，等待期间可以取消或关闭窗口。',True))
        layout.addWidget(plain_label('使用服务商API Key验证课题组身份，API费用由PI承担。' if role=='pi' else '一个完整学生访问ID可使用PI开通的全部API平台；无需按平台另建ID，也无需API Key。绘图平台仅用于模型图；Codex本机登录不共享。'))
        form=QFormLayout();self.provider=QComboBox()
        self.gateway=QComboBox();self.gateway.addItem('默认 · biomedreader.cn',LAB_CUSTOM_URL);self.gateway.addItem('备用入口 · workers.dev',LAB_URL);form.addRow('登录入口',self.gateway)
        from settings_wheel import ClickOnlyComboGuard
        self.gateway_guard=ClickOnlyComboGuard(self.gateway)
        for p,name in LAB_PROVIDERS.items():
            if p!='codex':self.provider.addItem(name,p)
        if role=='pi':form.addRow('服务商',self.provider)
        self.secret=QLineEdit();self.secret.setEchoMode(QLineEdit.EchoMode.Password);self.secret.setPlaceholderText('您的 API Key' if role=='pi' else 'PI 提供的完整访问ID');form.addRow('API Key' if role=='pi' else '学生访问ID',self.secret);layout.addLayout(form)
        self.extra_keys=[];self.extra_form=QFormLayout();layout.addLayout(self.extra_form)
        if role=='pi':
            add=QPushButton('＋ 同时绑定另一个服务商Key（可选）');add.clicked.connect(self.add_key_row);layout.addWidget(add)
        self.status=plain_label('',True);layout.addWidget(self.status);layout.addStretch();self.login=QPushButton();self.login.clicked.connect(self.start);layout.addWidget(self.login)
        close=QPushButton('关闭');close.clicked.connect(self.reject);layout.addWidget(close)
        self._attempt_id=None;self.refresh_login();manager.changed.connect(self.refresh_login)
        self.budget_clock=QTimer(self);self.budget_clock.setInterval(1000);self.budget_clock.timeout.connect(self.check_day);self.budget_clock.start()
    def refresh_login(self):
        self._budget_day=date.today();count=remaining(self.manager.store.database,self.role)
        self.login.setText(f'登录（{count}）');self.login.setEnabled(count>0 and not self.task and not self.manager.store.lab_state() and not getattr(self,'_closed',False))
    def check_day(self):
        if date.today()!=self._budget_day:self.refresh_login()
    def start(self):
        self.refresh_login()
        if not self.login.isEnabled():return
        key=self.secret.text().strip()
        if not key:QMessageBox.information(self,'请填写','请填写登录凭证。');return
        provider=self.provider.currentData();gateway=self.gateway.currentData();self.login.setEnabled(False);self.status.setText('正在通过所选入口验证身份……此过程不调用生成模型，可随时关闭。')
        additional=[(field.currentData(),secret.text().strip()) for field,secret in self.extra_keys if secret.text().strip()]
        if len({provider,*[p for p,k in additional]})!=1+len(additional):self.login.setEnabled(True);self.status.setText('每个服务商只需一个Key，请去除重复服务商。');return
        self._attempt_id=__import__('uuid').uuid4().hex;attempt=self._attempt_id
        def work():
            from lab_service import StudentDisabledError
            if self.role=='student':
                from lab_local import owner_for
                from student_keys import clear_keys
                clear_keys(self.manager.store.database,owner_for('student',{},key))
            try:result=LabAPI(gateway=gateway).login_pi(provider,key) if self.role=='pi' else LabAPI(gateway=gateway).login_student(key)
            except StudentDisabledError as error:return dict(disabled=True,student_id=key,identity=dict(error.identity,gateway_url=gateway))
            success(self.manager.store.database,self.role,attempt,result.get('logins_today',0))
            failures=[]
            for p,k in additional:
                try:
                    bound_gateway=result['identity'].get('gateway_url');identity=LabAPI(result['token'],bound_gateway).save_key(p,k)['identity'];identity['gateway_url']=bound_gateway;result['identity']=identity
                except ValueError:failures.append(LAB_PROVIDERS[p])
            result['partial_failures']=failures
            api=LabAPI(result['token'],result['identity'].get('gateway_url'));result['student_id']=key if self.role=='student' else ''
            try:
                snapshot=api.credentials();result['keys']=snapshot['keys'];result['identity'].update(snapshot['identity'])
            except ValueError:
                if self.role=='student':raise
                result['keys']={};result['sync_warning']=True
            if self.role=='student' and not result.get('keys'):raise ValueError('课题组没有返回可用Key，请联系PI检查配置。')
            try:result['members']=api.members()
            except ValueError:result['members']=[]
            from lab_local import owner_for,account
            owner=owner_for(self.role,result.get('keys',{}),result['student_id'])
            if not account(self.manager.store.database,owner):
                try:result['daily']=api.usage_activity(year_days())['daily']
                except ValueError:result['daily']=[]
            return result
        self.task=self.manager.submit(self,work,'loaded','failed')
    def add_key_row(self):
        if self.task:return
        provider=QComboBox()
        for p,n in LAB_PROVIDERS.items():
            if p!='codex':provider.addItem(n,p)
        secret=QLineEdit();secret.setEchoMode(QLineEdit.EchoMode.Password);secret.setPlaceholderText('可选，留空不绑定');row=QHBoxLayout();row.addWidget(provider);row.addWidget(secret);self.extra_form.addRow(row);self.extra_keys.append((provider,secret))
    def loaded(self,result):
        if getattr(self,'_closed',False):return
        if result.get('disabled'):
            from student_policy import activate_disabled,DISABLED_MESSAGE
            activate_disabled(self.manager.store,result['student_id'],result['identity'])
            self.task=None;self.secret.clear();self.manager.changed.emit();QMessageBox.information(self,'学生ID已被停用',DISABLED_MESSAGE);self.accept();return
        for p,secret in self.extra_keys:secret.clear()
        if result.get('partial_failures'):QMessageBox.information(self,'部分服务商未绑定','主账户已登录。以下服务商未绑定成功，可在管理窗口重新配置：'+ '、'.join(result['partial_failures']))
        if getattr(self,'_closed',False):return
        success(self.manager.store.database,self.role,self._attempt_id or __import__('uuid').uuid4().hex,result.get('logins_today',0))
        from lab_local import save_account
        owner=None
        if self.role=='pi' and not result.get('keys'):
            owner='pi-login:'+__import__('hashlib').sha256(self.secret.text().strip().encode()).hexdigest()
        identity=save_account(self.manager.store.database,result['identity'],result.get('keys',{}),result.get('student_id',''),owner=owner,members=result.get('members',[]),daily=result.get('daily',[]))
        self.task=None;self.secret.clear();self.manager.store.save_lab_session(result['token'],identity);self.manager.changed.emit()
        from student_keys import logged_in
        logged_in(self.manager.store.database,identity)
        if result.get('sync_warning'):QMessageBox.information(self,'已登录，密钥待同步','云端验证已通过，但本地Key同步未完成；当前继续云端转发，可在管理窗口重新同步。')
        self.accept()
    def failed(self,message):
        if getattr(self,'_closed',False):return
        self.task=None
        if '今日已登录20次' in message:exhausted(self.manager.store.database,self.role)
        self.refresh_login();self.status.setText(message)

class LabManagementDialog(CloudDialog):
    def __init__(self,manager,parent=None):
        super().__init__(parent);self.manager=manager;self.task=None;self.rows=[];self.token=manager.store.lab_token()
        self.identity=manager.store.lab_state()['identity'];self.is_pi=self.identity['role']=='pi'
        self.setWindowTitle('课题组用量与学生管理' if self.is_pi else '我的课题组用量');self.resize(1000,800)
        layout=QVBoxLayout(self);layout.addWidget(plain_label('仅展示当前登录账户在本机的最近一年用量；不包含其它设备或其它学生。缓存按账户独立保存，清理无用缓存不会删除。未确认调用及完整账单以服务商为准。',True))
        self.status=plain_label();layout.addWidget(self.status)
        sources=QHBoxLayout();sources.addWidget(QLabel('服务商来源'));self.usage_source=QComboBox();self.usage_source.addItem('全部来源',None);self.usage_source.setMinimumWidth(220);sources.addWidget(self.usage_source);sources.addStretch();layout.addLayout(sources)
        self.activity=TokenActivity();scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(self.activity);layout.addWidget(scroll,1)
        self.usage_source.currentIndexChanged.connect(lambda _:self.activity.select_provider(self.usage_source.currentData()))
        from settings_wheel import ClickOnlyComboGuard
        self.source_guard=ClickOnlyComboGuard(self.usage_source)
        self.table=QTableWidget(0,4);self.table.setHorizontalHeaderLabels(['姓名 / 角色','user_id','状态','导出ID'])
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
        if self.task or getattr(self,'_closed',False):return False
        self.status.setText('正在处理……');self.task=self.manager.submit(self,work,done,'failed');return True
    def api(self):return LabAPI(self.token,self.identity.get('gateway_url'))
    def reload(self):
        from lab_local import account,activity
        saved=account(self.manager.store.database,self.identity.get('local_owner',''))
        self.loaded(saved.get('members',[]) if saved else [])
        data=activity(self.manager.store.database,self.identity);self.activity.set_activity(data)
        selected=self.usage_source.currentData();providers=set(self.identity.get('providers',[]))|{row.get('provider','legacy') for row in data['daily']}
        self.usage_source.blockSignals(True);self.usage_source.clear();self.usage_source.addItem('全部来源',None)
        for provider in sorted(providers):self.usage_source.addItem('未分类历史用量' if provider=='legacy' else LAB_PROVIDERS.get(provider,provider),provider)
        index=self.usage_source.findData(selected);self.usage_source.setCurrentIndex(max(0,index));self.usage_source.blockSignals(False);self.activity.select_provider(self.usage_source.currentData())
        self.status.setText('已读取本地每日用量 · 仅当前账户、本机统计；可按服务商筛选，悬停查看明细。')
    def loaded(self,rows):
        self.task=None;self.rows=rows;self.table.setRowCount(len(rows))
        for index,row in enumerate(rows):
            values=[row['name']+' / '+row['role'],row['user_id'],'可用' if row['enabled'] else '已停用']
            for col,value in enumerate(values):self.table.setItem(index,col,QTableWidgetItem(value))
            export=QPushButton('导出PI ID' if row['role']=='pi' else '导出学生ID');export.clicked.connect(lambda checked=False,r=dict(row):self.export_id(r));self.table.setCellWidget(index,3,export)
        self.table.resizeColumnsToContents()
        if rows:self.table.setColumnWidth(3,max(self.table.columnWidth(3),self.table.cellWidget(0,3).sizeHint().width()+8))
        self.table.resizeRowsToContents()
        for index in range(len(rows)):
            self.table.setRowHeight(index,max(self.table.rowHeight(index),self.table.cellWidget(index,3).sizeHint().height()+6))
    def export_id(self,member):
        from lab_member_ids import ExportIDDialog
        ExportIDDialog(self.manager,member,self).exec()
    def failed(self,message):
        if not getattr(self,'_closed',False):
            self.task=None;self.status.setText(message)
    def sync(self):
        token=self.token;api=self.api()
        def work():
            data=api.credentials();data['members']=api.members();return data
        self.submit(work,'synced')
    def synced(self,data):
        if getattr(self,'_closed',False):return
        current=self.manager.store.lab_state()
        if not current or current['identity'].get('local_owner')!=self.identity.get('local_owner'):
            self.task=None;self.status.setText('当前账户已切换，未覆盖其它账户的缓存。');return
        from lab_local import save_account
        identity=dict(data['identity']);identity['gateway_url']=self.identity.get('gateway_url','')
        # New PI key combinations become a separate protected account cache.
        from lab_local import account
        old=account(self.manager.store.database,self.identity.get('local_owner','')) or {}
        identity=save_account(self.manager.store.database,identity,data['keys'],old.get('student_id',''),members=data['members'])
        self.task=None;self.identity=identity;self.manager.store.save_lab_session(self.token,identity);self.manager.changed.emit();self.reload()
    def add_student(self):
        from PySide6.QtWidgets import QInputDialog
        if self.task:return
        name,ok=QInputDialog.getText(self,'新增学生','学生姓名或备注（最长80字符）')
        if ok and name.strip():self.submit(lambda:self.api().create_student(name.strip()),'credential_loaded')
    def credential_loaded(self,result):
        if getattr(self,'_closed',False):return
        from lab_member_ids import cache_id
        cache_id(self.manager.store.database,self.identity.get('local_owner',''),result['user_id'],result['access_id'])
        self.task=None;self.sync();CredentialDialog(result,self).exec()
    def selected(self):
        index=self.table.currentRow()
        if index<0 or self.rows[index]['role']!='student':QMessageBox.information(self,'请选择学生','请先选择一个学生行。');return None
        return self.rows[index]
    def action(self,action):
        if self.task:return
        row=self.selected()
        if not row:return
        if QMessageBox.question(self,'确认操作','对 '+row['name']+' 执行 '+{'enable':'启用','disable':'停用','reset':'重置访问ID（旧ID和会话立即失效）','delete':'永久删除学生信息（访问ID与会话立即失效）'}[action]+'？')!=QMessageBox.StandardButton.Yes:return
        self.submit(lambda:self.api().member_action(row['user_id'],action),'credential_loaded' if action=='reset' else 'action_loaded')
    def action_loaded(self,result):self.task=None;self.sync()
    def configure_key(self):
        if self.task:return
        dialog=QDialog(self);dialog.setWindowTitle('新增 / 更换课题组服务商Key');layout=QFormLayout(dialog);provider=QComboBox()
        for p,name in LAB_PROVIDERS.items():
            if p!='codex':provider.addItem(name,p)
        secret=QLineEdit();secret.setEchoMode(QLineEdit.EchoMode.Password);layout.addRow('服务商',provider);layout.addRow('API Key',secret)
        save=QPushButton('确认提交服务端');save.clicked.connect(dialog.accept);layout.addRow(save)
        if dialog.exec()==QDialog.DialogCode.Accepted and secret.text().strip():
            p=provider.currentData();key=secret.text().strip();secret.clear();self.submit(lambda:self.api().save_key(p,key),'key_loaded')
    def key_loaded(self,result):self.task=None;self.sync()

class LabPanel(QFrame):
    def __init__(self,manager,parent=None):
        super().__init__(parent);self.manager=manager;self.task=None;layout=QVBoxLayout(self)
        row=QHBoxLayout();row.addWidget(QLabel('未单独设置时的默认通道'));self.mode=QComboBox();self.mode.addItems(['优先个人 API Key 直连','优先课题组授权']);row.addWidget(self.mode);row.addStretch();layout.addLayout(row)
        self.status=plain_label('',True);layout.addWidget(self.status);self.available_buttons={};self.login_buttons={}
        for role,title,description in [('pi','PI · 课题组管理','使用自己的 API Key 建立课题组，创建学生访问ID，管理权限并查看本机最近一年的每日Token用量。'),('student','学生 · 课题组授权','使用 PI 提供的访问ID登录，不需要 PI 的 API Key。也可切回个人通道，使用自己的 Key。')]:
            card=QFrame();card.setObjectName('LabRoleCard');card.setStyleSheet('QFrame#LabRoleCard {background:#FFF8EB;border:1px solid #EDCEAA;border-radius:12px;}');box=QVBoxLayout(card);heading=plain_label(title);heading.setStyleSheet('font-weight:700;color:#B45309;');box.addWidget(heading);box.addWidget(plain_label(description,True));controls=QHBoxLayout()
            login=QPushButton('PI登录' if role=='pi' else '学生登录');login.clicked.connect(lambda checked=False,r=role:self.login(r));controls.addWidget(login);self.login_buttons[role]=login
            manage=QPushButton('学生与用量管理' if role=='pi' else '我的用量');manage.clicked.connect(lambda checked=False,r=role:self.manage(r));controls.addWidget(manage)
            available=QPushButton('可用AI模型');available.clicked.connect(self.available_models);controls.addWidget(available);self.available_buttons[role]=available
            if role=='pi':
                self.balance_button=QPushButton('余额查询');self.balance_button.clicked.connect(self.show_balance);controls.addWidget(self.balance_button)
                self.keys_button=QPushButton('查看API keys');self.keys_button.clicked.connect(self.show_keys);controls.addWidget(self.keys_button)
            controls.addStretch();box.addLayout(controls);layout.addWidget(card)
        logout=self.logout_button=QPushButton('退出课题组登录');logout.setProperty('secondary',True);logout.clicked.connect(self.logout);logout_row=QHBoxLayout();logout_row.addWidget(logout);logout_row.addStretch();layout.addLayout(logout_row)
        self.mode.currentIndexChanged.connect(self.change_mode);manager.changed.connect(self.reload);self.reload()
    def reload(self):
        state=self.manager.store.lab_state();self.mode.blockSignals(True);self.mode.setCurrentIndex(1 if state and state['active'] else 0);self.mode.blockSignals(False)
        self.logout_button.setVisible(bool(state));self.balance_button.setVisible(bool(state and state['identity']['role']=='pi'))
        self.keys_button.setVisible(bool(state and state['identity']['role']=='pi'))
        for button in self.login_buttons.values():button.setEnabled(not bool(state))
        for role,button in self.available_buttons.items():button.setVisible(bool(state and state['identity']['role']==role))
        self.status.setText(('已登录：'+state['identity']['name']+' / '+state['identity']['role']+' · '+('课题组通道已启用' if state['active'] else '当前使用个人 Key')) if state else '尚未登录课题组。个人模型设置独立保留。')
        if state and state['identity'].get('disabled'):self.status.setText('学生ID已被PI停用 · 课题组AI调用已锁定，可查看本地用量。')
    def showEvent(self,event):
        super().showEvent(event)
        if self.manager.store.lab_state() and not self.task and not self.manager.store.lab_state()['identity'].get('local_owner'):
            token=self.manager.store.lab_token();gateway=self.manager.store.lab_state()['identity'].get('gateway_url');self.task=self.manager.submit(self,lambda:LabAPI(token,gateway).me(),'identity_loaded','identity_failed')
    def identity_loaded(self,identity):
        self.task=None;state=self.manager.store.lab_state()
        if not state or state['identity'].get('local_owner') or identity.get('user_id')!=state['identity'].get('user_id'):return
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
    def show_balance(self):
        from lab_balance_ui import LabBalanceDialog
        LabBalanceDialog(self.manager,self).exec()
    def show_keys(self):
        from lab_keys_ui import LabKeysDialog
        LabKeysDialog(self.manager,self).exec()
    def login(self,role):
        if self.manager.store.lab_state():return
        LoginDialog(self.manager,role,self).exec()
    def manage(self,role):
        state=self.manager.store.lab_state()
        if not state or state['identity']['role']!=role:QMessageBox.information(self,'请先登录','请先使用对应的 PI 或学生账户登录。');return
        LabManagementDialog(self.manager,self).exec()
    def logout(self):
        state=self.manager.store.lab_state()
        if not state:return
        if self.task:self.manager.cancel(self.task)
        token=self.manager.store.lab_token();self.manager.store.logout_lab();self.manager.changed.emit()
        self.task=self.manager.submit(self,lambda:LabAPI(token,state['identity'].get('gateway_url')).logout(),'logged_out','logout_failed')
    def logged_out(self,result):self.task=None
    def logout_failed(self,message):self.task=None
