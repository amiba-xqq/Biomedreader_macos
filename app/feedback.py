"""User-authored feedback only; no credentials, library contents or automatic uploads."""
import json,platform,uuid
from urllib.parse import urlencode
import requests
from pathlib import Path
from urllib3.util.retry import Retry
from PySide6.QtCore import QObject,QRunnable,Signal,QThreadPool,QUrl,Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget,QVBoxLayout,QLabel,QComboBox,QLineEdit,QPlainTextEdit,QPushButton,QCheckBox,QMessageBox,QStackedWidget,QGroupBox,QHBoxLayout
from software_update import session_for_updates
from version import APP_VERSION

FEEDBACK_URL='https://biomedreader.cn/api/feedback'
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

def submit_feedback(session,data):
    # Feedback is a write, unlike the public update downloads which share this
    # TLS/proxy setup. Do not silently retry a POST or wait on Retry-After.
    session.get_adapter(FEEDBACK_URL).max_retries=Retry(total=0,redirect=0)
    # requests' default JSON escaping can turn 6000 Chinese characters into a
    # >32 KB request even though the form and server both allow 6000 characters.
    payload=json.dumps({**data,'website':''},ensure_ascii=False,separators=(',',':')).encode('utf-8')
    response=session.post(FEEDBACK_URL,data=payload,headers={'Content-Type':'application/json; charset=utf-8'},timeout=(10,30),allow_redirects=False)
    try:
        if response.status_code!=200:
            messages={
                400:'反馈内容未通过服务校验，请检查标题、正文和联系方式。',
                403:'反馈请求被服务或网络访问规则拒绝，请检查网络或代理。',
                404:'反馈服务入口暂时不可用，请稍后重试或使用 GitHub Issue。',
                409:'反馈编号发生冲突，请修改内容后重新提交。',
                413:'反馈内容传输后过长，请精简内容后重试。',
                429:'提交过于频繁，请稍后再试。',
                503:'反馈服务尚未就绪，请稍后重试。',
            }
            message=messages.get(response.status_code,'反馈服务暂时无法确认提交，请稍后重试。')
            raise ValueError(f'{message}（HTTP {response.status_code}）填写内容已保留。')
        try:result=response.json()
        except ValueError:raise ValueError('反馈服务没有返回有效确认，内容已保留，请稍后重试。') from None
        if not isinstance(result,dict) or result.get('ok') is not True or result.get('id')!=data['id']:
            raise ValueError('服务未确认保存本次反馈，填写内容已保留；重试会使用相同编号。')
        return result
    finally:response.close()

def run_feedback_self_test(destination):
    """Check packaged HTTPS and live routing without saving or emailing feedback."""
    report={'url':FEEDBACK_URL,'version':APP_VERSION,'saved_feedback':False,'sent_email':False}
    try:
        with session_for_updates() as session:
            session.get_adapter(FEEDBACK_URL).max_retries=Retry(total=0,redirect=0)
            # The nonempty honeypot is always rejected before D1 writes or mail.
            response=session.post(FEEDBACK_URL,json={'website':'connection-check'},timeout=(10,20),allow_redirects=False)
            try:
                report['http_status']=response.status_code
                result=response.json() if response.headers.get('Content-Type','').startswith('application/json') else None
                if response.status_code!=400 or not isinstance(result,dict) or result.get('ok') is not False:
                    raise ValueError('Feedback routing/readiness check failed')
            finally:response.close()
        report['passed']=True
    except Exception as error:
        report.update(passed=False,error=type(error).__name__)
    path=Path(destination);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if report['passed'] else 1

class FeedbackTask(QRunnable):
    def __init__(self,data):super().__init__();self.data=data;self.signals=FeedbackSignals()
    def run(self):
        try:
            with session_for_updates() as session:
                result=submit_feedback(session,self.data)
                self.signals.finished.emit(result)
        except ValueError as error:
            self.signals.failed.emit(str(error))
        except requests.exceptions.SSLError:
            self.signals.failed.emit('反馈 HTTPS 证书验证失败，请检查系统时间、代理或证书配置。填写内容已保留。')
        except requests.exceptions.ProxyError:
            self.signals.failed.emit('反馈代理连接失败，请检查代理设置。填写内容已保留，可使用相同编号重试。')
        except requests.exceptions.Timeout:
            self.signals.failed.emit('反馈连接或确认超时，填写内容已保留；可能已提交，请使用相同编号重试以避免重复。')
        except requests.exceptions.ConnectionError:
            self.signals.failed.emit('无法连接反馈服务 biomedreader.cn，请检查网络或代理。填写内容已保留。')
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
