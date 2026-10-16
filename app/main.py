from __future__ import annotations

import os
import shutil
import sys
import traceback
import uuid
import json
from pathlib import Path
from datetime import datetime

from PySide6.QtCore import QStandardPaths, Qt,QProcess,QSettings
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from database import Database
from ui import APP_STYLE, MainWindow
from data_backup import default_data_directory
from data_location import active_data_directory
from light_theme import configure_light_theme,light_palette


APP_VERSION = "1.9.18"


def resource_path(relative: str) -> Path:
    """Locate packaged assets in development and in a PyInstaller onedir bundle."""
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return bundle_root / relative


def install_exception_handler(log_path: Path) -> None:
    """Write unexpected GUI exceptions to a local log and show a readable error dialog."""
    def handle_exception(exception_type, value, tb):
        if issubclass(exception_type, KeyboardInterrupt):
            sys.__excepthook__(exception_type, value, tb)
            return
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write("\n" + "=" * 80 + "\n")
            traceback.print_exception(exception_type, value, tb, file=handle)
        QMessageBox.critical(
            None,
            "程序遇到错误",
            f"错误信息：{value}\n\n诊断日志：{log_path}",
        )

    sys.excepthook = handle_exception


def main() -> int:
    # High-DPI behavior is enabled by Qt 6. Organization/application names also determine where
    # Windows stores QSettings values such as the last window size.
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
    application = QApplication(sys.argv)
    application.setOrganizationName("OrangeReader")
    application.setApplicationName("BiomedLiterature")
    # Keep the historical storage identity so upgrades open existing caches and PDFs.
    # The visible product name is independent of the on-disk compatibility identifier.
    application.setApplicationDisplayName("刊禾 · Biomed Reader")
    application.setApplicationVersion(APP_VERSION)
    configure_light_theme(application)
    application.setStyleSheet(APP_STYLE)

    if '--complete-data-switch' in sys.argv:
        from data_switch import run_data_switch
        index=sys.argv.index('--complete-data-switch')
        return run_data_switch(application,sys.argv[index+1],int(sys.argv[index+2]))

    icon_path = resource_path("assets/app_icon.png")
    if icon_path.exists():
        application.setWindowIcon(QIcon(str(icon_path)))

    if "--self-test" in sys.argv or "--browser-self-test" in sys.argv:
        # The packaged executable uses this path during release verification. It constructs every
        # page against a disposable database and exits without changing the user's real data.
        directory = Path.cwd() / f".biomed-reader-self-test-{uuid.uuid4().hex}"
        directory.mkdir(parents=True)
        try:
            database = Database(directory / "test.sqlite3", paper_directory=directory / "paper")
            window = MainWindow(database, icon_path)
            window.show_journal_scopes()
            window.journal_scopes.open_scope("builtin_if_6_10")
            assert len(window.journal_scopes.selected_issns()) == 274
            window.journal_scopes.open_scope("builtin_nc_selected")
            assert len(window.journal_scopes.selected_issns()) == 91
            assert window.windowTitle() == "刊禾 · Biomed Reader"
            assert not hasattr(window.settings_page, 'clarivate_key')
            assert not hasattr(window.settings_page, 'sync_ablesci')
            assert not hasattr(window.settings_page, 'email')
            assert window.settings_page.journal_table.isColumnHidden(6)
            assert window.settings_page.journal_table.isColumnHidden(7)
            assert not window.settings_page.journal_table.horizontalHeader().stretchLastSection()
            assert not hasattr(window.settings_page,'search_sources')
            assert not hasattr(window.settings_page,'biorxiv_method')
            assert window.settings_page.biorxiv_years.value() == 2
            assert not hasattr(window.settings_page,'default_max')
            assert not hasattr(window.settings_page,'default_biorxiv_max')
            assert window.settings_page.preprint_servers.count() == 3
            assert '完整检索' == window.results.force_full.text()
            profile_id = database.save_profile(dict(name='self-test', query_expression='DNA',
                journal_issns=['0092-8674'], scope_ids=['builtin_nc_selected'], minimum_jif=0,
                maximum_results=10, sort_mode='最新发表'))
            window.home.reload()
            window.home.set_refresh_state(profile_id, '正在联网搜索')
            assert not window.home.profile_cards[profile_id].search_progress.isHidden()
            window.home.set_refresh_state(profile_id, '')
            assert window.home.profile_cards[profile_id].search_progress.isHidden()
            assert hasattr(window.favorites, 'export_bundle_button')
            assert hasattr(window.favorites, 'import_bundle_button')
            assert hasattr(window.journal_scopes, 'export_bundle_button')
            assert hasattr(window.journal_scopes, 'import_bundle_button')
            assert hasattr(window.home.profile_cards[profile_id], 'handle')
            assert database.get_profile(profile_id)['card_width'] == 0
            assert int(database.get_profile(profile_id)['pubmed_maximum_results']) == 10
            assert int(database.get_profile(profile_id)['biorxiv_maximum_results']) == 10
            window.open_profile(profile_id)
            assert window.results.table.rowCount() == 0
            assert window.results._index_ready
            assert not hasattr(window.settings_page,'biorxiv_website_scope')
            window.journal_scopes.open_scope('builtin_nc_extended')
            assert 500<=len(window.journal_scopes.selected_issns())<=1000
            if '--browser-self-test' in sys.argv:
                from PySide6.QtWebEngineWidgets import QWebEngineView
                from PySide6.QtCore import QEventLoop,QTimer,QUrl,QCoreApplication,QEvent
                browser_view=QWebEngineView(window)
                loop=QEventLoop();loaded=[]
                browser_view.loadFinished.connect(lambda ok:(loaded.append(ok),loop.quit()))
                browser_view.setHtml('<html><body>bioRxiv browser runtime self-test</body></html>',QUrl('https://www.biorxiv.org/search/self-test'))
                QTimer.singleShot(15000,loop.quit)
                loop.exec()
                assert loaded and loaded[-1], 'Embedded browser failed to load offline fixture'
                text=[]
                browser_view.page().toPlainText(lambda value:(text.append(value),loop.quit()))
                QTimer.singleShot(5000,loop.quit);loop.exec()
                assert text and 'bioRxiv browser runtime self-test' in text[0]
                browser_view.deleteLater()
                QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
            assert database.get_settings()['jif_year']==str(datetime.now().year-1)
            assert window.settings_page.pubmed_scope.count()==2
            assert window.settings_page.preprint_scope.count()==4
            assert hasattr(window.results,'result_sources')
            database.select_jif_year(2025)
            assert any(j['jif'] is not None for j in database.list_journals() if j['issn']=='0092-8674')
            assert sum(json.loads(database.get_settings()['user_jif_2023_2025_1913'])['added'].values())==33795
            assert QApplication.testAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
            assert application.palette()==light_palette()
            window.close()
            application.processEvents()
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        return 0

    try:
        data_directory = active_data_directory()
    except Exception as error:
        QMessageBox.critical(None,'本地数据目录不可用',str(error))
        return 1
    data_directory.mkdir(parents=True, exist_ok=True)
    install_exception_handler(data_directory / "logs" / "application.log")
    database = Database(data_directory / "biomed_literature.sqlite3")
    try:
        database.paper_directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Search and offline reading remain available from read-only portable directories.
        # Linking a PDF explains the precise filesystem error at the time of that operation.
        pass

    window = MainWindow(database, icon_path)
    window.show()
    result=application.exec()
    switch=getattr(application,'pending_data_switch',None)
    if switch:
        from data_switch import launch_data_switch
        try:launch_data_switch(switch)
        except Exception as error:QMessageBox.warning(None,'数据目录操作未启动',str(error)+'\n原数据目录未删除。')
    return result


if __name__ == "__main__":
    raise SystemExit(main())
