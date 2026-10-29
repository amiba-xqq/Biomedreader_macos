"""Optional API model cards and user-triggered keyword recommendations."""
import json
import weakref
from datetime import datetime
import shiboken6
from PySide6.QtCore import QObject,QRunnable,Signal,Slot,QThreadPool,Qt,QUrl,QEvent
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QWidget,QDialog,QFrame,QLabel,QPushButton,QToolButton,QLineEdit,
    QComboBox,QPlainTextEdit,QVBoxLayout,QHBoxLayout,QFormLayout,QScrollArea,QMessageBox,
    QApplication,QDialogButtonBox,QProgressBar,QSizePolicy,QSpinBox,QTabWidget)
from ai_store import AIStore,PROVIDERS,key_fingerprint
from ai_service import AIClient,generate_recommendations

class JobSignals(QObject):
    completed=Signal(int,object)
    failed=Signal(int,str)

class AIJob(QRunnable):
    def __init__(self,identifier,work):super().__init__();self.identifier=identifier;self.work=work;self.signals=JobSignals()
    def run(self):
        try:self.signals.completed.emit(self.identifier,self.work())
        except Exception as error:
            # Provider errors are sanitized by AIClient. Never propagate raw
            # transport errors, keys, request objects or response bodies to logs.
            message=str(error) if isinstance(error,ValueError) else 'AI 操作未完成，请检查网络与配置后重试。'
            self.signals.failed.emit(self.identifier,message[:400])

class AIManager(QObject):
    changed=Signal()
    progress=Signal(str,str)
    def __init__(self,database,parent=None):
        super().__init__(parent);self.store=AIStore(database);self.jobs={};self.serial=0
        from ai_repository import AIRepository
        self.repository=AIRepository(database)
        from ai_content import ArticleWebReader
        self.article_reader=ArticleWebReader(database.path.parent,parent or self)
        QThreadPool.globalInstance().setMaxThreadCount(max(8,QThreadPool.globalInstance().maxThreadCount()))
    def submit(self,owner,work,done,failed):
        self.serial+=1;identifier=self.serial;task=AIJob(identifier,work)
        self.jobs[identifier]=(task,weakref.ref(owner),done,failed)
        task.signals.completed.connect(self.completed);task.signals.failed.connect(self.failed)
        QThreadPool.globalInstance().start(task)
        return identifier
    @Slot(int,object)
    def completed(self,identifier,result):self.deliver(identifier,result,False)
    @Slot(int,str)
    def failed(self,identifier,message):self.deliver(identifier,message,True)
    def deliver(self,identifier,result,failed):
        job=self.jobs.pop(identifier,None)
        self.changed.emit()
        if job:
            owner=job[1]()
            if owner is not None and shiboken6.isValid(owner):getattr(owner,job[3] if failed else job[2])(result)

def plain_label(text='',muted=False):
    label=QLabel(text);label.setTextFormat(Qt.TextFormat.PlainText);label.setWordWrap(True)
    if muted:label.setObjectName('Muted')
    return label

def local_time(value):
    try:return datetime.fromisoformat(value).astimezone().strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError,TypeError):return '暂无'

class UsageLabel(QLabel):
    def setText(self,text):
        super().setText(text);self.update_height()
    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange,QEvent.Type.StyleChange):self.update_height()
    def update_height(self):
        self.setMinimumHeight(self.fontMetrics().lineSpacing()*max(1,len(self.text().splitlines()))+6)

