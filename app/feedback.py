"""User-authored feedback only; no credentials, library contents or automatic uploads."""
import json,platform,uuid
from urllib.parse import urlencode
from PySide6.QtCore import QObject,QRunnable,Signal,QThreadPool,QUrl,Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget,QVBoxLayout,QLabel,QComboBox,QLineEdit,QPlainTextEdit,QPushButton,QCheckBox,QMessageBox,QStackedWidget,QGroupBox,QHBoxLayout
from software_update import session_for_updates
from version import APP_VERSION

FEEDBACK_URL='https://biomedreader.1310305204.workers.dev/api/feedback'
ISSUE_URL='https://github.com/amiba-xqq/Biomedreader/issues/new'

def validated_feedback(kind,title,body,contact=''):
    title=title.strip();body=body.strip();contact=contact.strip()
    if not 3<=len(title)<=120:raise ValueError('标题需要 3–120 个字符。')
    if not 10<=len(body)<=6000:raise ValueError('反馈内容需要 10–6000 个字符。')
    if len(contact)>200 or '\n' in contact or '\r' in contact:raise ValueError('联系方式最多 200 个字符，不能换行。')
    return dict(id=str(uuid.uuid4()),kind=kind,title=title,body=body,contact=contact,version=APP_VERSION,system=platform.system()+' '+platform.release())

def issue_link(data):
    # Contact information is intentionally never placed into the public URL.
    body=f"### 类型\n{data['kind']}\n\n### 描述\n{data['body']}\n\n### 环境\nBiomed Reader {data['version']} · {data['system']}\n"
    url=ISSUE_URL+'?'+urlencode(dict(title=data['title'],body=body))
    if len(url)>7500:raise ValueError('预填 GitHub 链接内容过长，请精简描述后重试（完整内容可改用私密表单）。')
    return url

class FeedbackSignals(QObject):
    finished=Signal(object);failed=Signal(str)

class FeedbackTask(QRunnable):
    def __init__(self,data):super().__init__();self.data=data;self.signals=FeedbackSignals()
    def run(self):
        try:
            with session_for_updates() as session:
                # Stable request IDs make transport retries and manual retries idempotent.
                response=session.post(FEEDBACK_URL,json={**self.data,'website':''},timeout=(10,30),allow_redirects=False)
                if response.status_code==429:raise ValueError('提交过于频繁，请稍后再试。填写内容已保留。')
                if response.status_code!=200:raise ValueError('反馈服务暂时不可用，填写内容已保留，请稍后重试或使用 GitHub Issue。')
                result=response.json()
                if result.get('ok') is not True or not result.get('id'):raise ValueError('服务未确认保存反馈，填写内容已保留。')
                self.signals.finished.emit(result)
        except Exception:
            self.signals.failed.emit('未能确认反馈提交。内容已保留，可重试；相同编号不会重复保存。也可改用 GitHub Issue。')

