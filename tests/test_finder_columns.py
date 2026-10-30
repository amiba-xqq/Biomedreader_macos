import json
import unittest
import shiboken6
from pathlib import Path
from unittest.mock import patch
import test_hidden_articles as fixtures
from PySide6.QtWidgets import QApplication
app=QApplication.instance() or QApplication([])
from ai_ui import AIManager
from ai_finder_ui import FinderChat
from PySide6.QtCore import QPoint,QPointF,Qt,QUrl,QMimeData
from PySide6.QtGui import QWheelEvent,QDragEnterEvent,QDropEvent
from PySide6.QtWidgets import QMessageBox,QLabel,QPushButton
from finder_presentation import citation_html


class Tests(unittest.TestCase):
    def setUp(self):
        fixtures.HiddenArticleTests.setUp(self)
        self.row=self.article
        self.group,_=self.db.create_favorite_group("Research fixture", "DNA")
    def tearDown(self):app.processEvents()

    def drain(self):
        for _ in range(12):app.processEvents()

    def chat(self):
        self.manager=AIManager(self.db)
        rows=[dict(self.row,article_key='finder-'+str(i),title='DNA replication research and experimental evidence '+str(i),abstract='PRIVATE ABSTRACT') for i in range(8)]
        messages=[dict(role='user',content='我研究转录复制冲突，希望比较不同的验证方法。'),
                  dict(role='assistant',content=('先明确研究问题：转录和复制过程中哪些因素导致冲突？在设计实验之前，需要区分相关性和因果关系。[1]\n\n'*15),
                       references=[dict(article_key=a['article_key'],number=i+1) for i,a in enumerate(rows)])]
        identifier=self.manager.repository.save_history('finder','Synthetic research',dict(messages=messages),{})
        self.manager.repository.replace_finder_cards(identifier,rows)
        d=FinderChat(self.manager,self.manager.repository.history(identifier));d.resize(1250,800);d.show();self.drain()
        self.addCleanup(lambda: (setattr(d,'approved',True),d.close() if shiboken6.isValid(d) else None))
        return d

    def test_columns_links_card_feedback_and_independent_wheel(self):
        d=self.chat();frame,browser,_=d.message_frames[1];sizes=frame.columns.sizes()
        self.assertAlmostEqual(sizes[1]/sum(sizes),.25,delta=.035)
        self.assertGreater(browser.height(),browser.document().documentLayout().documentSize().height())
        self.assertNotIn('PRIVATE ABSTRACT',' '.join(l.text() for l in frame.evidence_scroll.findChildren(QLabel)))
        self.assertIn('biomed-ref:1',browser.toHtml())
        outer=d.scroll.verticalScrollBar();inner=frame.evidence_scroll.verticalScrollBar();self.assertGreater(inner.maximum(),0)
        before=outer.value();d.follow_reference(frame,QUrl('biomed-ref:8'));self.drain()
        self.assertGreater(inner.value(),0);self.assertEqual(outer.value(),before);self.assertTrue(hasattr(frame.reference_cards['8'],'_highlight'))
        inner.setValue(0)
        wheel=QWheelEvent(QPointF(30,30),QPointF(30,30),QPoint(0,0),QPoint(0,-120),Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
        app.sendEvent(frame.evidence_scroll.viewport(),wheel);self.drain()
        self.assertGreater(inner.value(),0);self.assertEqual(outer.value(),before)

    def test_saved_layout_and_scroll_are_restored(self):
        d=self.chat();frame=d.message_frames[1][0];frame.columns.setSizes([500,400]);d.columns_moved(1);self.drain()
        d.scroll.verticalScrollBar().setValue(300);frame.evidence_scroll.verticalScrollBar().setValue(100)
        d.composer_splitter.setSizes([650,0]);self.drain();d.view_state=d.capture_view();d.persist()
        expected=d.scroll.verticalScrollBar().value();ratio=d.messages[1]['text_ratio'];identifier=d.identifier;d.approved=True;d.close()
        reopened=FinderChat(self.manager,self.manager.repository.history(identifier));reopened.show();self.drain()
        self.addCleanup(lambda:(setattr(reopened,'approved',True),reopened.close()))
        self.assertAlmostEqual(reopened.scroll.verticalScrollBar().value(),expected,delta=5)
        self.assertAlmostEqual(reopened.messages[1]['text_ratio'],ratio,delta=.02)
        self.assertEqual(reopened.composer_splitter.sizes()[1],0)
        self.assertAlmostEqual(reopened.message_frames[1][0].evidence_scroll.verticalScrollBar().value(),100,delta=3)

    def test_deleting_output_removes_evidence_without_touching_favorites(self):
        d=self.chat();d.notes={a['article_key']:dict(text='PRIVATE NOTE') for a in d.articles};d.persist()
        with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Yes):d.delete_message(1)
        saved=self.manager.repository.history(d.identifier)
        self.assertEqual(len(saved['inputs']['messages']),1)
        self.assertEqual(saved['inputs']['notes'],{});self.assertEqual(saved['result']['text'],'')
        self.assertEqual(self.manager.repository.finder_cards(d.identifier),[])
        self.assertNotIn('PRIVATE NOTE',json.dumps(saved))

    def test_atomic_collection_protects_real_favorite_and_other_history(self):
        d=self.chat();self.db.save_pdf_favorite(self.row,None,self.group,link_pdf=False)
        d.manager.repository.replace_finder_cards(d.identifier,[self.row])
        d.manager.repository.replace_finder_conversation(d.identifier,dict(messages=[]),{},[])
        with self.db.connect() as c:
            self.assertIsNotNone(c.execute('SELECT article_key FROM articles WHERE article_key=?',(self.row['article_key'],)).fetchone())
            self.assertEqual(c.execute('SELECT is_favorite FROM favorites WHERE article_key=?',(self.row['article_key'],)).fetchone()[0],1)

    def test_citations_are_local_and_model_html_is_escaped(self):
        html=citation_html('<script>unsafe</script> [1] [2]',[dict(number=1)])
        self.assertIn('&lt;script&gt;',html);self.assertNotIn('<script>',html)
        self.assertIn('biomed-ref:1',html);self.assertNotIn('biomed-ref:2',html)

    def test_reviewer_drop_adds_local_paths_and_deduplicates(self):
        from expert_ui import ReviewerDialog
        manager=AIManager(self.db);reviewer=ReviewerDialog(manager);reviewer.show();self.drain()
        self.addCleanup(lambda:(setattr(reviewer,'approved',True),reviewer.close()))
        pdf=Path(self.db.path).parent/'Synthetic manuscript.pdf';pdf.write_bytes(b'%PDF-1.7\nSynthetic fixture')
        mime=QMimeData();mime.setUrls([QUrl.fromLocalFile(str(pdf))])
        target=reviewer.manuscript_drop
        enter=QDragEnterEvent(QPoint(15,15),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        target.dragEnterEvent(enter);self.assertTrue(enter.isAccepted())
        event=QDropEvent(QPointF(15,15),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        target.dropEvent(event);target.add_paths([str(pdf)]);self.assertEqual(reviewer.files,[str(pdf.resolve())]);self.assertEqual(target.count(),1)


if __name__=='__main__':unittest.main()