class AISettingsDialog(QDialog):
    def __init__(self,manager,provider,parent=None):
        super().__init__(parent);self.manager=manager;self.provider=provider;self.task=None
        self.setWindowTitle(PROVIDERS[provider]+' · 模型设置');self.resize(680,430)
        self.config=manager.store.config(provider);self.models=json.loads(self.config['models_json']);self.models_key=self.config['key_fingerprint']
        self.lab=self.config.get('route')=='lab'
        outer=QVBoxLayout(self);outer.addWidget(plain_label('当前使用课题组授权通道。只选择模型，无需输入 PI 的 API Key。' if self.lab else '使用您自己的 API Key，直接访问服务商官方接口。密钥使用 Windows 当前用户加密保存；复制资料库到其他电脑后需重新输入。'))
        if provider=='openai':outer.addWidget(plain_label('这里接入 OpenAI API；ChatGPT 网页订阅与 API 额度分别管理，请使用 API 平台创建的密钥。',True))
        form=QFormLayout();self.key_edit=QLineEdit();self.key_edit.setEchoMode(QLineEdit.EchoMode.Password);self.key_edit.setMaxLength(2048)
        self.key_edit.setPlaceholderText('已加密保存，留空可保留原密钥' if self.config['encrypted_key'] else '粘贴您的 API Key')
        key_row=QHBoxLayout();key_row.addWidget(self.key_edit,1)
        reveal=QPushButton('显示');reveal.setCheckable(True);reveal.toggled.connect(lambda checked:(self.key_edit.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password),reveal.setText('隐藏' if checked else '显示')));key_row.addWidget(reveal)
        if not self.lab:form.addRow('API Key',key_row)
        else:self.key_edit.hide();reveal.hide()
        self.model=QComboBox();self.model.setEditable(True);self.model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert);self.model.setMinimumContentsLength(24)
        model_row=QHBoxLayout();model_row.addWidget(self.model,1);self.refresh_button=QPushButton('联网刷新模型');self.refresh_button.clicked.connect(self.refresh_models);model_row.addWidget(self.refresh_button)
        from reasoning import ReasoningPicker
        self.reasoning=ReasoningPicker(manager.store,provider,self)
        form.addRow('选择模型',model_row);form.addRow('推理强度',self.reasoning);outer.addLayout(form)
        self.model.currentIndexChanged.connect(self.update_reasoning)
        outer.addWidget(plain_label('优先读取/models接口；GLM未提供列表时使用有标注的官方文档预置目录，不能据此确认Key和模型权限。可输入名称筛选；实际调用权限及接口兼容性以服务商返回为准。',True))
        if provider=='glm':
            image_settings=QPushButton('GLM-Image 绘图设置');image_settings.setProperty('secondary',True)
            image_settings.clicked.connect(lambda:__import__('image_routes').ImageSettingsDialog(manager.store.database,'glm',self).exec());outer.addWidget(image_settings)
        self.status=plain_label('');outer.addWidget(self.status)
        self.progress=QProgressBar();self.progress.setRange(0,0);self.progress.hide();outer.addWidget(self.progress)
        buttons=QHBoxLayout();self.forget_button=QPushButton('删除已保存密钥');self.forget_button.setProperty('secondary',True);self.forget_button.clicked.connect(self.forget);buttons.addWidget(self.forget_button);buttons.addStretch()
        self.save_button=QPushButton('保存设置');self.save_button.clicked.connect(self.save);buttons.addWidget(self.save_button)
        close=QPushButton('关闭');close.setProperty('secondary',True);close.clicked.connect(self.reject);buttons.addWidget(close);outer.addLayout(buttons)
        self.populate(self.models,self.config['model'])
        if self.lab:self.forget_button.setText('清除当前模型选择')
        if self.config['encrypted_key']:self.refresh_models()
    def populate(self,models,selected):
        self.model.clear()
        for row in models:self.model.addItem(row['id'] if row['name']==row['id'] else row['name']+' · '+row['id'],row['id'])
        index=self.model.findData(selected)
        if index>=0:self.model.setCurrentIndex(index)
        self.update_reasoning()
        self.model.completer().setFilterMode(Qt.MatchFlag.MatchContains);self.model.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    def update_reasoning(self):self.reasoning.load(self.model.currentData() or '',self.models)
    def current_key(self):return self.manager.store.key(self.provider) if self.lab else self.key_edit.text().strip() or self.manager.store.key(self.provider)
    def busy(self,active):
        self.refresh_button.setEnabled(not active);self.save_button.setEnabled(not active);self.forget_button.setEnabled(not active);self.progress.setVisible(active)
    def refresh_models(self):
        if self.task:return
        try:
            from ai_store import validated_key
            key=validated_key(self.current_key());self.request_fingerprint=key_fingerprint(key);self.busy(True);self.status.setText('正在从官方接口获取当前可用模型……')
            if self.lab and self.request_fingerprint!=self.config['key_fingerprint']:raise ValueError('课题组账户已变化，请重新打开设置。')
            factory=self.manager.store.client_factory(self.provider,dict(self.config,reasoning_effort='default')) if self.lab or self.provider=='biomni' else AIClient
            def fetch():
                with factory(self.provider,key) as client:return client.models()
            self.task=self.manager.submit(self,fetch,'models_loaded','failed')
        except ValueError as error:self.failed(str(error))
    def models_loaded(self,models):
        selected=self.model.currentData() or self.config['model'];self.models=models;self.models_key=self.request_fingerprint
        self.populate(models,selected);self.task=None;self.busy(False);self.status.setText('已联网验证Biomni Lab API凭据；档位来自官方API规范，实际权限以执行结果为准。' if self.provider=='biomni' else f'已联网获取 {len(models)} 个文本模型；选择后点击保存。')
    def failed(self,message):self.task=None;self.busy(False);self.status.setText(message)
    def forget(self):
        self.manager.store.forget(self.provider);self.config=self.manager.store.config(self.provider);self.key_edit.clear();self.key_edit.setPlaceholderText('粘贴您的 API Key');self.models=[];self.models_key='';self.model.clear();self.status.setText('已清除模型选择。课题组账户未退出。' if self.lab else '已删除密钥并关闭此服务。本机调用统计保留。');self.manager.changed.emit()
    def save(self):
        if self.task:return
        try:
            current=self.manager.store.config(self.provider)
            if (current.get('route')=='lab')!=self.lab or (self.lab and current['key_fingerprint']!=self.config['key_fingerprint']):raise ValueError('调用通道或课题组账户已变化，请关闭并重新打开设置。')
            key=self.current_key()
            if key_fingerprint(key)!=self.models_key:raise ValueError('密钥已更改，请先点击“联网刷新模型”。')
            model=self.model.currentData()
            if self.model.currentIndex()<0 or self.model.currentText()!=self.model.itemText(self.model.currentIndex()):raise ValueError('请选择下拉列表中的实际模型。')
            self.manager.store.save(self.provider,key,model,self.models);self.reasoning.save();self.manager.changed.emit();self.accept()
        except ValueError as error:QMessageBox.warning(self,'无法保存',str(error))

