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
    if '--feedback-self-test' in sys.argv:
        from feedback import run_feedback_self_test
        return run_feedback_self_test(sys.argv[sys.argv.index('--feedback-self-test')+1])
    if '--codex-self-test' in sys.argv:
        from codex_diagnostics import self_test
        return self_test(sys.argv[sys.argv.index('--codex-self-test')+1])
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
            from ai_ui import AIDeploymentPage,RecommendationDialog
            assert window.ai_assistant_page.assistant_board.count()==6
            deployment=AIDeploymentPage(window.ai_manager,window)
            assert deployment.model_grid.itemAtPosition(0,2) and deployment.model_grid.itemAtPosition(1,2)
            deployment.deleteLater()
            from figure_ui import FigureHub,apply_if_scale,build_project,direct_if_edit,figure_tests
            from figure_script import read_script,renderer_source
            from figure_data import GROUPED_CHARTS,csv_bytes,ordinary_values,qpcr_export_csv,qpcr_file_names,qpcr_values,tests_for
            import xlrd,openpyxl
            assert xlrd.__version__ and openpyxl.__version__
            figure_hub=FigureHub(window.ai_manager,window)
            figure_id=figure_hub.store.create('Figure runtime check','qpcr')
            figure_source=directory/'qpcr-check.csv'
            figure_source.write_text('Sample Name,Target Name,Ct Mean\nControl,GAPDH,20\nControl,X,25\nDrug,GAPDH,20\nDrug,X,24\n',encoding='utf8')
            figure_hub.store.attach(figure_id,[figure_source])
            assert figure_hub.store.files(figure_id)[0]['path']==str(figure_source)
            with database.connect() as connection:
                assert connection.execute('SELECT length(data) FROM figure_files WHERE project=?',(figure_id,)).fetchone()[0]==0
            figure_options=dict(sample='Sample Name',gene='Target Name',ct='Ct Mean',reference='GAPDH',
                                control='Control',sheet='',header=0,title='Runtime plot',ylabel='Relative RNA level',
                                kind='bar',test='pooled')
            figure_style=read_script('use_backend("matplotlib")\nset_gridlines(True)','qpcr')
            assert 'def chart(' in renderer_source('qpcr')
            class PlotModelCheck:
                model='preview-fixture'
                def call(self,instruction,text,reference=None):
                    assert reference['images'][0].startswith('data:image/png;base64,iVBOR')
                    assert json.loads(text)['figure']['scene']['width_pt']>0
                    return json.dumps(dict(script='import matplotlib.pyplot as plt\nimport numpy as np\n'
                        'import matplotlib.colors as mc\nx = canvas_width()\nresize_canvas([x+36,canvas_height()])\n'
                        'for y in np.linspace(20,35,2):\n    plt.text(x+2,y,"Check",color=mc.to_hex("steelblue"))'))
            figure_state,figure_files=build_project(figure_hub.store,figure_id,'qpcr',
                dict(options=figure_options,style=figure_style,history=[]),PlotModelCheck(),'Check',None,send_preview=True)
            assert figure_files['pdf'].startswith(b'%PDF') and b'<text ' in figure_files['svg']
            assert figure_files['svg'].count(b'>Check</text>')==2
            assert b'Drug,X,24.0,20.0,4.0,-1.0,2.0' in figure_files['plot.csv']
            class ScriptMaterialFixture:
                provider='codex'
                model='script-material-fixture'
                def call(self,instructions,text,reference=None):
                    payload=json.loads(text)
                    assert payload['raw_rendering_code']['figure_render.py'].startswith('"""')
                    assert payload['materials'][0]['name']=='qpcr-check.csv'
                    assert reference['local_files'][0][1].startswith(b'%PDF')
                    label=next(item for item in payload['figure']['scene']['elements']
                               if item['kind']=='text' and item['args'][2]=='Runtime plot')
                    return json.dumps(dict(script=f'edit_element([{label["id"]},"text","Edited plot"])\n'
                        'set_setting(["options.comparison_mode","none"])\nset_statistical_test("none")'))
            grouped_options=dict(figure_options,sample_grouping={'Control':['Vehicle','Control'],'Drug':['Treatment','Drug']})
            edited_state,script_outputs=build_project(figure_hub.store,figure_id,'qpcr',
                dict(options=grouped_options,style={},history=[]),ScriptMaterialFixture(),'Edit',None,
                drawing_route='vector',send_preview=True)
            assert b'>Edited plot</text>' in script_outputs['svg']
            assert b'Treatment / Drug,X,24.0,20.0,4.0,0.0,1.0' in script_outputs['plot.csv']
            assert len(script_outputs['tests.csv'].splitlines())==1
            class ContinueFigureCheck:
                provider='codex'
                model='continue-figure-fixture'
                def call(self,instructions,text,reference=None):
                    payload=json.loads(text)
                    assert payload['prior_revisions'][-1]['request']=='Edit'
                    assert 'Edited plot' in payload['prior_revisions'][-1]['script']
                    assert any(element['args'][2]=='Edited plot' for element in payload['figure']['scene']['elements']
                               if element['kind']=='text')
                    return json.dumps(dict(script='add_text([12,12,"Second revision"])'))
            _,continued=build_project(figure_hub.store,figure_id,'qpcr',edited_state,ContinueFigureCheck(),
                'Keep that title and add a label',None,drawing_route='vector',send_preview=True,previous_figure=script_outputs)
            assert b'>Edited plot</text>' in continued['svg'] and b'>Second revision</text>' in continued['svg']
            from figure_render import chart
            samples={'A1':['siNC','Vehicle','A1'],'A2':['siNC','Drug','A1'],
                     'A3':['siU1','Vehicle','A3'],'A4':['siU1','Drug','A3']}
            long_table='Sample,Value\nA1,10\nA2,20\nA3,4\nA4,12\nA1,10\nA2,22\nA3,4\nA4,14\n'
            grouped_table_options=dict(sample='Sample',ct='Value',sheet='',header=1,control='A1',
                                       title='',ylabel='Relative level',sample_grouping=samples)
            cells=ordinary_values([dict(name='grouped.csv',data=long_table.encode())],grouped_table_options)
            assert [row['value'] for row in cells[:4]]==[1,2,1,3] and len({row['group'] for row in cells})==4
            qpcr_grouped_data='Sample Name,Target Name,Ct Mean\n'+''.join(
                f'{sample},GAPDH,20\n{sample},X,{ct}\n' for sample,ct in zip(samples,(25,24,23,22)))
            cells_qpcr=qpcr_values([dict(name='grouped.csv',data=qpcr_grouped_data.encode())],
                                   dict(figure_options,control='A1',sample_grouping=samples))
            assert [row['value'] for row in cells_qpcr]==[1,2,1,2] and len({row['group'] for row in cells_qpcr})==4
            unscaled={name:[group,treatment,''] for name,(group,treatment,_) in samples.items()}
            raw_qpcr=qpcr_values([dict(name='grouped.csv',data=qpcr_grouped_data.encode())],
                                 dict(figure_options,control='',sample_grouping=unscaled))
            assert [row['value'] for row in raw_qpcr]==[1/32,1/16,1/8,1/4]
            assert all(row['delta_delta_ct'] is None for row in raw_qpcr)
            raw_table=ordinary_values([dict(name='grouped.csv',data=long_table.encode())],
                                      dict(grouped_table_options,sample_grouping=unscaled))
            assert [row['value'] for row in raw_table[:4]]==[10,20,4,12]
            from figure_grouping import GroupingDialog
            from PySide6.QtWidgets import QStyleOptionViewItem,QTableWidgetSelectionRange
            grouping=GroupingDialog(window,list(samples),samples,'A1',True)
            grouping.swap_columns()
            cell=grouping.table.model().index(0,1)
            delegate=grouping.table.itemDelegateForColumn(1)
            editor=delegate.createEditor(grouping.table,QStyleOptionViewItem(),cell)
            delegate.setEditorData(editor,cell)
            assert editor.findText('Compound')<0 and editor.findText('Drug')>=0 and editor.currentText()=='Vehicle'
            editor.setEditText('New condition')
            delegate.setModelData(editor,grouping.table.model(),cell)
            grouping.table.setRangeSelected(QTableWidgetSelectionRange(1,2,3,2),True)
            grouping.batch_value.setEditText('Shared')
            grouping.fill_selection()
            grouping.table.cellWidget(0,3).setCurrentIndex(0)
            grouping.finish()
            assert grouping.result_settings['A1']==['New condition','siNC','']
            assert grouping.result_settings['A4']==['Drug','Shared','A3']
            for kind in GROUPED_CHARTS:
                grouped_svg=chart(cells,{},dict(grouped_table_options,kind=kind,geometry='table'),[])['svg']
                assert all(label in grouped_svg for label in (b'>siNC</text>',b'>siU1</text>',b'>Vehicle</text>',b'>Drug</text>'))
            from qpcr_mapping import MappingDialog
            qpcr_fixture=[]
            for index,drug_ct in enumerate((24,23,22),1):
                source=('Sample Name,Target Name,Cт,Ct Mean\n'
                        'Control,GAPDH,20,99\nControl,X,22,99\nControl,X,25,99\nControl,X,25.5,99\n'
                        f'Drug,GAPDH,20,99\nDrug,X,{drug_ct-2},99\nDrug,X,{drug_ct},99\nDrug,X,{drug_ct+2},99\n'
                        'Unused,X,Undetermined,99\nDrug,Y,Undetermined,99\n')
                qpcr_fixture.append(dict(id=index,name=f'replicate{index}.csv',data=source.encode('utf8')))
            qpcr_settings={**figure_options,'test':'paired','test_basis':'delta_ct','show_replicates':True,
                'name_mapping':{'samples':{'Control':{'name':'Vehicle','keep':False},'Drug':{'name':'Treated'},
                                            'Unused':{'keep':False}},
                                'genes':{'GAPDH':{'keep':False},'X':{'name':'Mapped X'},'Y':{'keep':False}}}}
            qpcr_points=qpcr_values(qpcr_fixture,qpcr_settings)
            assert len(qpcr_points)==6 and {row['target'] for row in qpcr_points}=={'Mapped X'}
            assert qpcr_points[0]['value']==1 and qpcr_points[0]['ct']==25.25 and qpcr_points[0]['technical_rows_used']==2
            assert qpcr_points[1]['ct']==24 and qpcr_points[1]['technical_wells_removed']==2
            assert 0<figure_tests(qpcr_points,qpcr_settings,'qpcr')[0]['p_unadjusted']<.1
            qpcr_svg=chart(qpcr_points,{},dict(qpcr_settings,control='Vehicle',geometry='qpcr'),[])['svg']
            assert qpcr_svg.count(b'<circle ')==6 and qpcr_svg.count(b'opacity="0.7"')==6 and b'>Treated</text>' in qpcr_svg
            assert b'rotate(-45 ' in qpcr_svg
            mapping_check=MappingDialog(window,qpcr_file_names(qpcr_fixture,qpcr_settings),qpcr_settings)
            assert mapping_check.tables['1','samples'].item(0,0).checkState()==Qt.CheckState.Checked
            from PySide6.QtWidgets import QLineEdit
            mapping_check.show()
            sample_table=mapping_check.tables['1','samples']
            sample_table.edit(sample_table.model().index(1,2))
            QApplication.processEvents()
            name_editor=sample_table.findChild(QLineEdit)
            assert name_editor and name_editor.height()>name_editor.fontMetrics().height()+6
            sample_table.setFocus()
            mismatch=mapping_check.tables['2','samples'].item(1,2)
            mismatch.setText('Other')
            assert mapping_check.validate() and mismatch.foreground().color().name()=='#b42318'
            mismatch.setText('Treated')
            mapping_check.finish()
            assert mapping_check.result_settings['file_mappings']['1']['samples']['Control']['keep']
            assert qpcr_values(qpcr_fixture,{**qpcr_settings,**mapping_check.result_settings})==qpcr_points
            import base64,csv,io,re
            columns=('replicate','group','target','value')
            matrix_fixture=[*qpcr_points,dict(replicate=2,group='Treated',target='Extra target',value=1.5)]
            detail=csv_bytes(matrix_fixture,columns)
            exported=list(csv.reader(io.StringIO(qpcr_export_csv(detail).decode('utf-8-sig'))))
            assert exported[0]==['group','Mapped X','','','Extra target'] and exported[3]==[]
            assert [row[0] for row in exported[1:3]]==['Vehicle','Treated']
            assert exported[1][1:4]==['1.0']*3 and exported[2][4]=='1.5'
            assert exported[4:]==list(csv.reader(io.StringIO(detail.decode('utf-8-sig'))))
            for comparison_mode in ('manual','all'):
                marked=chart(qpcr_points,{},dict(qpcr_settings,control='Vehicle',geometry='qpcr',comparison_mode=comparison_mode),
                             [dict(target='Mapped X',group_a='Vehicle',group_b='Treated',p_unadjusted=.6)])['svg']
                assert marked.count(b'stroke="#000000" stroke-width="0.5"')==3 and b'>n.s.</text>' in marked
                assert re.search(rb'y="85\.0"[^>]*rotate\(-90 [^>]+>Relative RNA level<',marked)
            from PIL import Image
            image_fixture=io.BytesIO()
            Image.new('RGB',(600,520),'#ADBC48').save(image_fixture,'PNG')
            class DirectFigureCheck:
                model='image-tool-fixture'
                provider='codex'
                store=window.ai_manager.store
                cancelled=staticmethod(lambda:False)
                def call(self,instructions,body,reference=None):
                    assert reference['images'][0].startswith('data:image/png;base64,')
                    return json.dumps(dict(prompt='Add a title'))
                def draw_pixels(self,prompt,reference,instructions):
                    assert 'Add a title' in prompt and reference['project'] is True
                    return image_fixture.getvalue()
            direct_model=DirectFigureCheck()
            _,direct_artifacts=build_project(figure_hub.store,figure_id,'qpcr',
                dict(options=figure_options,style={},history=[]),direct_model,'New title',None,
                drawing_route='codex',send_preview=True)
            assert direct_artifacts['png']==image_fixture.getvalue() and b'<image ' in direct_artifacts['svg']
            assert direct_artifacts['pdf'].startswith(b'%PDF') and b'<text ' in direct_artifacts['original.svg']
            from codex_bridge import CodexClient
            from figure_ai_image import configured_route
            assert configured_route(database,'codex','native')=='codex'
            assert configured_route(database,'deepseek','ark')=='ark'
            image_client=CodexClient()
            def simulated_image_turn(model,instructions,items,workspace=None):
                assert model=='test-orchestrator' and workspace is not None
                assert 'image_gen.imagegen' in items[0]['text'] and items[1]['type']=='localImage'
                assert not (workspace/'Model20250528.pdf').exists()
                image_client.generated_images.append(image_fixture.getvalue())
                return '',{},False
            image_client.turn=simulated_image_turn
            raw,_,incomplete=image_client.generate_pixels('test-orchestrator','Draw','Render test',
                dict(project=True,images=['data:image/png;base64,'+base64.b64encode(image_fixture.getvalue()).decode()]))
            assert raw==image_fixture.getvalue() and not incomplete
            from PIL import Image
            from figure_palette import PaletteDialog
            from figure_render import if_scene,wb_scene
            from PySide6.QtCore import QRect
            from PySide6.QtGui import QImage,QPainter
            from PySide6.QtWidgets import QStyleOptionViewItem
            palette_check=PaletteDialog(window,dict(mode='gradient',map='spring_r'),[])
            for name in ('spring','spring_r','tab10'):
                palette_image=QImage(300,28,QImage.Format.Format_RGB32)
                painter=QPainter(palette_image)
                option=QStyleOptionViewItem();option.rect=QRect(0,0,300,28)
                delegate=palette_check.maps.itemDelegate()
                try:delegate.paint(painter,option,palette_check.maps.model().index(palette_check.maps.findText(name),0))
                finally:painter.end()
                assert name in delegate.strips
            assert delegate.strips['spring'].toImage().pixelColor(0,0)==delegate.strips['spring_r'].toImage().pixelColor(127,0)
            palette_check.close()
            pixels=Image.new('RGB',(35,18),'#ADBC48')
            original=io.BytesIO();pixels.save(original,'PNG')
            panel=[dict(uid='A',file=1,rect=[0,0,35,18],label='Band')]
            montage=wb_scene(panel,{1:original.getvalue()},{})
            layout_script='from PIL import Image\nimport matplotlib.pyplot as plt\nw = Image.size("crop:panel:A")[0]\nrebuild_canvas([120,80])\nplt.imshow("crop:panel:A",20,20,w)\nplt.text(20,15,"Band A")'
            layout=read_script(layout_script,'wb',montage.metadata(),lambda key:list(montage.load_source(key)[0].size))
            montage_output=wb_scene(panel,{1:original.getvalue()},layout).export()
            encoded=re.search(rb'data:image/png;base64,\s*([A-Za-z0-9+/=]+)',montage_output['svg'])
            assert encoded and b'>Band A</text>' in montage_output['svg']
            with Image.open(io.BytesIO(base64.b64decode(encoded.group(1)))) as image:
                assert image.size==pixels.size and image.convert('RGB').tobytes()==pixels.tobytes()
            long_label='Long treatment label for the horizontal axis'
            long_wb=wb_scene([{**panel[0],'lanes':[long_label]}],{1:original.getvalue()},{})
            long_if=if_scene([{**panel[0],'channels':{'IF':1},'label':long_label}],{1:original.getvalue()},{})
            channels={'pATM':1,'pNBS1':1,'DAPI':1,'merge':1}
            if_panels=[{**panel[0],'label':'siNC','channels':channels,'scale_calibration':.5},
                       {**panel[0],'uid':'B','label':'siU1','channels':channels,'scale_pixels_per_um':3}]
            if_request='更换pATM和pNBS1的上下位置，并且为每个图片都加上比例尺，都是5μm'
            if_layout=read_script(direct_if_edit(if_request,if_panels),'if',
                                  if_scene(if_panels,{1:original.getvalue()},{}).metadata())
            assert apply_if_scale(if_panels,if_layout.pop('if_scale_all'))==[]
            scaled=if_scene(if_panels,{1:original.getvalue()},if_layout)
            anchors={group['id']:group['anchor_pt'] for group in scaled.metadata()['groups']}
            assert anchors['channel:pNBS1']<anchors['channel:pATM']<anchors['channel:DAPI']
            assert len(scaled.sources)==8 and all(bar is not None and image.tobytes()==pixels.tobytes()
                                                 for image,bar in scaled.sources.values())
            assert [round(bar[2]*image.width) for image,bar in scaled.sources.values()]==[10]*4+[15]*4
            uncalibrated={**panel[0],'label':'Missing calibration','channels':channels}
            assert apply_if_scale([uncalibrated],5)==['Missing calibration'] and 'scale_target' not in uncalibrated
            for long_scene in (long_wb,long_if):
                svg=long_scene.export()['svg']
                assert b'rotate(-45 ' in svg and long_label.encode() in svg
            from wb_density import automatic_bands,image_signature,measure_bands,panel_density
            from wb_density_ui import DensityDialog
            lane_labels=['Control','Treatment']
            band_boxes=[[12,13,20,7],[67,13,20,7]]
            wb_sources,wb_panels,wb_images,band_settings={},{},[],{}
            for index,signals in enumerate(((150,120),(100,140)),1):
                band=Image.new('L',(105,38),180)
                for (x,y,w,h),value in zip(band_boxes,signals):
                    band.paste(value,(x,y,x+w,y+h))
                buffer=io.BytesIO()
                band.save(buffer,'PNG')
                wb_sources[index]=buffer.getvalue()
                wb_panels[index]=dict(uid=str(index),file=index,rect=[0,0,105,38],
                                      label=f'Target {index}',lanes=lane_labels)
                wb_images.append(band)
                band_settings[str(index)]=dict(signature=image_signature(band),labels=lane_labels,rectangles=band_boxes)
            bands=list(wb_panels.values())
            auto=automatic_bands(wb_images[0],2,'dark')
            assert measure_bands(wb_images[0],auto,'dark',0)[0]==[1.,2.]
            freehand=[dict(rect=[x,y,w,h],points=[(x,y),(x+w-1,y),(x+w-1,y+h-1),(x,y+h-1)])
                      for x,y,w,h in band_boxes]
            assert measure_bands(wb_images[1],freehand,'dark',0)[0]==[1.,.5]
            assert figure_tests(qpcr_points,dict(qpcr_settings,comparison_mode='none'),'qpcr')==[]
            wb_settings=dict(enabled=True,control='Control',polarity='dark',display='below',panels=band_settings)
            assert panel_density(bands[0],wb_images[0],lane_labels,wb_settings)[0]==[1.,2.]
            assert panel_density(bands[1],wb_images[1],lane_labels,wb_settings)[0]==[1.,.5]
            wb_dialog=DensityDialog(window,bands,wb_images,lane_labels,wb_settings)
            wb_dialog.finish()
            assert wb_dialog.result_settings['control']=='Control'
            from PySide6.QtCore import QPointF
            mixed=DensityDialog(window,bands,wb_images,lane_labels,dict(wb_settings,selection='auto'))
            originals=[row[:] for row in mixed.rectangles]
            mixed.selection_mode.setCurrentIndex(mixed.selection_mode.findData('manual'))
            assert mixed.rectangles==originals
            mixed.record_trace([QPointF(x/105,y/38) for x,y in ((11,12),(31,12),(31,20),(11,20))])
            assert mixed.rectangles[0][0]['points'] and mixed.rectangles[0][1]==originals[0][1]
            assert mixed.rectangles[1]==originals[1]
            mixed.selection_mode.setCurrentIndex(mixed.selection_mode.findData('auto'))
            assert mixed.rectangles==originals
            mixed.selection_mode.setCurrentIndex(mixed.selection_mode.findData('manual'))
            assert mixed.rectangles[0][0]['points'] and mixed.rectangles[1]==originals[1]
            mixed.finish()
            assert mixed.result_settings['panels']['1']['rectangles'][1]==originals[0][1]
            wb_under=wb_scene(bands,wb_sources,{},wb_settings).export()['svg']
            assert wb_under.count(b'>1.00</text>')==2 and b'>2.00</text>' in wb_under and b'>0.50</text>' in wb_under
            wb_side=wb_scene(bands,wb_sources,{},dict(wb_settings,display='side')).export()['svg']
            assert wb_side.count(b'<image ')==4 and b'Measurement regions' in wb_side and b'#29A7C7' in wb_side
            rank_fixture=[dict(target='',group=group,value=value) for group,points in
                          [('A',[1,2,3]),('B',[4,5,6])] for value in points]
            assert abs(tests_for(rank_fixture,'ranksum')[0]['p_unadjusted']-0.1)<1e-12
            selected=tests_for(rank_fixture+[dict(target='',group='C',value=x) for x in (2,3,4)],
                               'ranksum',[['B','A']])
            assert len(selected)==1 and (selected[0]['group_a'],selected[0]['group_b'])==('B','A')
            figure_hub.store.save(figure_id,figure_state,figure_files)
            figure_store=figure_hub.store
            figure_window=figure_hub.open_project(figure_id)
            assert figure_window.svg_ready() and not figure_hub.isVisible()
            from PySide6.QtWidgets import QPushButton
            from unittest.mock import patch
            from figure_grouping import GroupingDialog

            def await_figure_job(dialog):
                loop=QEventLoop()
                timer=QTimer(loop)
                timer.timeout.connect(lambda:loop.quit() if dialog.task is None else None)
                timer.start(25)
                QTimer.singleShot(12000,loop,loop.quit)
                loop.exec()
                assert dialog.task is None and dialog.local_button.isEnabled()

            grouping_calls=[]
            def open_test_grouping(dialog):
                grouping_calls.append(dialog.names)
                return False

            def click_grouping(dialog):
                button=next(item for item in dialog.findChildren(QPushButton)
                            if item.text()=='设置样本分组与归一化')
                button.click()
                assert dialog.task is not None
                await_figure_job(dialog)

            with patch.object(GroupingDialog,'exec',open_test_grouping):
                click_grouping(figure_window)
            figure_window.close()
            figure_store.delete(figure_id)
            assert not figure_store.exports(figure_id) and not figure_store.files(figure_id)
            table_id=figure_store.create('Grouping runtime check','table')
            table_source=directory/'grouping-check.csv'
            table_source.write_text('Group,Value\nA1,2\nA2,4\n',encoding='utf8')
            figure_store.attach(table_id,[table_source])
            table_window=FigureHub(window.ai_manager,window).open_project(table_id)
            with patch.object(GroupingDialog,'exec',open_test_grouping):
                click_grouping(table_window)
            assert grouping_calls==[['Control','Drug'],['A1','A2']]
            table_window.close()
            figure_store.delete(table_id)
            wb_id=figure_store.create('WB local preview runtime check','wb')
            wb_path=directory/'WB-local-preview.tif'
            wb_images[0].save(wb_path)
            figure_store.attach(wb_id,[wb_path])
            wb_file=figure_store.files(wb_id)[0]['id']
            figure_store.save(wb_id,dict(history=[],style={},panels=[dict(bands[0],file=wb_file)]))
            wb_window=FigureHub(window.ai_manager,window).open_project(wb_id)
            wb_window.local_button.click()
            assert wb_window.task is not None
            await_figure_job(wb_window)
            assert wb_window.svg_ready() and wb_window.artifacts['pdf'].startswith(b'%PDF')
            wb_window.close()
            figure_store.delete(wb_id)
            from protocol_ui import ProtocolHub
            from protocol_documents import document_sections,export_docx,export_pdf,import_protocols,share_protocols,write_protocol
            assert list(document_sections({'text':'清洗缓冲液：配方 A；成分 B；Storage buffer：配方 C'}))==[
                ('body','清洗缓冲液：配方 A；成分 B'),('body','Storage buffer：配方 C')]
            from model_artifact import validate_figure
            from figure_render import Figure
            flow_fixture=Figure(120,48,{})
            flow_fixture.text(10,20,'Check source image')
            protocol=dict(title='图像分析 · Protocol',text='试剂：无。\n仪器：显微镜。\n步骤：检查图像信息。',
                          stages=['核对原图','记录结果'],sources=[],author='QA',
                          diagram=validate_figure(flow_fixture.export()['pdf']))
            word_path,pdf_path,zip_path=[directory/name for name in ('protocol.docx','protocol.pdf','protocol.zip')]
            write_protocol(word_path,protocol,export_docx)
            write_protocol(pdf_path,protocol,export_pdf)
            assert word_path.read_bytes().startswith(b'PK') and pdf_path.read_bytes().startswith(b'%PDF')
            from zipfile import ZipFile
            with ZipFile(word_path) as archive:
                assert b'AI Protocol' in archive.read('word/header1.xml') and b'PAGE' in archive.read('word/footer1.xml')
            import pdfplumber
            with pdfplumber.open(pdf_path) as exported_protocol:
                assert 'AI Protocol' in exported_protocol.pages[0].extract_text()
            from protocol_workflow import draft_protocol
            photo=directory/'protocol-revision.png'
            photo.write_bytes(original.getvalue())
            class RevisionFixture:
                model='image-fixture'
                provider='codex'
                input_budget=8000
                cancelled=staticmethod(lambda:False)
                def call(self,instructions,body,reference=None):
                    if reference:
                        assert reference['images'][0].startswith('data:image/png;base64,iVBOR')
                        return '{"allowed":true,"notes":"图像标注说明"}'
                    assert '补充图像标注' in body
                    return '{"allowed":true,"queries":[]}'
                def reduce(self,material,progress,instructions):
                    assert '补充图像标注' in material and '图像标注说明' in material and '检查图像信息' in material
                    return '{"allowed":true,"title":"修订稿","body":"已修订的完整正文"}',False
                def result(self,body,**metadata):
                    return dict(text=body,**metadata)
            revision=draft_protocol(RevisionFixture(),database,'图像分析',[str(photo)],[],lambda _:None,
                                    with_diagram=False,previous=protocol,request='补充图像标注')
            assert revision['text']=='已修订的完整正文'
            assert share_protocols(zip_path,[protocol])==1 and import_protocols(database,zip_path)==1
            protocol_id=window.ai_manager.repository.histories('protocol')[0]['id']
            protocol_hub=ProtocolHub(window.ai_manager,window)
            assert protocol_hub.board.count()==1
            protocol_dialog=protocol_hub.open(protocol_id)
            assert '检查图像信息' in protocol_dialog.editor.toPlainText()
            assert 'Protocol · QA' in protocol_dialog.preview.toPlainText()
            protocol_dialog.set_editor_visible(True)
            assert protocol_dialog.editor.textCursor().hasSelection()
            protocol_dialog.editor.insertPlainText('已替换的正文')
            protocol_dialog.set_editor_visible(False)
            for size in ((800,440),(1060,820),(820,510)):
                protocol_dialog.resize(*size)
                QApplication.processEvents()
                assert protocol_dialog.preview.toPlainText().count('已替换的正文')==1
                assert '检查图像信息' not in protocol_dialog.preview.toPlainText()
            protocol_dialog.splitter.setSizes([0,600])
            QApplication.processEvents()
            assert protocol_dialog.splitter.sizes()[0]==0 and protocol_dialog.preview.isVisible()
            protocol_dialog.toggle_full()
            assert protocol_dialog.preview.isVisible() and protocol_dialog.upper_panel.isHidden()
            protocol_dialog.toggle_full()
            protocol_dialog.save_clicked()
            assert window.ai_manager.repository.history(protocol_id)['result']['text']=='已替换的正文'
            protocol_dialog.close()
            finder_id=window.ai_manager.repository.save_history('finder','Runtime scientific chat',dict(messages=[],attachments=[],notes={}),{})
            finder=FinderChat(window.ai_manager,window.ai_manager.repository.history(finder_id),window)
            assert finder.models.count()==0 and not finder.messages
            finder.close()
            hub=FinderHub(window.ai_manager,window);assert hub.board.count()==1;hub.close()
            from lab_ui import LoginDialog
            login_check=LoginDialog(window.ai_manager,'pi');assert not hasattr(login_check,'connection');assert login_check.login.text().startswith('登录（');login_check.reject()
            assert login_check.gateway.currentData()=='https://biomedreader.cn'
            from lab_balance_ui import LabBalanceDialog
            balance_check=LabBalanceDialog(window.ai_manager);assert balance_check.task is None;balance_check.reject()
            from lab_keys_ui import LabKeysDialog
            keys_check=LabKeysDialog(window.ai_manager);assert keys_check.table.rowCount()==0;keys_check.reject()
            import student_keys,lab_member_ids
            assert not student_keys.expire_on_startup(window.ai_manager.store)
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
    word_background=sys.platform=='darwin' and '--word-background' in sys.argv
    if not word_background:window.show()
    from release_notes import first_run_release_notes
    if startup_test_directory:
        from release_notes import ReleaseNotesDialog,BUILD_ID
        settings=QSettings('OrangeReader','BiomedLiterature')
        expected_stamp=BUILD_ID+':'+str(settings.value('installation_id','source'))
        expect_notes=not word_background and settings.value('last_release_notes','')!=expected_stamp
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
                if word_background:
                    window.word_bridge.execute({'action':'show'})
                    report['explicit_show_visible']=window.isVisible()
                    report['passed']=bool(not report['main_window_visible'] and not report['notes_visible'] and report['explicit_show_visible'])
                else:
                    report['passed']=bool(report['main_window_visible'] and report['notes_visible']==expect_notes and report['notes_stamp_saved'] and (not expect_notes or report.get('detailed_notes_work')))
                (startup_test_directory/'startup-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
                window.close()
                application.quit()
            QTimer.singleShot(100,finish)
        QTimer.singleShot(650,inspect_normal_startup)
    if not word_background:QTimer.singleShot(150,lambda:first_run_release_notes(window))
    if sys.platform=='darwin' and not startup_test_directory and not word_background:
        def offer_word_plugin():
            settings=database.get_settings()
            if settings.get('mac_word_plugin_offer')=='2.2.1-v4':return
            database.save_settings({'mac_word_plugin_offer':'2.2.1-v4'})
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
