"""Read a preprint search page only after the user clicks Read."""
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from PySide6.QtCore import QObject, QTimer, QUrl, Qt, Signal

NAVIGATION_GAP_SECONDS = 12
READ_TIMEOUT_SECONDS = 900
CANCELLED = '检索已取消，保留上次缓存。'


class WebsiteBrowser(QObject):
    requested = Signal(object)
    expired = Signal(object)

    def __init__(self, data_directory, parent=None):
        super().__init__(parent)
        self.data_directory = Path(data_directory)
        self.dialog = None
        self.active = None
        self.stopped = False
        self.page_ready = False
        self.navigation = 0
        self.last_navigation = None
        self.navigation_timer = QTimer(self)
        self.navigation_timer.setSingleShot(True)
        self.navigation_timer.timeout.connect(self.navigate)
        self.requested.connect(self.begin)
        self.expired.connect(self.expire)

    def get(self, url, cancelled=lambda: False):
        if self.stopped or cancelled():
            raise InterruptedError(CANCELLED)
        request = dict(url=url, event=threading.Event(), html=None, error=None, cancelled=cancelled)
        deadline = time.monotonic() + READ_TIMEOUT_SECONDS
        self.requested.emit(request)
        while not request['event'].wait(0.1):
            if cancelled():
                request['event'].set()
                self.expired.emit(request)
                raise InterruptedError(CANCELLED)
            if time.monotonic() >= deadline:
                request['event'].set()
                self.expired.emit(request)
                raise ValueError('等待读取官网搜索页超时，已读取进度及旧缓存保留。')
        if request['error'] == CANCELLED:
            raise InterruptedError(CANCELLED)
        if request['error']:
            raise ValueError(request['error'])
        return request['html']

    def begin(self, request):
        from biorxiv_website import safe_page

        if request['event'].is_set():
            return
        if self.stopped or request['cancelled']():
            request.update(error=CANCELLED)
            request['event'].set()
            return
        if not safe_page(request['url']):
            request.update(error='不是受支持的 bioRxiv 或 medRxiv 官网搜索地址。')
            request['event'].set()
            return
        if self.active:
            if not self.active['event'].is_set():
                request.update(error='官网浏览器仍有请求在处理，请稍后重试。')
                request['event'].set()
                return
            self.expire(self.active)
        self.active = request
        self.page_ready = False
        try:
            if self.dialog is None:
                self.create_window()
            self.status.setText('加载官网搜索页面……')
            self.read_button.setEnabled(False)
            self.reload_button.setEnabled(False)
            self.address.setText(request['url'])
            self.dialog.showNormal()
            delay = self.navigation_delay()
            if delay > 0:
                self.navigation_timer.start(int(delay * 1000) + 1)
            else:
                self.navigate()
        except Exception as error:
            self.finish(error=f'官网浏览器启动失败：{error}')

    def create_window(self):
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

        self.dialog = QDialog(self.parent())
        self.dialog.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowMinMaxButtonsHint |
                                   Qt.WindowType.WindowCloseButtonHint)
        self.dialog.setWindowTitle('预印本官网搜索 · bioRxiv / medRxiv')
        self.dialog.resize(1100, 800)
        layout = QVBoxLayout(self.dialog)
        note = QLabel('如网站要求验证，请手动完成。搜索结果显示后，点击“读取当前页”。有多页时，每页都需点击读取。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.address = QLineEdit()
        self.address.setReadOnly(True)
        layout.addWidget(self.address)
        self.view = QWebEngineView(self.dialog)
        self.profile = QWebEngineProfile('BioRxivWebsite', self.dialog)
        self.profile.setPersistentStoragePath(str(self.data_directory / 'website_session'))
        self.profile.setCachePath(str(self.data_directory / 'website_cache'))
        page = QWebEnginePage(self.profile, self.view)
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.ForceDarkMode, False)
        self.view.setPage(page)
        layout.addWidget(self.view, 1)

        controls = QHBoxLayout()
        self.read_button = QPushButton('读取当前页')
        self.read_button.clicked.connect(self.read_current_page)
        controls.addWidget(self.read_button)
        self.reload_button = QPushButton('重新加载此页')
        self.reload_button.clicked.connect(self.reload_requested)
        controls.addWidget(self.reload_button)
        cancel = QPushButton('取消检索')
        cancel.clicked.connect(self.dialog.reject)
        controls.addWidget(cancel)
        layout.addLayout(controls)
        self.view.loadStarted.connect(self.loading)
        self.view.loadFinished.connect(self.loaded)
        self.dialog.rejected.connect(self.closed)

    def loaded(self, ok):
        request = self.active
        if not request or request['event'].is_set() or self.navigation_timer.isActive():
            return
        self.page_ready = True
        self.read_button.setEnabled(not request.get('reading'))
        self.reload_button.setEnabled(not request.get('blocked'))
        self.status.setText('请确认页面显示搜索结果后点击读取。' if ok else
                            '页面未能正常加载。请在窗口中检查网站提示。')

    def loading(self):
        self.page_ready = False
        self.navigation += 1
        self.read_button.setEnabled(False)
        self.reload_button.setEnabled(False)
        if self.active:
            self.active['blocked'] = False

    def navigation_delay(self):
        if self.last_navigation is None:
            return 0
        return max(0, NAVIGATION_GAP_SECONDS - (time.monotonic() - self.last_navigation))

    def navigate(self):
        if not self.active or self.active['event'].is_set():
            return
        if self.active['cancelled']():
            self.finish(error=CANCELLED)
            return
        self.last_navigation = time.monotonic()
        self.loading()
        self.view.load(QUrl(self.active['url']))

    def read_current_page(self):
        request = self.active
        if not request or request['event'].is_set() or request.get('reading') or not self.page_ready:
            return
        if request['cancelled']():
            self.finish(error=CANCELLED)
            return
        url = self.view.url().toString()
        navigation = self.navigation
        request['reading'] = True
        self.read_button.setEnabled(False)

        def receive(html):
            if self.active is not request or request['event'].is_set():
                return
            request['reading'] = False
            self.read_button.setEnabled(self.page_ready)
            if not self.page_ready or navigation != self.navigation or url != self.view.url().toString():
                return
            self.accept_page(request, url, html)

        self.view.page().toHtml(receive)

    def accept_page(self, request, url, html):
        from biorxiv_website import challenge_page, parse_page, rate_limit_page, safe_page, validate_search_context

        if rate_limit_page(html):
            request['blocked'] = True
            self.reload_button.setEnabled(False)
            self.status.setText('官网正在限制请求频率。请等待官网恢复，看到搜索结果后再点击读取。')
            return
        if challenge_page(html):
            request['blocked'] = True
            self.reload_button.setEnabled(False)
            self.status.setText('请在网页中手动完成验证，看到搜索结果后再点击读取。')
            return
        request['blocked'] = False
        self.reload_button.setEnabled(True)
        actual, expected = urlsplit(url), urlsplit(request['url'])
        if (not safe_page(url) or actual.hostname != expected.hostname or
                unquote(actual.path).rstrip('/') != unquote(expected.path).rstrip('/') or
                parse_qs(actual.query).get('page', ['0']) != parse_qs(expected.query).get('page', ['0'])):
            self.status.setText('当前页面不是待读取的结果页。请返回搜索页后再点击读取。')
            return
        try:
            parse_page(html, url)
            validate_search_context(html, url)
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.finish(html)

    def reload_requested(self):
        request = self.active
        if not request or request['event'].is_set() or not self.page_ready or request.get('blocked'):
            return
        delay = self.navigation_delay()
        if delay > 0:
            self.status.setText(f'请等待约 {int(delay) + 1} 秒后再重新加载。')
            return
        self.navigate()

    def finish(self, html=None, error=None):
        self.navigation_timer.stop()
        request, self.active = self.active, None
        self.page_ready = False
        if request and not request['event'].is_set():
            request.update(html=html, error=error)
            request['event'].set()
        if self.dialog:
            self.dialog.hide()

    def expire(self, request):
        if self.active is request:
            self.finish()
            if self.dialog:
                self.view.stop()

    def closed(self):
        self.stopped = True
        self.end_search()

    def end_search(self):
        self.finish(error=CANCELLED)
        if self.dialog:
            self.view.stop()

    def shutdown(self):
        self.closed()
