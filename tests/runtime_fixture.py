"""Isolated shared fixtures; test encryption never opens a real user Keychain."""
import tempfile
from pathlib import Path
from unittest.mock import patch
from cryptography.fernet import Fernet
import test_hidden_articles
from PySide6.QtCore import Qt,QCoreApplication,QEvent
from PySide6.QtWidgets import QApplication,QDialog
from database import Database
QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
app=QApplication.instance() or QApplication([])
app.setQuitOnLastWindowClosed(False)

def article(key,doi=None):return dict(article_key=key,pmid=key.split(':')[-1],doi=doi or '',pmcid='',title='DNA repair research article '+key,abstract='Example complete abstract',authors='Author A',journal_title='Cell',issn='0092-8674',publication_date='2026-10-01',publication_year=2026,source_name='PubMed',volume='1',pages='1-10',cited_by_count=0,is_open_access=0,landing_page_url='',graphical_abstract_url='')
class Runtime22Tests:
    def setUp(self):
        self.cipher=Fernet(Fernet.generate_key());self.protection=patch('ai_store._protect',side_effect=lambda value,decrypt=False:self.cipher.decrypt(value) if decrypt else self.cipher.encrypt(value));self.protection.start()
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.root=Path(self.temp.name).resolve();self.db=Database(self.root/'biomed_literature.sqlite3')
        self.group,_=self.db.create_favorite_group('Test','DNA');self.row=article('pmid:1');self.pdf=self.root/'original.pdf';self.pdf.write_bytes(b'%PDF-1.4\nTest original\n%%EOF')
        self.profile=self.db.save_profile(dict(name='Topic',query_expression='DNA',journal_issns=['0092-8674'],minimum_jif=0,maximum_results=0,sort_mode='最新发表',search_sources='pubmed'))
        # Registered before test-specific cleanups, so it runs last in LIFO
        # order and keeps their widgets/database valid until they finish.
        self.addCleanup(Runtime22Tests.cleanup,self)
    def tearDown(self):
        app.processEvents()
    def cleanup(self):
        try:
            # An assertion must not leave modal windows attached to a database
            # which this fixture is about to remove.
            for widget in app.topLevelWidgets():
                manager=getattr(widget,'manager',None)
                if getattr(getattr(manager,'store',None),'database',None) is self.db:
                    manager.abandon_owner(widget)
                    coordinator=getattr(widget,'coordinator',None);cancel=getattr(coordinator,'cancel',None)
                    if callable(cancel) and getattr(widget,'profile_id',None) in getattr(coordinator,'running',()):cancel(widget.profile_id)
                    widget.approved=True;widget._closing_approved=True
                    if isinstance(widget,QDialog):QDialog.done(widget,0)
                    widget.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
            app.processEvents();self.temp.cleanup()
        finally:self.protection.stop()
