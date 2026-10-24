"""Sequential refresh using the same source checkpoints as an individual refresh."""
from PySide6.QtCore import QObject, Signal, QTimer


class BatchRefresh(QObject):
    changed = Signal()

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.pending = []
        self.task = None
        self.active = False
        self.cancelled = False
        self.completed = 0
        self.failures = []

    def start(self, identifiers):
        if self.active or self.window.results.current_task is not None:
            return False
        self.pending = list(dict.fromkeys(identifiers))
        if not self.pending:
            return False
        self.active = True
        self.cancelled = False
        self.completed = 0
        self.failures = []
        self.total = len(self.pending)
        self.window.results.refresh_button.setEnabled(False)
        self.changed.emit()
        QTimer.singleShot(0, self.advance)
        return True

    def cancel(self):
        self.cancelled = True
        self.pending.clear()
        if self.task:
            self.task.cancelled = True
            self.window.results.website.shutdown()
        else:
            self.finish()
        self.changed.emit()

    def advance(self):
        from ui import SearchTask
        if self.cancelled or not self.pending:
            self.finish()
            return
        identifier = self.pending.pop(0)
        profile = self.window.database.get_profile(identifier)
        if not profile:
            QTimer.singleShot(0, self.advance)
            return
        browser = self.window.results.website
        browser.stopped = False
        # Never force a full search: refresh_search itself decides independently
        # for each source based on its durable successful-day checkpoint.
        self.task = SearchTask(self.window.database, profile, False, website_fetch=browser.get)
        self.task.signals.progress.connect(self.progress)
        self.task.signals.cached.connect(self.cached)
        self.task.signals.finished.connect(self.succeeded)
        self.task.signals.failed.connect(self.failed)
        self.progress('正在启动检索……')
        self.window.thread_pool.start(self.task)

    def progress(self, text):
        if self.task:
            identifier = self.task.profile['id']
            message = f'全部刷新 {self.completed + 1}/{self.total}：{text}'
            self.window.home.set_refresh_state(identifier, message)
            if self.window.results.profile_id == identifier:
                self.window.results.status.setText(message)

    def cached(self, identifier):
        if self.window.results.profile_id == identifier:
            self.window.results._batch_cached(identifier)

    def succeeded(self, report):
        if report.source_errors:
            self.failures.append((self.task.profile['name'], '；'.join(report.source_errors)))
        self.complete_current()

    def failed(self, message):
        if not self.cancelled:
            self.failures.append((self.task.profile['name'], message))
        self.complete_current()

    def complete_current(self):
        identifier = self.task.profile['id']
        self.window.results.website.end_search()
        self.window.home.set_refresh_state(identifier, '')
        self.task = None
        self.completed += 1
        if self.window.results.profile_id == identifier:
            self.window.results.reload_cache()
        self.window.home.reload()
        self.changed.emit()
        QTimer.singleShot(0, self.advance)

    def finish(self):
        self.active = False
        self.window.results.refresh_button.setEnabled(True)
        self.window.home.reload()
        self.changed.emit()
