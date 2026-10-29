"""Offline tests shared by the Windows build and both native macOS builds."""
import os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt,QThreadPool
from PySide6.QtWidgets import QApplication
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
from database import Database
from literature import LiteratureClient
from ui import MainWindow,ArticleCard,ArticleDialog
from data_backup import default_data_directory

class HiddenArticleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.temporary.cleanup)
        self.db=Database(Path(self.temporary.name)/'biomed_literature.sqlite3')
        self.profile=self.db.save_profile(dict(name='DNA',query_expression='DNA',journal_issns=['0092-8674'],scope_ids=['builtin_nc_selected'],minimum_jif=0,maximum_results=10,sort_mode='最新发表',search_sources='pubmed'))
        article,score=LiteratureClient('').normalize_biorxiv(dict(doi='10.1101/2026.01.01.123456',title='DNA repair',abstract='Complete cached abstract',authors='A',date='2026-01-01',version='1'),'DNA')
        article.update(source_name='PubMed',journal_title='Cell',issn='0092-8674',pmid='12345',publication_types=['Journal Article'])
        self.article=article;self.rows=[(article,score)]
        self.db.store_refresh_results(self.profile,self.rows,'pubmed')
    def test_hidden_state_is_profile_local_and_persists_across_refresh(self):
        other=self.db.save_profile(dict(name='RNA',query_expression='RNA',journal_issns=['0092-8674'],scope_ids=['builtin_nc_selected'],minimum_jif=0,maximum_results=10,sort_mode='最新发表',search_sources='pubmed'))
        self.db.set_favorite(self.article['article_key'],True,profile_id=self.profile)
        self.db.set_article_hidden(self.profile,self.article,True)
        self.assertTrue(self.db.matches_hidden_article(self.article,self.db.hidden_article_tokens(self.profile)))
        self.assertFalse(self.db.matches_hidden_article(self.article,self.db.hidden_article_tokens(other)))
        self.db.store_refresh_results(self.profile,self.rows,'pubmed')
        reopened=Database(self.db.path)
        self.assertTrue(reopened.matches_hidden_article(self.article,reopened.hidden_article_tokens(self.profile)))
        self.assertEqual(reopened.result_count(self.profile,'pubmed'),1)
        self.assertEqual(len(reopened.favorite_articles()),1)
    def test_doi_identity_survives_changed_article_key_and_unhides(self):
        self.db.set_article_hidden(self.profile,self.article,True)
        changed={**self.article,'article_key':'pmid:12345','doi':self.article['doi'].upper()}
        self.assertTrue(self.db.matches_hidden_article(changed,self.db.hidden_article_tokens(self.profile)))
        self.db.set_article_hidden(self.profile,changed,False)
        self.assertFalse(self.db.hidden_article_tokens(self.profile))
    def test_show_hidden_setting_restores_table_and_card_actions(self):
        window=MainWindow(self.db);window.open_profile(self.profile);page=window.results
        self.assertEqual(page.table.rowCount(),1)
        page.toggle_article_hidden(self.article)
        self.assertEqual(page.table.rowCount(),0);self.assertEqual(self.db.result_count(self.profile,'pubmed'),1)
        page.set_show_hidden(True)
        self.assertEqual(page.table.rowCount(),1)
        self.assertTrue(page.displayed_articles[0]['is_hidden'])
        page.view_mode.setCurrentIndex(page.view_mode.findData('cards'))
        cards=page.findChildren(ArticleCard);card=next(c for c in cards if c.article['article_key']==self.article['article_key'] and not c.isHidden())
        self.assertEqual(card.hidden_button.text(),'取消隐藏')
        card.hidden_button.click();page.set_show_hidden(False)
        self.assertEqual(page.table.rowCount(),1)
        window.close();window.deleteLater();self.app.processEvents()
    def test_detail_window_preserves_text_without_duplicate_hide_button(self):
        dialog=ArticleDialog(self.db,self.article,profile_id=self.profile)
        self.assertIn('Complete cached abstract',dialog.browser.toPlainText())
        self.assertFalse(hasattr(dialog,'hidden_button'))
        dialog.close();dialog.deleteLater();self.app.processEvents()
    def test_macos_default_data_location(self):
        with patch('data_backup.sys.platform','darwin'):
            self.assertEqual(default_data_directory(),Path.home()/'Library/Application Support/OrangeReader/BiomedLiterature')
    def test_deleting_profile_removes_its_hidden_markers(self):
        self.db.set_article_hidden(self.profile,self.article,True)
        self.db.delete_profile(self.profile)
        self.assertFalse(self.db.hidden_article_tokens(self.profile))

if __name__=='__main__':unittest.main(verbosity=2)

