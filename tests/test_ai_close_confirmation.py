import unittest,time
from types import SimpleNamespace
from unittest.mock import Mock,patch
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox,QPushButton
from runtime_fixture import Runtime22Tests,app
from test_ai_window_close import Blocked
from test_ai_cancellation_progress import until
from ai_ui import AIManager
from ai_workflow_ui import SummaryDialog,ScientificQuestionsDialog,AIReportDialog
from expert_ui import KnowledgeDialog,ReviewerDialog
from ai_finder_ui import FinderChat


class Tests(unittest.TestCase):
    setUp=Runtime22Tests.setUp
    tearDown=Runtime22Tests.tearDown
    def manager(self):
        manager=AIManager(self.db);self.db.store_refresh_results(self.profile,[(self.row,1)],'pubmed');return manager
    def answer(self,action,accept):
        observed=[]
        def choose():
            box=app.activeModalWidget()
            if not isinstance(box,QMessageBox):observed.append(None);return
            observed.append(dict(title=box.windowTitle(),text=box.text(),default=box.defaultButton().text(),escape=box.escapeButton().text()))
            next(button for button in box.buttons() if button.text()==('确认关闭' if accept else '继续分析')).click()
        QTimer.singleShot(0,choose);value=action()
        self.assertEqual(len(observed),1);self.assertIsNotNone(observed[0]);self.assertEqual(observed[0]['title'],'中断AI任务并关闭')
        self.assertIn('中断',observed[0]['text']);self.assertIn('保存的结果会保留',observed[0]['text']);self.assertEqual(observed[0]['default'],'继续分析');self.assertEqual(observed[0]['escape'],'继续分析')
        return value
    def test_summary_and_all_assistant_windows_require_confirmation_for_both_close_controls(self):
        manager=self.manager();old=dict(text='Saved answer',provider='deepseek',model='fixture');key=self.row['article_key']
        manager.repository.save_summary(key,old,'profile:'+str(self.profile))
        for kind in ('summary','knowledge','questions','reviewer','finder'):
            for footer in (False,True) if kind!='finder' else (False,):
                with self.subTest(kind=kind,footer=footer):
                    if kind=='summary':dialog=SummaryDialog(manager,self.row,'profile:'+str(self.profile))
                    elif kind=='finder':
                        i=manager.repository.save_history('finder','Saved chat',dict(messages=[dict(role='assistant',content='Saved answer')]),old);dialog=FinderChat(manager,manager.repository.history(i))
                    else:dialog={'knowledge':KnowledgeDialog,'questions':ScientificQuestionsDialog,'reviewer':ReviewerDialog}[kind](manager)
                    dialog.show();blocked=Blocked()
                    if kind=='finder':dialog.task=manager.submit(dialog,blocked,'completed','failed')
                    else:dialog.start(blocked)
                    until(blocked.entered.is_set);identifier=dialog.task
                    action=next(b for b in dialog.findChildren(QPushButton) if b.text()=='关闭').click if footer else dialog.close
                    self.answer(action,False)
                    self.assertTrue(dialog.isVisible());self.assertEqual(dialog.task,identifier);self.assertFalse(dialog.stop.is_set());self.assertIn(identifier,manager.jobs);self.assertFalse(manager.jobs[identifier][0].cancellation.cancelled());self.assertFalse(blocked.aborted.is_set())
                    start=time.monotonic();self.answer(action,True);self.assertLess(time.monotonic()-start,1)
                    self.assertNotIn(identifier,manager.jobs);until(blocked.aborted.is_set);blocked.release.set();until(blocked.finished.is_set);app.processEvents()
                    self.assertEqual(manager.repository.summary(key)['text'],old['text'])
    def test_translation_only_also_waits_for_confirmation(self):
        manager=self.manager();dialog=SummaryDialog(manager,self.row,'profile:'+str(self.profile));dialog.show();blocked=Blocked()
        controller=dialog.language_control;controller.task=manager.submit(controller,blocked,'done','failed');until(blocked.entered.is_set);identifier=controller.task
        self.answer(dialog.close,False);self.assertIn(identifier,manager.jobs);self.assertFalse(blocked.aborted.is_set())
        self.answer(dialog.close,True);self.assertNotIn(identifier,manager.jobs);blocked.release.set();until(blocked.finished.is_set);app.processEvents()
    def test_report_continue_does_not_cancel_or_restore_until_confirmed(self):
        manager=self.manager();report=dict(keys=[self.row['article_key']],window={},errors={})
        coordinator=SimpleNamespace(manager=manager,running={self.profile},drafts={self.profile:report},changed=manager.changed,window=SimpleNamespace(settings_page=None),cancel=Mock(),restore_summaries=Mock())
        dialog=AIReportDialog(coordinator,self.profile);dialog.show();self.answer(dialog.close,False)
        coordinator.cancel.assert_not_called();coordinator.restore_summaries.assert_not_called();self.assertIs(coordinator.drafts[self.profile],report);self.assertTrue(dialog.isVisible())
        self.answer(dialog.close,True);coordinator.cancel.assert_called_once_with(self.profile);coordinator.restore_summaries.assert_called_once_with(self.profile);self.assertNotIn(self.profile,coordinator.drafts);app.processEvents()
    def test_idle_window_keeps_existing_save_question_without_interrupt_question(self):
        manager=self.manager();dialog=SummaryDialog(manager,self.row,'profile:'+str(self.profile));dialog.display(dict(text='Completed result'));dialog.show()
        with patch('ai_workflow_ui.confirm_ai_close',side_effect=AssertionError('Idle close must not ask to interrupt')),patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Yes) as question:
            self.assertTrue(dialog.close())
        self.assertEqual(question.call_count,1);self.assertEqual(manager.repository.summary(self.row['article_key'])['text'],'Completed result');app.processEvents()
    def test_task_finishes_normally_while_user_is_deciding_to_continue(self):
        manager=self.manager();dialog=SummaryDialog(manager,self.row,'profile:'+str(self.profile));dialog.show();blocked=Blocked(dict(text='Finished while deciding',provider='deepseek',model='fixture'));dialog.start(blocked);until(blocked.entered.is_set)
        seen=[]
        def decide():
            box=app.activeModalWidget();seen.append(box.windowTitle());blocked.release.set()
            QTimer.singleShot(120,lambda:next(button for button in box.buttons() if button.text()=='继续分析').click())
        QTimer.singleShot(0,decide);self.assertFalse(dialog.close());until(lambda:dialog.task is None)
        self.assertTrue(dialog.isVisible());self.assertEqual(dialog.result['text'],'Finished while deciding');self.assertFalse(blocked.aborted.is_set());self.assertEqual(seen,['中断AI任务并关闭'])
        with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Yes):dialog.close()
        self.assertEqual(manager.repository.summary(self.row['article_key'])['text'],'Finished while deciding');app.processEvents()


if __name__=='__main__':unittest.main(defaultTest='Tests')
