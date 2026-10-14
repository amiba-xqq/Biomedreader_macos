"""A normal, visible Qt browser for website search; users handle any site verification."""
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit,unquote,parse_qs
from PySide6.QtCore import QObject, Signal, QTimer, QUrl, Qt


class WebsiteBrowser(QObject):
    requested = Signal(object)

    def __init__(self,data_directory,parent=None):
        super().__init__(parent)
        self.data_directory=Path(data_directory)
        self.dialog=None;self.active=None;self.profile=None
        self.stopped=False;self.page_ready=False;self.loaded_at=0
        self.requested.connect(self.begin)

    def get(self,url,cancelled=lambda:False):
        if self.stopped or cancelled():
            raise InterruptedError('检索已取消，保留上次缓存。')
        request=dict(url=url,event=threading.Event(),html=None,error=None,cancelled=cancelled,started=time.monotonic(),timeout=240)
        self.requested.emit(request)
        while not request['event'].wait(0.1):
            if cancelled():
                request['error']='检索已取消，保留上次缓存。';request['event'].set()
                raise InterruptedError('检索已取消，保留上次缓存。')
            if time.monotonic()-request['started']>request.get('timeout',240):
                request['error']='官网页面或验证等待超时，已读取进度及旧缓存保留；稍后再次刷新可继续。'
                request['event'].set()
        if request['error']:
            if request['error'].startswith('检索已取消'):
                raise InterruptedError(request['error'])
            raise ValueError(request['error'])
        return request['html']

    def begin(self,request):
        from biorxiv_website import safe_page
        if not safe_page(request['url']):
            request['error']='不是受支持的 bioRxiv 官网搜索地址。';request['event'].set();return
        if self.active and not self.active['event'].is_set():
            request['error']='官网浏览器仍有请求在处理，请稍后重试。';request['event'].set();return
        if request['event'].is_set() or request['cancelled']():
            request['error']='检索已取消，保留上次缓存。';request['event'].set();return
        self.active=request
        request.setdefault('timeout',240)
        request['cooldown_until']=0
        self.page_ready=False
        try:
            if self.dialog is None:
                # Lazy import keeps the API-only workflow independent of browser startup/GPU.
                from PySide6.QtWebEngineCore import QWebEngineProfile,QWebEnginePage,QWebEngineSettings
                from PySide6.QtWebEngineWidgets import QWebEngineView
                from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QPushButton,QLineEdit,QHBoxLayout
                self.dialog=QDialog(self.parent())
                self.dialog.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowMinimizeButtonHint |
                                           Qt.WindowType.WindowMaximizeButtonHint | Qt.WindowType.WindowCloseButtonHint)
                self.dialog.setWindowTitle('预印本官网搜索 · bioRxiv / medRxiv')
                self.dialog.resize(1100,800)
                layout=QVBoxLayout(self.dialog)
                note=QLabel('正在读取官网搜索结果。可最小化窗口，检索会继续；若网站要求验证，请恢复窗口后手动完成。关闭此窗口会取消官网检索。')
                note.setWordWrap(True);layout.addWidget(note)
                self.status=QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
                self.address=QLineEdit();self.address.setReadOnly(True);layout.addWidget(self.address)
                self.view=QWebEngineView(self.dialog)
                self.profile=QWebEngineProfile('BioRxivWebsite',self.dialog)
                self.profile.setPersistentStoragePath(str(self.data_directory/'website_session'))
                self.profile.setCachePath(str(self.data_directory/'website_cache'))
                page=QWebEnginePage(self.profile,self.view)
                page.settings().setAttribute(QWebEngineSettings.WebAttribute.ForceDarkMode,False)
                self.view.setPage(page)
                layout.addWidget(self.view,1)
                check=QPushButton('验证已完成 · 继续读取')
                check.clicked.connect(self.inspect)
                controls=QHBoxLayout();controls.addWidget(check)
                reload_button=QPushButton('重新加载此页');reload_button.clicked.connect(self.reload_requested)
                self.reload_button=reload_button
                controls.addWidget(reload_button)
                cancel=QPushButton('取消检索');cancel.clicked.connect(self.dialog.reject)
                controls.addWidget(cancel);layout.addLayout(controls)
                self.view.loadFinished.connect(self.loaded)
                self.view.loadStarted.connect(self.loading)
                self.dialog.rejected.connect(self.closed)
                self.timer=QTimer(self.dialog);self.timer.setInterval(1500);self.timer.timeout.connect(self.inspect)
            self.status.setText('加载官网搜索页面……')
            self.reload_button.setEnabled(True)
            self.address.setText(request['url'])
            # Paging must not restore a window the reader has minimized.
            if not self.dialog.isVisible():self.dialog.showNormal()
            self.view.load(QUrl(request['url']))
            self.timer.start()
        except Exception as error:
            self.finish(error=f'官网浏览器启动失败：{error}')

    def loaded(self,ok):
        self.page_ready=True;self.loaded_at=time.monotonic()
        if not ok and self.active and not self.active['event'].is_set():
            self.status.setText('页面尚未成功加载；可在窗口中检查网络、完成网站提示后继续。')
        self.inspect()

    def loading(self):
        self.page_ready=False

    def inspect(self):
        request=self.active
        if not request:return
        if request['event'].is_set():
            self.timer.stop();return
        if request['cancelled']():
            self.finish(error='检索已取消，保留上次缓存。');return
        if time.monotonic()-request['started']>request.get('timeout',240):
            self.finish(error='官网加载或验证等待超时，已读取进度及旧缓存保留；稍后再次刷新可继续。');return
        if not self.page_ready:return
        def receive(text):
            if self.active is not request or request['event'].is_set():return
            from biorxiv_website import challenge_page,rate_limit_page,parse_page,safe_page,validate_search_context
            current=self.view.url().toString()
            if rate_limit_page(text):
                # Allow the site's own countdown/automatic reload to complete.
                # Never repeatedly reload a rate-limit page or bypass its checks.
                request['timeout']=900
                request['cooldown_until']=max(request['cooldown_until'],time.monotonic()+15)
                self.reload_button.setEnabled(False)
                self.status.setText('官网提示请求过多，正在等待网站倒计时及自动恢复（本页最多等待15分钟）。已读取分页已保存，可最小化；请勿连续重新加载。')
                return
            self.reload_button.setEnabled(time.monotonic()>=request['cooldown_until'])
            if challenge_page(text):
                request['timeout']=900
                self.status.setText('官网要求访问验证，请手动完成。软件不会自动处理验证码或切换到 API 扫描。');return
            if not safe_page(current):
                return
            actual,expected=urlsplit(current),urlsplit(request['url'])
            if actual.hostname!=expected.hostname or unquote(actual.path).rstrip('/')!=unquote(expected.path).rstrip('/') or parse_qs(actual.query).get('page',['0'])!=parse_qs(expected.query).get('page',['0']):
                self.status.setText('当前页面不是待读取的结果页。请点击“重新加载此页”返回该页后继续。')
                return
            try:
                parse_page(text,current)
                validate_search_context(text,current)
            except ValueError as error:
                self.status.setText(str(error))
                if time.monotonic()-self.loaded_at>30:self.finish(error=str(error))
                return
            self.finish(text)
        self.view.page().toHtml(receive)

    def reload_requested(self):
        if self.active and not self.active['event'].is_set():
            if time.monotonic()<self.active.get('cooldown_until',0):return
            self.view.load(QUrl(self.active['url']))

    def finish(self,text=None,error=None):
        if self.active and not self.active['event'].is_set():
            self.active['html']=text;self.active['error']=error;self.active['event'].set()
        if self.dialog:
            self.timer.stop()
            self.status.setText(error or '本页已读取，正在整理或继续下一页……')

    def closed(self):
        self.stopped=True
        self.finish(error='检索已取消，保留上次缓存。')

    def end_search(self):
        if self.dialog:
            if self.active and not self.active['event'].is_set():self.finish(error='检索已取消，保留上次缓存。')
            self.timer.stop();self.view.stop();self.dialog.hide()

    def shutdown(self):
        self.stopped=True
        self.finish(error='检索已取消，保留上次缓存。')
        self.end_search()
