import hashlib,json,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from PySide6.QtCore import Qt,QThreadPool
from PySide6.QtWidgets import QApplication,QMessageBox,QDialog
from database import Database
from favorite_tools import filter_grouped_favorites,FavoriteSearchDialog,confirm_unfavorite,search_terms
from ui import FavoritesPage,ArticleDialog,ResultsPage,BulkUnfavoriteDialog
from software_update import (check_release,version_numbers,platform_package,download_artifact,
    asset_response,validate_artifact,UpdateCancelled,SoftwareUpdateWidget,MANIFEST_URL)

app=QApplication.instance() or QApplication([])

def article(key,title,abstract=''):
    return dict(article_key=key,pmid=key.split(':')[-1],doi='',title=title,abstract=abstract,authors='Author A',
        journal_title='Cell',issn='0092-8674',publication_date='2025-01-01',source_name='PubMed',volume='86',pages='10-20',
        pmcid='',publication_year=2025,cited_by_count=0,is_open_access=0,landing_page_url='',graphical_abstract_url='')

class FavoritesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.root=Path(self.temp.name).resolve()
        assert self.root.is_relative_to(Path(__file__).resolve().parent)
        self.db=Database(self.root/'library.sqlite3')
        self.a,_=self.db.create_favorite_group('Alpha','DNA');self.b,_=self.db.create_favorite_group('Beta','RNA')
        self.empty,_=self.db.create_favorite_group('Empty','empty')
        self.rows=[article('pmid:1','DNA repair','ATM signaling'),article('pmid:2','RNA processing','R-loop'),article('pmid:3','Ungrouped work')]
        for index,row in enumerate(self.rows):
            source=self.root/f'original-{index}.pdf';source.write_bytes(b'%PDF-1.4\nfixture\n%%EOF')
            self.db.save_pdf_favorite(row,source,self.a if index!=1 else self.b)
        self.db.transfer_favorites(['pmid:1'],[self.b])
        with self.db.connect() as c:c.execute('DELETE FROM favorite_group_articles WHERE article_key=?',('pmid:3',))
    def tearDown(self):
        QThreadPool.globalInstance().waitForDone(5000);app.processEvents();self.temp.cleanup()
    def test_scopes_and_tokens_deduplicate_without_changing_library(self):
        selected=dict(group_ids=[str(self.a),str(self.b)],query='"DNA repair" ATM')
        result=filter_grouped_favorites(self.db,self.db.grouped_favorites(),selected)
        self.assertEqual(set(result),{'Alpha','Beta'})
        self.assertEqual({row['article_key'] for rows in result.values() for row in rows},{'pmid:1'})
        selected['group_ids']=[str(self.a)]
        self.assertEqual(set(filter_grouped_favorites(self.db,self.db.grouped_favorites(),selected)),{'Alpha'})
        self.assertEqual(len(self.db.favorite_articles()),3)
        self.assertEqual(search_terms('ＤＮＡ “repair pathway”'),['dna','repair pathway'])
    def test_filtered_group_cards_and_flat_view_and_clear(self):
        page=FavoritesPage(self.db);page.search_filter=dict(group_ids=[str(self.a),str(self.b)],query='ATM');page.reload()
        self.assertEqual(len(page.group_container.cards),2)
        page._show_group('Beta');self.assertEqual([a['article_key'] for a in page.visible_articles],['pmid:1'])
        page.article_view.setCurrentIndex(page.article_view.findData('cards'));self.assertEqual(len(page.visible_articles),1)
        page.view_mode.setCurrentText('不分组展示');self.assertEqual(len(page.visible_articles),1)
        page.search_filter['query']='NO_MATCH_8841';page.current_group=None;page.view_mode.setCurrentText('分组展示');page.reload()
        self.assertEqual(len(page.group_container.cards),0)
        page.clear_favorite_search();self.assertEqual(len(page.all_articles),3);self.assertGreaterEqual(len(page.group_container.cards),3)
        page.deleteLater();app.processEvents()
    def test_search_dialog_requires_groups_and_query(self):
        dialog=FavoriteSearchDialog(self.db);dialog.select_groups(False);dialog.query.setText('DNA')
        with patch.object(QMessageBox,'warning') as warning:dialog.validate();self.assertTrue(warning.called)
        self.assertIsNone(dialog.selection);dialog.select_groups(True);dialog.query.setText('"unclosed')
        with patch.object(QMessageBox,'warning') as warning:dialog.validate();self.assertTrue(warning.called)
        dialog.query.setText('ATM');dialog.validate();self.assertEqual(dialog.result(),QDialog.DialogCode.Accepted)
        self.assertTrue(dialog.selection['group_ids']);dialog.deleteLater()
    def test_global_removal_preserves_cache_notes_and_original(self):
        profile=self.db.save_profile(dict(name='Profile',query_expression='DNA',journal_issns=['0092-8674'],minimum_jif=0,maximum_results=0,sort_mode='最新发表'))
        self.db.store_refresh_results(profile,[(self.rows[0],1)],'pubmed');self.db.set_favorite('pmid:1',True,profile_id=profile)
        self.db.save_favorite_notes('pmid:1','tag','retained note');copy=self.db.linked_pdf('pmid:1')
        self.db.set_favorite('pmid:1',False,profile_id=profile)
        self.assertFalse(copy.exists());self.assertIsNone(self.db.linked_pdf('pmid:1'))
        self.assertEqual(len(self.db.favorite_articles()),2);self.assertTrue(self.db.article_record('pmid:1'))
        self.assertEqual(len(self.db.cached_articles(profile,'pubmed')),1)
        with self.db.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM favorite_group_articles WHERE article_key=?',('pmid:1',)).fetchone()[0],0)
            self.assertEqual(c.execute('SELECT personal_note FROM favorites WHERE article_key=?',('pmid:1',)).fetchone()[0],'retained note')
        self.assertTrue((self.root/'original-0.pdf').is_file())
    def test_detail_reject_confirmation_retains_pdf_accept_removes_all(self):
        row=next(a for a in self.db.favorite_articles() if a['article_key']=='pmid:1');copy=self.db.linked_pdf('pmid:1')
        dialog=ArticleDialog(self.db,row,None,allow_pdf=True)
        with patch('ui.confirm_unfavorite',return_value=False) as confirm:dialog._toggle_favorite();confirm.assert_called_once()
        self.assertTrue(copy.exists());self.assertEqual(len(self.db.favorite_articles()),3)
        with patch('ui.confirm_unfavorite',return_value=True):dialog._toggle_favorite()
        self.assertFalse(copy.exists());self.assertFalse(dialog.open_pdf_button.isEnabled());dialog.deleteLater()
    def test_results_heart_also_confirms_global_cancel(self):
        profile=self.db.save_profile(dict(name='Profile',query_expression='DNA',journal_issns=['0092-8674'],minimum_jif=0,maximum_results=0,sort_mode='最新发表'))
        self.db.set_favorite('pmid:1',True,profile_id=profile)
        page=SimpleNamespace(database=self.db,profile_id=profile,reload_cache=Mock(),cache_changed=SimpleNamespace(emit=Mock()))
        with patch('ui.confirm_unfavorite',return_value=False):ResultsPage.toggle_article_favorite(page,self.rows[0])
        self.assertIn('pmid:1',self.db.favorite_keys_for_profile(profile))
        with patch('ui.confirm_unfavorite',return_value=True):ResultsPage.toggle_article_favorite(page,self.rows[0])
        self.assertNotIn('pmid:1',self.db.favorite_keys_for_profile(profile));self.assertIn('pmid:1',{a['article_key'] for a in self.db.favorite_articles()});page.reload_cache.assert_called_once()
    def test_confirmation_has_cancel_default_and_explicit_pdf_all_groups_warning(self):
        captured=[]
        def execute(dialog):
            captured.append(dialog.text());self.assertEqual(dialog.standardButton(dialog.defaultButton()),QMessageBox.StandardButton.Cancel)
            return QMessageBox.StandardButton.Cancel
        with patch.object(QMessageBox,'exec',execute):self.assertFalse(confirm_unfavorite(None,self.rows[:1]))
        self.assertIn('不再属于任何收藏分组',captured[0]);self.assertIn('paper',captured[0])
    def test_bulk_group_selection_and_rejected_confirmation(self):
        dialog=BulkUnfavoriteDialog(self.db)
        self.assertTrue(dialog.include_pdf.isHidden())
        item=next(dialog.groups.item(i) for i in range(dialog.groups.count()) if dialog.groups.item(i).data(Qt.ItemDataRole.UserRole)=='Alpha')
        item.setCheckState(Qt.CheckState.Checked)
        self.assertEqual(set(dialog.checked(dialog.articles)),{'pmid:1'})
        page=FavoritesPage(self.db)
        with patch('ui.BulkUnfavoriteDialog',return_value=dialog),patch.object(dialog,'exec',return_value=QDialog.DialogCode.Accepted),patch('ui.confirm_unfavorite',return_value=False):page.bulk_unfavorite()
        self.assertIsNone(page.cancel_task);self.assertEqual(len(self.db.favorite_articles()),3)
        with patch('ui.BulkUnfavoriteDialog',return_value=dialog),patch.object(dialog,'exec',return_value=QDialog.DialogCode.Accepted),patch('ui.confirm_unfavorite',return_value=True),patch.object(QMessageBox,'information'):
            page.bulk_unfavorite();QThreadPool.globalInstance().waitForDone(5000);app.processEvents()
        self.assertIsNone(page.cancel_task);self.assertEqual(len(self.db.favorite_articles()),3);self.assertTrue(self.db.linked_pdf('pmid:1'))
        page.deleteLater();dialog.deleteLater();app.processEvents()
    def test_locked_pdf_rolls_back_only_failed_article(self):
        locked=self.db.linked_pdf('pmid:1');original=__import__('local_maintenance').recycle_path
        def unlink(path,*args,**kwargs):
            if path==locked:raise PermissionError('locked')
            return original(path,*args,**kwargs)
        with patch('local_maintenance.recycle_path',unlink):report=self.db.cancel_favorites(['pmid:1','pmid:2'])
        self.assertEqual(report['removed'],['pmid:2']);self.assertEqual(report['failed'][0]['article_key'],'pmid:1')
        self.assertTrue(locked.exists());self.assertTrue(self.db.linked_pdf('pmid:1'))
        self.assertEqual(len(self.db.grouped_favorites()['Beta']),1)
    def test_shared_copy_is_retained_for_other_article(self):
        shared=self.db.linked_pdf('pmid:1')
        with self.db.connect() as c:c.execute('UPDATE article_pdfs SET filename=? WHERE article_key=?',(shared.name,'pmid:2'))
        self.db.set_favorite('pmid:1',False);self.assertTrue(shared.is_file())
        self.db.set_favorite('pmid:2',False);self.assertFalse(shared.exists())
    def test_bulk_is_blocked_while_pdf_selection_is_open(self):
        page=FavoritesPage(self.db);page.pdf_import_dialog=object()
        with patch.object(QMessageBox,'information') as notice:page.bulk_unfavorite();notice.assert_called_once()
        self.assertIsNone(page.cancel_task);self.assertEqual(len(self.db.favorite_articles()),3)
        page.pdf_import_dialog=None;page.deleteLater();app.processEvents()

