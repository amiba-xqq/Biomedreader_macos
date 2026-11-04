"""Long research replies: visible evidence docking and local message times."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from pathlib import Path
import shiboken6
from PySide6.QtCore import Qt, QPoint, QPointF, QUrl
from PySide6.QtGui import QWheelEvent
from runtime_fixture import Runtime22Tests, app
from ai_ui import AIManager
from ai_finder_ui import FinderChat
from finder_presentation import message_time


class Tests(unittest.TestCase):
    setUp = Runtime22Tests.setUp
    tearDown = Runtime22Tests.tearDown

    def drain(self):
        for _ in range(16):
            app.processEvents()

    def chat(self):
        self.manager = AIManager(self.db)
        rows = [dict(self.row, article_key=f'time-dock-{i}',
                     title=f'[{i+1}] Molecular mechanisms of transcription and replication conflict',
                     journal_title='Synthetic research journal') for i in range(9)]
        now = datetime.now().astimezone()
        messages = [dict(role='user', content='转录复制冲突：哪些方法能区分因果关系？', created_at=now.isoformat()),
                    dict(role='assistant', model='Fixture model', created_at=now.isoformat(),
                         content=('从领域内尚未解决的问题出发，逐步比较证据、机制和方法。先明确因果推断的边界，再考虑如何验证。 [3]\n\n'*65),
                         references=[dict(article_key=a['article_key'], number=i+1) for i,a in enumerate(rows)]),
                    dict(role='user', content='请比较这些方法的实验限制。', created_at=now.isoformat()),
                    dict(role='assistant', content='短回复也应保留正常的文献栏。 [1]', created_at=now.isoformat(),
                         references=[dict(article_key=rows[0]['article_key'], number=1)])]
        identifier = self.manager.repository.save_history('finder', '科研方法与证据', dict(messages=messages), {})
        self.manager.repository.replace_finder_cards(identifier, rows)
        d = FinderChat(self.manager, self.manager.repository.history(identifier))
        d.resize(1260, 850);d.show();self.drain()
        self.addCleanup(lambda: (setattr(d,'approved',True),d.close()) if shiboken6.isValid(d) else None)
        return d

    def test_timestamp_calendar_boundaries_and_legacy(self):
        current = datetime(2026,11,3,20,30).astimezone()
        examples = [('2026-11-03T19:10:00','19:10'),
                    ('2026-11-02T19:10:00','2日 19:10'),
                    ('2026-10-02T19:10:00','10月2日 19:10'),
                    ('2025-11-03T19:10:00','2025年11月3日 19:10')]
        for value, expected in examples:
            self.assertEqual(message_time(value, current)[0], expected)
        instant = datetime(2026,11,3,19,10).astimezone()
        self.assertEqual(message_time(instant.astimezone(timezone.utc).isoformat(),current)[0], '19:10')
        self.assertEqual(message_time('',current)[0], '时间未记录')
        self.assertEqual(message_time(None,current)[0], '时间未记录')
        self.assertEqual(message_time(instant.isoformat(),current.replace(day=4))[0], '3日 19:10')
        self.assertEqual(message_time(instant.isoformat(),current.replace(year=2027,month=1,day=1))[0], '2026年11月3日 19:10')

    def test_timestamps_update_and_roundtrip_without_provider_calls(self):
        d = self.chat();original = d.messages[0]['created_at']
        tomorrow = datetime.now().astimezone() + timedelta(days=1)
        with patch('ai_finder_ui.datetime') as clock:
            clock.now.return_value = tomorrow
            d.message_clock.timeout.emit()
        self.assertEqual(d.message_frames[0][0].message_time.text(),message_time(original,tomorrow)[0])
        for frame, _, _ in d.message_frames:
            self.assertTrue(frame.message_time.isVisible())
            self.assertLess(abs(frame.message_time.geometry().right()-(frame.width()-21)),4)
        d.persist();saved = self.manager.repository.history(d.identifier)
        self.assertEqual(saved['inputs']['messages'][0]['created_at'],original)
        d.edit_message(0);d.cancel_edit();self.drain()
        self.assertEqual(d.messages[0]['created_at'],original)

    def test_user_send_and_assistant_completion_record_local_instants(self):
        d = self.chat();d.models.addItem('Fixture','deepseek');d.models.setCurrentIndex(d.models.count()-1)
        self.manager.store.config = Mock(return_value=dict(enabled=True,model='fixture'))
        self.manager.submit = Mock(return_value='fixture-job')
        before = datetime.now().astimezone()
        d.input.setPlainText('怎样区分因果关系？')
        with patch('ai_finder_ui.AnalysisEngine',return_value=Mock()):d.send()
        submitted = datetime.fromisoformat(d.messages[-1]['created_at'])
        self.assertGreaterEqual(submitted,before)
        d.completed(dict(text='这是科研文献核查后的最终回答。', model='fixture',articles=[],created_at='1900-01-01T00:00:00'))
        finished = datetime.fromisoformat(d.messages[-1]['created_at'])
        self.assertGreaterEqual(finished,submitted)
        self.assertLessEqual(finished,datetime.now().astimezone())
        saved = self.manager.repository.history(d.identifier)
        self.assertEqual(saved['inputs']['messages'][-1]['created_at'],d.messages[-1]['created_at'])
        self.drain()

    def test_evidence_follows_long_reply_and_citation_is_visible_at_top(self):
        d = self.chat();frame = d.message_frames[1][0];pane = frame.evidence_scroll
        dock = frame.evidence_dock;outer = d.scroll.verticalScrollBar()
        top = dock.mapTo(d.content,QPoint()).y()
        self.assertGreater(dock.height(),d.scroll.viewport().height()*3)
        for offset in (400,1300,700):
            outer.setValue(top+offset);self.drain()
            self.assertAlmostEqual(pane.mapTo(d.scroll.viewport(),QPoint()).y(),0,delta=1)
            self.assertGreaterEqual(pane.height(),100)
            before = outer.value();d.follow_reference(frame,QUrl('biomed-ref:3'));self.drain()
            self.assertEqual(outer.value(),before)
            card = frame.reference_cards['3']
            self.assertAlmostEqual(card.mapTo(d.scroll.viewport(),QPoint()).y(),8,delta=2)
            self.assertTrue(hasattr(card,'_highlight'))
        path = Path(__file__).with_name('finder-sticky-time-preview.png')
        d.grab().save(str(path))
        # The floating pane stays entirely inside its reply at the lower edge.
        outer.setValue(top+dock.height()-100);self.drain()
        self.assertLessEqual(pane.y()+pane.height(),dock.height())
        self.assertAlmostEqual(pane.mapTo(d.scroll.viewport(),QPoint()).y(),0,delta=1)
        d.resize(900,650);self.drain();d.reflow_messages();self.drain()
        self.assertEqual(pane.width(),dock.width())

    def test_sticky_evidence_wheel_is_independent_and_text_wheel_follows(self):
        d = self.chat();frame,browser,_ = d.message_frames[1]
        outer = d.scroll.verticalScrollBar();pane = frame.evidence_scroll
        outer.setValue(frame.evidence_dock.mapTo(d.content,QPoint()).y()+600);self.drain()
        inner = pane.verticalScrollBar();inner.setValue(0);before = outer.value()
        def wheel(target):
            event = QWheelEvent(QPointF(20,20),QPointF(20,20),QPoint(),QPoint(0,-120),
                                Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
            app.sendEvent(target,event);self.drain()
        wheel(pane.viewport())
        self.assertGreater(inner.value(),0);self.assertEqual(outer.value(),before)
        wheel(browser.viewport())
        self.assertGreater(outer.value(),before)
        self.assertAlmostEqual(pane.mapTo(d.scroll.viewport(),QPoint()).y(),0,delta=1)
        # Geometry and message position survive rebuilding and reopening.
        d.view_state=d.capture_view();d.persist();expected=outer.value();identifier=d.identifier
        reopened=FinderChat(self.manager,self.manager.repository.history(identifier));reopened.show();self.drain()
        self.addCleanup(lambda:(setattr(reopened,'approved',True),reopened.close()))
        self.assertAlmostEqual(reopened.scroll.verticalScrollBar().value(),expected,delta=5)
        restored=reopened.message_frames[1][0].evidence_scroll
        self.assertAlmostEqual(restored.mapTo(reopened.scroll.viewport(),QPoint()).y(),0,delta=1)


if __name__=='__main__':unittest.main(defaultTest='Tests')
