"""Portable citation caching, PDF parsing and independent drop-window regressions."""
import tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import test_hidden_articles as shared
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import QDialog,QWidget
from bibliography import enrich_articles
from citations import show_citation,render
from pdf_import import recognize_pdf
from ui import FavoritesPage

def make_pdf(path):
    stream=b'BT /F1 22 Tf 60 720 Td (A complete scientific article title) Tj 0 -26 Td (continuing on its second line) Tj /F1 10 Tf 0 -60 Td (Abstract: examples) Tj ET'
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    data=b'%PDF-1.4\n';offsets=[]
    for index,obj in enumerate(objects,1):offsets.append(len(data));data+=str(index).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    position=len(data);data+=b'xref\n0 6\n0000000000 65535 f \n'
    for offset in offsets:data+=f'{offset:010d} 00000 n \n'.encode()
    data+=b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(position).encode()+b'\n%%EOF'
    path.write_bytes(data)

class CitationPDFTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=shared.QApplication.instance() or shared.QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.temp.cleanup)
        self.db=shared.Database(Path(self.temp.name)/'library.sqlite3');self.pdf=Path(self.temp.name)/'sample.pdf';make_pdf(self.pdf)
        self.group,_=self.db.create_favorite_group('PDF','PDF')
        self.row=dict(article_key='pmid:42',pmid='42',doi='',title='Example paper',authors='Author A; Author B',
            journal_title='Cell',publication_date='2025-01-01',volume='86',issue='18',pages='10-20',source_name='PubMed')
        self.db.save_pdf_favorite(self.row,self.pdf,self.group)
    def test_wrapped_pdf_title(self):
        self.assertEqual(recognize_pdf(self.pdf)['title'],'A complete scientific article title continuing on its second line')
    def test_missing_metadata_retries_each_explicit_request(self):
        row={**self.row,'volume':'','pages':''}
        with self.db.connect() as conn:conn.execute("UPDATE articles SET volume='',pages='' WHERE article_key=?",(row['article_key'],))
        self.db.save_bibliography(row['article_key'],{'issue':'18'})
        client=Mock();client.pubmed_request.return_value.content=b'<PubmedArticleSet/>'
        with patch('bibliography.time.sleep'):
            enrich_articles(self.db,[row],client);client.pubmed_request.assert_not_called()
            for _ in range(2):enrich_articles(self.db,[row],client,force_missing=True)
        self.assertEqual(client.pubmed_request.call_count,2)
    def test_complete_record_uses_cache_even_with_old_snapshot(self):
        client=Mock();old={**self.row,'volume':'','pages':''}
        result,_=enrich_articles(self.db,[old],client,force_missing=True)
        client.pubmed_request.assert_not_called();self.assertEqual(result[0]['pages'],'10-20')
        self.assertIn('86',render(result,'Cell')['references'][0])
        QTimer.singleShot(20,lambda:[widget.reject() for widget in self.app.topLevelWidgets() if isinstance(widget,QDialog)])
        with patch('bibliography.enrich_articles') as request:show_citation(self.db,old,None);request.assert_not_called()
    def test_independent_nonmodal_pdf_selection_window(self):
        parent=QWidget();parent.show();page=FavoritesPage(self.db);page.setParent(parent)
        page.start_pdf_import();dialog=page.pdf_import_dialog;self.app.processEvents()
        self.assertIsNone(dialog.parentWidget());self.assertEqual(dialog.windowModality(),Qt.WindowModality.NonModal)
        self.assertTrue(parent.isEnabled());parent.showMinimized();self.app.processEvents()
        self.assertTrue(dialog.isVisible());self.assertFalse(dialog.isMinimized())
        dialog.reject();self.app.processEvents();self.assertIsNone(page.pdf_import_dialog)
        parent.close();parent.deleteLater()