def artifact(data=b'verified installer',version='2.2',key='windows-x64'):
    filename=f'BiomedReader_Setup_{version}_x64.exe' if key=='windows-x64' else f'BiomedReader_{version}_macOS12_'+('AppleSilicon' if key=='macos-arm64' else 'Intel')+'.dmg'
    repo='Biomedreader' if key=='windows-x64' else 'Biomedreader_macos'
    return dict(filename=filename,url=f'https://github.com/amiba-xqq/{repo}/releases/download/v{version}/{filename}',bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
def response(data,status=200):
    value=Mock();value.status_code=status;value.headers={};value.iter_content.return_value=iter([data]);return value

class UpdateTests(unittest.TestCase):
    def test_versions_platforms_and_rossetta(self):
        self.assertGreater(version_numbers('2.10'),version_numbers('2.9'))
        self.assertEqual(version_numbers('2.1'),version_numbers('2.1.0.0'))
        self.assertEqual(platform_package('Windows','AMD64'),'windows-x64');self.assertEqual(platform_package('Darwin','arm64'),'macos-arm64')
        with patch('software_update.platform.system',return_value='Darwin'),patch('software_update.subprocess.check_output',return_value='1\n'):
            self.assertEqual(platform_package('Darwin','x86_64'),'macos-arm64')
        with patch('software_update.platform.system',return_value='Windows'):self.assertEqual(platform_package('Darwin','x86_64'),'macos-intel')
        with self.assertRaises(ValueError):version_numbers('2.1-beta')
    def test_current_version_and_new_version_from_site_manifest(self):
        for latest,newer in [('2.1',False),('2.2',True)]:
            data=dict(schema=1,application='BiomedReader',version=latest,packages={'windows-x64':artifact(version=latest)})
            session=Mock();session.get.return_value=response(json.dumps(data).encode())
            result=check_release('2.1','Windows','AMD64',session)
            self.assertEqual(result['newer'],newer);self.assertEqual(session.get.call_args.args[0],MANIFEST_URL)
            self.assertFalse(session.get.call_args.kwargs['allow_redirects'])
    def test_reject_wrong_origin_architecture_and_hash(self):
        for value in ({**artifact(),'url':'https://example.com/package.exe'},{**artifact(),'sha256':'bad'},
            {**artifact(key='macos-intel')}):
            with self.assertRaises(ValueError):validate_artifact(value,'2.2','windows-x64')
    def test_download_integrity_cancel_and_atomic_overwrite(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            target=Path(temporary)/'update.exe';target.write_bytes(b'old');data=b'verified installer'
            session=Mock();session.get.return_value=response(data)
            progress=[];download_artifact(artifact(data),target,lambda p,m:progress.append(p),session=session)
            self.assertEqual(target.read_bytes(),data);self.assertEqual(progress[-1],100)
            target.write_bytes(b'old');session.get.return_value=response(b'wrong')
            with self.assertRaises(ValueError):download_artifact(artifact(data),target,session=session)
            self.assertEqual(target.read_bytes(),b'old');self.assertFalse(list(Path(temporary).glob('*.part')))
            stop=[False];resp=response(data);resp.iter_content.return_value=iter([data[:3],data[3:]]);session.get.return_value=resp
            with self.assertRaises(UpdateCancelled):download_artifact(artifact(data),target,lambda p,m:stop.__setitem__(0,True),lambda:stop[0],session)
            self.assertEqual(target.read_bytes(),b'old');self.assertFalse(list(Path(temporary).glob('*.part')))
    def test_untrusted_redirect_never_requested(self):
        session=Mock();resp=response(b'',302);resp.headers={'Location':'http://127.0.0.1/private'};session.get.return_value=resp
        with self.assertRaises(ValueError):asset_response(session,artifact()['url'])
        self.assertEqual(session.get.call_count,1)
    def test_update_widget_reports_latest_without_download(self):
        widget=SoftwareUpdateWidget()
        with patch('software_update.check_release',return_value=dict(current='2.1',latest='2.1',newer=False,artifact=None)),patch.object(QMessageBox,'information') as notice,patch('software_update.download_artifact') as download:
            widget.check();deadline=time.monotonic()+10
            while widget.task is not None and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
            self.assertIsNone(widget.task);notice.assert_called_once();download.assert_not_called()
            self.assertIn('最新版本',widget.status.text())
        widget.deleteLater();app.processEvents()
    def test_newer_version_widget_selects_save_path_and_starts_download(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            assert Path(temporary).resolve().is_relative_to(Path(__file__).resolve().parent)
            target=Path(temporary)/'update.exe';widget=SoftwareUpdateWidget()
            report=dict(current='2.1',latest='2.2',newer=True,artifact=artifact())
            with patch('software_update.check_release',return_value=report),patch('software_update.QFileDialog.getSaveFileName',return_value=(str(target),'')) as save,patch('software_update.download_artifact',return_value=str(target)) as download,patch.object(QMessageBox,'information') as notice:
                widget.check();deadline=time.monotonic()+10
                while widget.task is not None and time.monotonic()<deadline:
                    QThreadPool.globalInstance().waitForDone(1000);app.processEvents()
                self.assertIsNone(widget.task);download.assert_called_once();save.assert_called_once()
                self.assertIn('BiomedReader_Setup_2.2_x64.exe',save.call_args.args[2]);self.assertEqual(download.call_args.args[1],str(target))
                notice.assert_called_once();self.assertIn('下载完成',widget.status.text())
            widget.deleteLater();app.processEvents()

if __name__=='__main__':unittest.main()