class ModelCard(QFrame):
    def __init__(self,manager,provider,parent=None):
        super().__init__(parent);self.manager=manager;self.provider=provider;self.balance_task=None
        self.setObjectName('AIModelCard');layout=QVBoxLayout(self)
        header=QHBoxLayout();self.heading=QToolButton();self.heading.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed);self.heading.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon);self.heading.setArrowType(Qt.ArrowType.RightArrow);self.heading.setCheckable(True);self.heading.toggled.connect(self.expand);header.addWidget(self.heading,1)
        self.settings_button=QPushButton('设置');self.settings_button.setSizePolicy(QSizePolicy.Policy.Fixed,QSizePolicy.Policy.Fixed);self.settings_button.setProperty('secondary',True);self.settings_button.clicked.connect(self.settings);header.addWidget(self.settings_button);layout.addLayout(header)
        self.model_name=plain_label();layout.addWidget(self.model_name)
        self.state=plain_label();layout.addWidget(self.state)
        row=QHBoxLayout();self.toggle_button=QPushButton();self.toggle_button.clicked.connect(self.toggle);row.addWidget(self.toggle_button);row.addStretch();layout.addLayout(row)
        self.details=QWidget();details=QVBoxLayout(self.details);details.setContentsMargins(0,10,0,0)
        self.usage=UsageLabel();self.usage.setTextFormat(Qt.TextFormat.PlainText);self.usage.setWordWrap(True);self.usage.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);details.addWidget(self.usage)
        from ai_service import BALANCE_PROVIDERS
        self.balance=plain_label('点击刷新余额，可向 '+PROVIDERS[provider]+' 查询账户当前余额。' if provider in BALANCE_PROVIDERS else 'GLM 暂未接入可核实的普通 API Key 现金余额接口，请打开官方账户账单查看。' if provider=='glm' else '普通 OpenAI API Key 未在本页查询账户余额。请在 OpenAI 平台查看账单和完整账户用量。');details.addWidget(self.balance)
        if provider=='biomni':self.balance.setText('Biomni Lab未提供可核实的Token/余额查询接口；任务按平台规则计费。缺失用量不代表零消耗，请在Biomni Lab账户核对。')
        controls=QHBoxLayout()
        if provider in BALANCE_PROVIDERS:
            self.balance_button=QPushButton('刷新余额');self.balance_button.setProperty('secondary',True);self.balance_button.clicked.connect(self.refresh_balance);controls.addWidget(self.balance_button)
        dashboard=QPushButton('打开账户用量／账单');dashboard.setProperty('secondary',True)
        dashboard.clicked.connect(lambda:QDesktopServices.openUrl(QUrl({'deepseek':'https://platform.deepseek.com/usage','openai':'https://platform.openai.com/usage','kimi':'https://platform.kimi.com','glm':'https://bigmodel.cn','biomni':'https://biomni.phylo.bio'}.get(provider,'https://platform.openai.com/usage'))));controls.addWidget(dashboard);controls.addStretch();details.addLayout(controls)
        details.addWidget(plain_label('统计范围：本资料库、本软件、当前 Key 的已确认响应。包含不同模型的调用，不代表整个账户；超时或响应丢失可能已计费，但无法计入确认 Token。',True))
        layout.addWidget(self.details);self.details.hide();manager.changed.connect(self.reload);self.reload()
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:self.heading.setChecked(not self.heading.isChecked())
        super().mousePressEvent(event)
    def expand(self,expanded):
        self.details.setVisible(expanded);self.heading.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        if expanded:
            self.reload()
            if hasattr(self,'balance_button'):self.refresh_balance()
    def reload(self):
        config=self.manager.store.config(self.provider);enabled=bool(config['enabled']);usage=self.manager.store.usage(self.provider)
        if hasattr(self,'balance_fingerprint') and config['key_fingerprint']!=self.balance_fingerprint:
            self.balance.setText('密钥已更改，原账户余额已隐藏，请重新刷新。')
        self.heading.setText(PROVIDERS[self.provider]);self.heading.setStyleSheet('font-size:16pt;font-weight:700;color:'+('#B45309' if enabled else '#30251D')+';background:transparent;border:none;')
        self.model_name.setText('当前模型：'+(config['model'] or '尚未设置')+' · 推理强度：'+__import__('reasoning').description(config.get('reasoning_effort')))
        self.state.setText(('● 已开启 · 可用于 AI 功能' if enabled else '○ 未开启')+'\n'+('课题组授权通道 · PI 密钥保留在服务端' if config.get('route')=='lab' else 'API Key 已在本机加密保存' if config['encrypted_key'] else '请先设置您自己的 API Key'))
        self.toggle_button.setText('关闭模型' if enabled else '开启模型')
        self.setStyleSheet('QFrame#AIModelCard {background:'+('#FFF1D6' if enabled else '#FFFFFF')+';border:2px solid '+('#F59E0B' if enabled else '#E8D5C4')+';border-radius:14px;padding:12px;}')
        self.usage.setText(f"本软件调用次数：{usage['calls']:,}　未确认用量：{usage['unknown_calls']:,}\n输入 Token：{usage['input_tokens']:,}　输出 Token：{usage['output_tokens']:,}\n总 Token：{usage['total_tokens']:,}　其中缓存输入：{usage['cached_tokens']:,}\n上次调用：{local_time(usage['last_call'])}")
    def settings(self):
        if self.provider=='biomni':
            from biomni_ui import BiomniSettingsDialog
            BiomniSettingsDialog(self.manager,self).exec()
        else:AISettingsDialog(self.manager,self.provider,self).exec()
    def toggle(self):
        try:self.manager.store.toggle(self.provider);self.manager.changed.emit()
        except ValueError as error:QMessageBox.information(self,'请先设置模型',str(error))
    def refresh_balance(self):
        if self.balance_task:return
        try:
            config=self.manager.store.config(self.provider)
            lab=self.manager.store.lab_state()
            if config.get('route')=='lab' and lab and lab['identity']['role']!='pi':
                self.balance.setText('课题组余额由 PI 管理。您的累计用量请在学生卡片中查看。');return
            key=self.manager.store.key(self.provider);self.balance_fingerprint=key_fingerprint(key)
            if self.balance_fingerprint!=config['key_fingerprint']:raise ValueError('账户已变化，请重新查询。')
            self.balance_button.setEnabled(False);self.balance.setText('正在查询账户余额……');factory=self.manager.store.client_factory(self.provider,config)
            def fetch():
                with factory(self.provider,key) as client:return client.balance()
            self.balance_task=self.manager.submit(self,fetch,'balance_loaded','balance_failed')
        except ValueError as error:self.balance_failed(str(error))
    def balance_loaded(self,result):
        self.balance_task=None;self.balance_button.setEnabled(True)
        if self.manager.store.config(self.provider)['key_fingerprint']!=self.balance_fingerprint:self.balance.setText('密钥已更改，请重新刷新余额。');return
        lines=['账户额度状态：'+('可调用' if result.get('is_available') else '不可调用／余额不足')]
        for row in result['balance_infos']:
            lines.append(f"{row.get('currency','')} 可用余额：{row.get('total_balance','未返回')}　赠送：{row.get('granted_balance','未返回')}　充值余额：{row.get('topped_up_balance','未返回')}")
        lines.append('查询时间：'+datetime.now().strftime('%Y-%m-%d %H:%M:%S'));self.balance.setText('\n'.join(lines))
    def balance_failed(self,message):
        self.balance_task=None;self.balance_button.setEnabled(True);self.balance.setText(message)