class FeedbackPage(QWidget):
    def __init__(self):
        super().__init__();self.task=None;self.draft=None;root=QVBoxLayout(self);self.stack=QStackedWidget();root.addWidget(self.stack)
        cards=QWidget();cards_layout=QVBoxLayout(cards);self.stack.addWidget(cards)
        heading=QLabel('反馈');heading.setObjectName('PageTitle');cards_layout.addWidget(heading)
        from card_board import CardBoard
        self.feedback_board=CardBoard(columns=4)
        for title,text,index in [('反馈发送到开发者邮箱','私密表单 · 无需 GitHub 账号\n适合不希望公开的问题',1),('反馈上传到 GitHub Issue','公开讨论 · Bug 与功能建议\n打开预填网页后自行确认提交',0)]:
            card=self.feedback_board.add_card(str(index),title,text,lambda value:self.open_form(int(value)));card.setToolTip('点击填写反馈');card.mouseMoveEvent=lambda event:None
        cards_layout.addWidget(self.feedback_board,1);form=QWidget();self.stack.addWidget(form);layout=QVBoxLayout(form)
        back=QPushButton('← 返回反馈方式');back.setProperty('secondary',True);back.clicked.connect(lambda:self.stack.setCurrentIndex(0));layout.addWidget(back,0,Qt.AlignmentFlag.AlignLeft)
        title=QLabel('反馈');title.setObjectName('PageTitle');layout.addWidget(title)
        info=QLabel('帮助改进刊禾。请选择反馈渠道，填写问题或建议；请不要填写密码、密钥、患者信息或未公开的研究资料。');info.setWordWrap(True);layout.addWidget(info)
        self.channel=QComboBox();self.channel.addItems(['反馈上传到 GitHub Issue','反馈发送到开发者邮箱']);layout.addWidget(self.channel)
        self.scope=QLabel();self.scope.setWordWrap(True);layout.addWidget(self.scope)
        self.kind=QComboBox();self.kind.addItems(['Bug 报告','功能建议','使用问题','其它反馈']);layout.addWidget(self.kind)
        self.title=QLineEdit();self.title.setMaxLength(120);self.title.setPlaceholderText('标题（3–120 字）');layout.addWidget(self.title)
        self.body=QPlainTextEdit();self.body.setPlaceholderText('内容（10–6000 字）：操作步骤、预期结果、实际结果。不会自动上传日志、PDF、截图或文献库。');layout.addWidget(self.body,1)
        self.contact=QLineEdit();self.contact.setMaxLength(200);self.contact.setPlaceholderText('您的联系方式（可不填；仅私密表单发送，用于开发者回复）');layout.addWidget(self.contact)
        self.consent=QCheckBox('我已检查内容，同意将所填反馈及软件版本、基本系统信息发送到开发者反馈服务。');layout.addWidget(self.consent)
        self.submit=QPushButton();self.submit.clicked.connect(self.send);layout.addWidget(self.submit,0,Qt.AlignmentFlag.AlignLeft)
        self.status=QLabel();self.status.setWordWrap(True);self.status.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(self.status)
        self.channel.setCurrentIndex(1)
        self.channel.currentIndexChanged.connect(self.change_channel);self.change_channel()
        from compact_fields import CompactFields
        self.compact_fields=CompactFields(self,[self.channel,self.kind])
    def open_form(self,index):self.channel.setCurrentIndex(index);self.stack.setCurrentIndex(1)
    def change_channel(self):
        private=self.channel.currentIndex()==1;self.contact.setVisible(private);self.consent.setVisible(private)
        self.scope.setText('适合不使用 GitHub 的用户，或不希望公开的问题。提交后保存到开发者 Cloudflare D1，并通知开发者；联系方式可不填。不会公开发布，也不会自动附带文件或文献数据。' if private else '适合可公开讨论的 Bug、功能建议和经验交流。软件只打开预填的 GitHub Issue 页面，您需登录 GitHub 并自行确认提交；不要在公开内容中放入私人信息。')
        self.submit.setText('发送私密反馈' if private else '打开预填 GitHub Issue 页面')
    def send(self):
        if self.task:return
        try:
            data=validated_feedback(self.kind.currentText(),self.title.text(),self.body.toPlainText(),self.contact.text() if self.channel.currentIndex()==1 else '')
            if self.channel.currentIndex()==0:
                url=issue_link(data)
                if not QDesktopServices.openUrl(QUrl(url)):raise ValueError('无法打开浏览器，请检查默认浏览器设置。')
                self.status.setText('已打开 GitHub 预填页面，请在浏览器检查并确认提交；软件未代您提交。');return
            if not self.consent.isChecked():raise ValueError('请先勾选同意发送反馈内容。')
            if self.draft and {k:v for k,v in data.items() if k!='id'}=={k:v for k,v in self.draft.items() if k!='id'}:data=self.draft
            self.draft=data;self.task=FeedbackTask(data);self.task.signals.finished.connect(self.done);self.task.signals.failed.connect(self.failed)
            self.submit.setEnabled(False);self.channel.setEnabled(False);self.status.setText('正在发送，请稍候……');QThreadPool.globalInstance().start(self.task)
        except ValueError as error:QMessageBox.warning(self,'无法发送',str(error))
    def done(self,result):
        self.task=None;self.submit.setEnabled(True);self.channel.setEnabled(True)
        text='反馈已保存。编号：'+result['id']
        text+='。邮件通知已提交。' if result.get('notified') else '。邮件通知正在重试，反馈已安全保存。'
        self.status.setText(text);QMessageBox.information(self,'反馈已保存',text)
        self.draft=None;self.title.clear();self.body.clear();self.consent.setChecked(False)
    def failed(self,message):
        self.task=None;self.submit.setEnabled(True);self.channel.setEnabled(True);self.status.setText(message);QMessageBox.warning(self,'反馈未确认',message)
