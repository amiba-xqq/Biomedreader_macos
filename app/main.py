from __future__ import annotations

import os
import shutil
import sys
import traceback
import uuid
import json
from pathlib import Path
from datetime import datetime

from PySide6.QtCore import QStandardPaths, Qt,QProcess,QSettings,QTimer,QCoreApplication,QEvent,QEventLoop,QUrl
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from database import Database
from ui import APP_STYLE, MainWindow
from data_backup import default_data_directory
from data_location import active_data_directory
from light_theme import configure_light_theme,light_palette


from version import APP_VERSION


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
    if '--word-mac-command' in sys.argv:
        from mac_word_plugin import main as word_command
        index=sys.argv.index('--word-mac-command')
        return word_command(sys.argv[index+1:index+3])
    startup_test_directory=None
    if '--startup-test' in sys.argv:
        # Exercise the NORMAL startup/event-loop/release-notes path against a
        # marked disposable store. QSettings is redirected before any window is
        # constructed; never read/write the user's real library or preferences.
        index=sys.argv.index('--startup-test')
        startup_test_directory=Path(sys.argv[index+1]).resolve()
        marker=startup_test_directory/'.biomed-startup-test'
        if startup_test_directory.exists() and any(startup_test_directory.iterdir()) and not marker.is_file():
            raise ValueError('Startup diagnostics require a new empty or marked test directory.')
        startup_test_directory.mkdir(parents=True,exist_ok=True);marker.write_text('Biomed Reader startup fixture',encoding='utf8')
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat,QSettings.Scope.UserScope,str(startup_test_directory/'preferences'))
        # The QSettings(organization, application) overload explicitly selects
        # NativeFormat, ignoring setDefaultFormat(). Redirect that overload too.
        original_settings=QSettings
        class IsolatedStartupSettings(original_settings):
            def __init__(self,*arguments):
                organization=arguments[0] if arguments else 'OrangeReader'
                name=arguments[1] if len(arguments)>1 else 'BiomedLiterature'
                super().__init__(original_settings.Format.IniFormat,original_settings.Scope.UserScope,organization,name)
        for name in ('__main__','main','ui','data_location','release_notes','local_maintenance','PySide6.QtCore'):
            module=sys.modules.get(name)
            if module is not None and getattr(module,'QSettings',None) is original_settings:
                setattr(module,'QSettings',IsolatedStartupSettings)
    if '--clear-local-data' in sys.argv:
        from local_maintenance import clear_directory
        index=sys.argv.index('--clear-local-data')
        try:clear_directory(sys.argv[index+1]);return 0
        except Exception:return 3
    if '--reviewer-self-test' in sys.argv:
        from peer_review import runtime_self_test
        return runtime_self_test(sys.argv[sys.argv.index('--reviewer-self-test')+1])
    if '--update-self-test' in sys.argv:
        from software_update import check_release
        index=sys.argv.index('--update-self-test')
        try:report=dict(passed=True,**check_release());status=0
        except Exception as error:report=dict(passed=False,error=str(error));status=1
        Path(sys.argv[index+1]).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        return status
    if '--pdf-self-test' in sys.argv:
        from pdf_import import pdf_runtime_self_test
        index=sys.argv.index('--pdf-self-test')
        return pdf_runtime_self_test(sys.argv[index+2:],sys.argv[index+1])
    if '--network-self-test' in sys.argv:
        # Release diagnostics use a separate process and never open the user's DB.
        from network_transport import run_network_self_test
        index = sys.argv.index('--network-self-test')
        return run_network_self_test(Path(sys.argv[index + 1]))
    # High-DPI behavior is enabled by Qt 6. Organization/application names also determine where
    # Windows stores QSettings values such as the last window size.
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
    application = QApplication(sys.argv)
    from minimized_drag import install_minimized_drag
    install_minimized_drag(application)
    application.setOrganizationName("OrangeReader")
    application.setApplicationName("BiomedLiterature")
    # Keep the historical storage identity so upgrades open existing caches and PDFs.
    # The visible product name is independent of the on-disk compatibility identifier.
    application.setApplicationDisplayName("刊禾 · Biomed Reader")
    application.setApplicationVersion(APP_VERSION)
    from PySide6.QtGui import QFont
    ai_font=QFont();ai_font.setFamilies(["Arial","Microsoft YaHei"]);application.setFont(ai_font)
    configure_light_theme(application)
    application.setStyleSheet(APP_STYLE)

    if '--complete-clear-data' in sys.argv:
        from data_switch import wait_for_parent
        from local_maintenance import clear_directory
        index=sys.argv.index('--complete-clear-data')
        try:
            wait_for_parent(int(sys.argv[index+2]));clear_directory(sys.argv[index+1])
            QMessageBox.information(None,'本地数据已删除','资料库已移至 Windows 回收站。下次打开会建立新的默认资料库。');return 0
        except Exception as error:QMessageBox.warning(None,'未能完成数据清理',str(error));return 3

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
            from citations import render
            rendered = render([{'article_key':'self-test-citation','title':'Citation runtime check','authors':'Example A',
                'journal_title':'Cell','publication_date':'2025-01-01','doi':'10.1000/selftest'}], 'Cell')
            assert 'Citation runtime check' in rendered['references'][0]
            assert rendered['markers'] == ['1']
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
            from report_design import ReportPresentation,export_news_pdf,report_image
            from concurrent.futures import ThreadPoolExecutor
            editorial=dict(text='Runtime fixture',provider='deepseek',model='packaged-self-test',coverage='Synthetic fixture',presentation=dict(
                scientific_question='Runtime question?',highlights=['Fixture finding.'],innovations=['Fixture method.'],
                news=dict(background='Synthetic background.',main_discovery='Synthetic finding.',research=[dict(heading='Evidence',body='Synthetic evidence only.')],contribution='Synthetic contribution.',affiliations='Not provided.'),
                diagram=dict(nodes=[dict(id='a',label='DNA',visual='dna',phase='context'),dict(id='b',label='Response',visual='signal',phase='mechanism'),dict(id='c',label='Outcome',visual='outcome',phase='outcome')],edges=[{'from':'a','to':'b','label':'associated'}],caption='Synthetic concept illustration.'),evidence_limits='Runtime test, not real evidence.'))
            preview=ReportPresentation();preview.set_result(editorial,dict(title='Editorial runtime check'),'brief');assert len(preview.brief_labels)==3
            preview.set_result(editorial,dict(title='Editorial runtime check'),'news');assert not report_image(editorial)[0].isNull()
            with ThreadPoolExecutor(max_workers=1) as executor:
                assert executor.submit(export_news_pdf,directory/'editorial-check.pdf',[(dict(title='Editorial runtime check'),editorial)]).result(timeout=30)>0
            assert (directory/'editorial-check.pdf').read_bytes().startswith(b'%PDF')
            from research_report import parse_research,research_text
            from ai_workflow_ui import ScientificQuestionsDialog
            research_data=dict(categories=[dict(name='Runtime direction',article_ids=[1])],questions=[dict(question=f'Runtime question {i}?',reason='Synthetic test reason.',article_ids=[],relation='') for i in range(1,13)])
            structured=parse_research(json.dumps(research_data),[dict(article_key='runtime-paper',title='Runtime paper')])
            research_result=dict(text=research_text(structured),research=structured,topic='Runtime research report',provider='deepseek',model='packaged-self-test',read_count=1)
            research_dialog=ScientificQuestionsDialog(window.ai_manager,window);research_dialog.display(research_result,saved=True)
            with ThreadPoolExecutor(max_workers=1) as executor:
                assert executor.submit(export_news_pdf,directory/'research-check.pdf',[(dict(title='Runtime research report'),research_result)],research=True).result(timeout=30)>0
            import pdfplumber
            with pdfplumber.open(directory/'research-check.pdf') as checked:
                # Diagonal watermark glyphs may be interleaved into body lines
                # by PDF extractors; verify the body without those glyphs.
                all_text='\n'.join(page.filter(lambda obj:obj.get('object_type')!='char' or abs(obj.get('matrix',(1,0,0))[1])+abs(obj.get('matrix',(1,0,0))[2])<.00001).extract_text() or '' for page in checked.pages)
                assert 'Runtime question 12?' in all_text
            research_dialog.close()
            from ai_finder_ui import FinderHub,FinderChat
            from ai_ui import RecommendationDialog
            assert window.ai_assistant_page.assistant_board.count()==4
            finder_id=window.ai_manager.repository.save_history('finder','Runtime scientific chat',dict(messages=[],attachments=[],notes={}),{})
            finder=FinderChat(window.ai_manager,window.ai_manager.repository.history(finder_id),window)
            assert finder.models.count()==0 and not finder.messages
            finder.close()
            hub=FinderHub(window.ai_manager,window);assert hub.board.count()==1;hub.close()
            recommendation=RecommendationDialog(window.ai_manager,window);assert not hasattr(recommendation,'output_limit');recommendation.close()
            # Verify packaged Pillow codecs, figure validation and native previews.
            import io
            from PIL import Image
            from model_artifact import validate_figure,figure_bytes,figure_image
            for kind in ('PNG','JPEG','TIFF'):
                stream=io.BytesIO();Image.new('RGB',(600,400),'#75AAA2').save(stream,format=kind,dpi=(300,300));raw=stream.getvalue();artifact=validate_figure(raw)
                assert figure_bytes(artifact)==raw and not figure_image(artifact).isNull()
            from image_routes import ImageSettingsDialog
            image_settings=ImageSettingsDialog(database,'bailian',window);image_settings.show();image_settings.reject()
            editorial['presentation']['english_brief']=dict(scientific_question='English runtime question?',highlights=['Finding'],innovations=['Method'])
            preview.set_result(editorial,dict(title='Runtime bilingual check'),'brief');preview.set_language('en');assert preview.brief_labels[0].text()=='English runtime question?'
            preview.close()
            window.close()
            application.processEvents()
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        return 0

    try:
        data_directory = startup_test_directory/'data' if startup_test_directory else active_data_directory()
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
    from release_notes import first_run_release_notes
    if startup_test_directory:
        from release_notes import ReleaseNotesDialog,BUILD_ID
        settings=QSettings('OrangeReader','BiomedLiterature')
        expected_stamp=BUILD_ID+':'+str(settings.value('installation_id','source'))
        expect_notes=settings.value('last_release_notes','')!=expected_stamp
        def inspect_normal_startup():
            notes=next((widget for widget in application.topLevelWidgets() if isinstance(widget,ReleaseNotesDialog) and widget.isVisible()),None)
            report=dict(version=APP_VERSION,main_window_visible=window.isVisible(),notes_expected=expect_notes,notes_visible=notes is not None)
            if notes:
                brief=notes.browser.toPlainText();notes.mode.setCurrentIndex(1)
                report['detailed_notes_work']=len(notes.browser.toPlainText())>len(brief)
                notes.accept()
            report['notes_stamp_saved']=settings.value('last_release_notes','')==expected_stamp
            # Saving occurs after the modal loop unwinds; verify it on the next
            # event iteration, then close just this test window.
            def finish():
                report['notes_stamp_saved']=QSettings('OrangeReader','BiomedLiterature').value('last_release_notes','')==expected_stamp
                report['passed']=bool(report['main_window_visible'] and report['notes_visible']==expect_notes and report['notes_stamp_saved'] and (not expect_notes or report.get('detailed_notes_work')))
                (startup_test_directory/'startup-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
                window.close()
            QTimer.singleShot(100,finish)
        QTimer.singleShot(650,inspect_normal_startup)
    QTimer.singleShot(150,lambda:first_run_release_notes(window))
    if sys.platform=='darwin' and not startup_test_directory:
        def offer_word_plugin():
            settings=database.get_settings()
            if settings.get('mac_word_plugin_offer')=='2.2.1-v2':return
            database.save_settings({'mac_word_plugin_offer':'2.2.1-v2'})
            from mac_word_plugin import install_plugin
            install_plugin(window)
        QTimer.singleShot(2000,offer_word_plugin)
    result=application.exec()
    clear_path=getattr(application,'pending_clear_data',None)
    uninstall=getattr(application,'pending_uninstall',None)
    if clear_path:
        prefix=[] if getattr(sys,'frozen',False) else [str(Path(__file__).resolve())]
        started=QProcess.startDetached(sys.executable,prefix+['--complete-clear-data',clear_path,str(os.getpid())])
        if not (started[0] if isinstance(started,tuple) else started):QMessageBox.warning(None,'未启动数据清理','未删除任何数据，请重新打开软件后重试。')
    if uninstall:QProcess.startDetached(uninstall,[])
    switch=getattr(application,'pending_data_switch',None)
    if switch:
        from data_switch import launch_data_switch
        try:launch_data_switch(switch)
        except Exception as error:QMessageBox.warning(None,'数据目录操作未启动',str(error)+'\n原数据目录未删除。')
    return result


if __name__ == "__main__":
    raise SystemExit(main())