class AIDeploymentPage(QWidget):
    def __init__(self,manager,parent=None):
        super().__init__(parent);self.manager=manager;outer=QVBoxLayout(self)
        title=QLabel('AI部署');title.setObjectName('PageTitle');outer.addWidget(title)
        outer.addWidget(plain_label('云端 API 接入 · 使用自己的 Key · 无需下载模型或独立显卡。点击模型名称展开用量与账户信息；设置后开启，即可在关键词方案使用 AI 推荐。',True))
        scroll=QScrollArea();scroll.setWidgetResizable(True);content=QWidget();layout=QVBoxLayout(content);self.cards={}
        from lab_ui import LabPanel
        self.lab_panel=LabPanel(manager);layout.addWidget(self.lab_panel)
        for provider in PROVIDERS:
            if provider=='codex':
                from codex_bridge import CodexCard
                self.cards[provider]=CodexCard(manager)
            else:self.cards[provider]=ModelCard(manager,provider)
            layout.addWidget(self.cards[provider])
        from image_routes import ImageModelCard
        self.image_cards=[ImageModelCard(manager,p) for p in ('ark','bailian')]
        for card in self.image_cards:layout.addWidget(card)
        layout.addStretch();scroll.setWidget(content);outer.addWidget(scroll,1)

class RecommendationDialog(QDialog):
    def __init__(self,manager,parent=None):
        super().__init__(parent);self.manager=manager;self.task=None;self.setWindowTitle('AI推荐关键词组');self.resize(900,780)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose,True)
        self.setWindowFlags(Qt.WindowType.Window|Qt.WindowType.WindowMinMaxButtonsHint|Qt.WindowType.WindowCloseButtonHint)
        root=QVBoxLayout(self);self.tabs=QTabWidget();root.addWidget(self.tabs)
        page=QWidget();self.tabs.addTab(page,'生成关键词方案');outer=QVBoxLayout(page)
        self.last_result=None;self.saved_signature=None;self._closing_approved=False
        from ai_workflow_ui import HistoryPane
        self.history_pane=HistoryPane(manager,'keywords',self.restore_history,self)
        self.tabs.addTab(self.history_pane,'已保存方案');self.tabs.currentChanged.connect(lambda:self.history_pane.reload())
        outer.addWidget(plain_label('输入课题，生成多种英文布尔检索方案。仅发送本窗口输入的课题，不上传文献库或 PDF；AI 建议需人工核对，不会自动添加方案或执行文献检索。'))
        row=QHBoxLayout();row.addWidget(QLabel('本次使用模型'));self.model=QComboBox();row.addWidget(self.model,1)
        outer.addLayout(row)
        outer.addWidget(plain_label('软件不设置输入或输出Token上限；实际容量与默认输出长度由模型服务决定。',True))
        self.topic=QPlainTextEdit();self.topic.setPlaceholderText('例如：IRAK1及其同家族分子的临床研究');self.topic.setMaximumHeight(140);outer.addWidget(self.topic)
        controls=QHBoxLayout();self.generate=QPushButton('生成英文关键词方案');self.generate.clicked.connect(self.request);controls.addWidget(self.generate)
        self.status=plain_label('按服务商规则计费；请求失败不自动重试。',True);controls.addWidget(self.status,1);outer.addLayout(controls)
        self.progress=QProgressBar();self.progress.setRange(0,0);self.progress.hide();outer.addWidget(self.progress)
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True);self.content=QWidget();self.results=QVBoxLayout(self.content);self.results.addStretch();self.scroll.setWidget(self.content);outer.addWidget(self.scroll,1)
        close=QPushButton('关闭');close.setProperty('secondary',True);close.clicked.connect(self.reject);bottom=QHBoxLayout();bottom.addStretch();bottom.addWidget(close);outer.addLayout(bottom)
        manager.changed.connect(self.update_models);self.update_models()
    def update_models(self):
        if self.task:return
        selected=self.model.currentData();self.model.clear()
        for provider in PROVIDERS:
            config=self.manager.store.config(provider)
            if config['enabled'] and config['model']:self.model.addItem(PROVIDERS[provider]+' · '+config['model'],provider)
        index=self.model.findData(selected)
        if index>=0:self.model.setCurrentIndex(index)
        self.generate.setEnabled(self.model.count()>0)
        if self.model.count()==0:self.status.setText('请先前往“AI部署”设置并开启至少一个模型。')
    def reset_output_limit(self):
        pass
    def save_current(self):
        inputs=dict(topic=self.topic.toPlainText())
        identifier=self.manager.repository.save_history('keywords',self.topic.toPlainText()[:80] or '关键词建议',inputs,self.last_result or {})
        self.saved_signature=self.current_signature();self.history_pane.reload();return identifier
    def current_signature(self):return json.dumps([self.topic.toPlainText(),self.last_result],sort_keys=True,ensure_ascii=False)
    def confirm_save(self):
        if self._closing_approved:return True
        if (self.topic.toPlainText().strip() or self.last_result) and self.current_signature()!=self.saved_signature:
            answer=QMessageBox.question(self,'保存关键词方案','是否保存输入内容及 AI 提供的关键词方案，以便下次离线查看？',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No|QMessageBox.StandardButton.Cancel)
            if answer==QMessageBox.StandardButton.Cancel:return False
            if answer==QMessageBox.StandardButton.Yes:self.save_current()
        self._closing_approved=True;return True
    def restore_history(self,item):
        self.topic.setPlainText(item['inputs'].get('topic',''))
        if item['result']:self.loaded(item['result'])
        else:self.clear_results();self.last_result=None
        self.saved_signature=self.current_signature();self.tabs.setCurrentIndex(0)
    def reject(self):
        if self.task:self.status.setText('AI 请求尚在处理，可最小化本窗口；返回后可关闭。');return
        if self.confirm_save():super().reject()
    def closeEvent(self,event):
        if self.task:self.status.setText('AI 请求尚在处理，可最小化本窗口；返回后可关闭。');event.ignore();return
        if not self.confirm_save():event.ignore();return
        super().closeEvent(event)
    def request(self):
        if self.task:return
        provider=self.model.currentData();topic=self.topic.toPlainText().strip()
        if not provider or len(topic)<3:QMessageBox.information(self,'请完善输入','请选择已开启模型，并完整描述课题（至少3个字符）。');return
        self.generate.setEnabled(False);self.model.setEnabled(False);self.progress.show();self.status.setText('正在生成，可继续浏览软件；请勿重复发送……')
        self.task=self.manager.submit(self,lambda:generate_recommendations(self.manager.store,provider,topic),'loaded','failed')
    def clear_results(self):
        while self.results.count():
            item=self.results.takeAt(0)
            if item.widget():item.widget().deleteLater()
    def loaded(self,result):
        self.last_result=result
        self.task=None;self.update_models();self.model.setEnabled(True);self.progress.hide();self.clear_results()
        self.results.addWidget(plain_label('来自 '+PROVIDERS.get(result['provider'],result['provider'])+' · '+result['model']+'；'+__import__('reasoning').result_label(result)+'；未经实际检索验证。',True))
        if result['notes']:self.results.addWidget(plain_label(result['notes']))
        for index,suggestion in enumerate(result['suggestions'],1):
            card=QFrame();card.setObjectName('AIRecommendation');card.setStyleSheet('QFrame#AIRecommendation {background:#FFFFFF;border:1px solid #EDCCAD;border-radius:10px;padding:10px;}');layout=QVBoxLayout(card)
            heading=plain_label(str(index)+' · '+suggestion['title']);heading.setStyleSheet('font-weight:700;color:#B45309;');layout.addWidget(heading)
            query=QPlainTextEdit();query.setReadOnly(True);query.setPlainText(suggestion['query']);query.setMaximumHeight(105);layout.addWidget(query)
            layout.addWidget(plain_label(suggestion['explanation']))
            if suggestion['verification_notes']:layout.addWidget(plain_label('需核对：'+suggestion['verification_notes'],True))
            copy=QPushButton('复制关键词方案');copy.clicked.connect(lambda checked=False,text=suggestion['query']:self.copy(text));layout.addWidget(copy,0,Qt.AlignmentFlag.AlignRight);self.results.addWidget(card)
        if not result['suggestions']:
            self.results.addWidget(plain_label('未得到可用的结构化方案。下方为模型原始文本，仅供人工检查。'))
            raw=QPlainTextEdit();raw.setReadOnly(True);raw.setPlainText(result['raw']);raw.setMinimumHeight(220);self.results.addWidget(raw)
        self.results.addStretch();usage=result['usage']
        self.status.setText(f"生成完成 · {len(result['suggestions'])} 个方案 · 本次 Token：{usage.get('total_tokens', '接口未返回')}。可复制后粘贴到“添加关键词组”。")
    def failed(self,message):
        self.task=None;self.update_models();self.model.setEnabled(True);self.progress.hide();self.status.setText(message);QMessageBox.warning(self,'AI生成未完成',message)
    def copy(self,text):
        QApplication.clipboard().setText(text);QMessageBox.information(self,'复制成功','复制成功，可以粘贴到“添加关键词组”的关键词或布尔表达式中。')
