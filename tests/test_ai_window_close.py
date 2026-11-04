import threading,time,unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from runtime_fixture import Runtime22Tests,article,app
from test_ai_cancellation_progress import until
from ai_ui import AIManager,RecommendationDialog
from ai_workflow_ui import SummaryDialog,ScientificQuestionsDialog,AIReportCoordinator,AIReportDialog
from expert_ui import KnowledgeDialog,ReviewerDialog
from PySide6.QtWidgets import QPushButton,QWidget,QMessageBox
from ai_cancel import register_abort,check_cancelled

class Blocked:
    def __init__(self,value=None):
        self.entered=threading.Event();self.release=threading.Event();self.finished=threading.Event();self.aborted=threading.Event();self.value=value or dict(text='Late result',provider='deepseek',model='fixture')
    def __call__(self,*a,**kw):
        remove=register_abort(self.aborted.set)
        try:self.entered.set();self.release.wait(4);return self.value
        finally:remove();self.finished.set()

class Tests(unittest.TestCase):
    setUp=Runtime22Tests.setUp
    tearDown=Runtime22Tests.tearDown
    def manager(self):
        manager=AIManager(self.db)
        self.db.store_refresh_results(self.profile,[(self.row,1)],'pubmed')
        return manager
    def close_fast(self,dialog,button=False):
        closed=[];dialog.finished.connect(closed.append);start=time.monotonic()
        with patch('ai_workflow_ui.confirm_ai_close',return_value=True),patch('ai_finder_ui.confirm_ai_close',return_value=True):
            if button:next(b for b in dialog.findChildren(QPushButton) if b.text()=='关闭').click()
            else:self.assertTrue(dialog.close())
        self.assertLess(time.monotonic()-start,.7);self.assertTrue(closed)
    def test_summary_run_button_close_discards_late_results_and_keeps_saved_summary(self):
        manager=self.manager();old=dict(text='Previously saved summary',provider='deepseek',model='fixture')
        manager.repository.save_summary(self.row['article_key'],old,'profile:'+str(self.profile))
        saved=manager.repository.summary(self.row['article_key'])
        for button in (True,False):
            dialog=SummaryDialog(manager,self.row,'profile:'+str(self.profile));dialog.show();blocked=Blocked()
            engine=SimpleNamespace(summarize=blocked)
            with patch.object(dialog,'engine',return_value=engine):dialog.run_button.click()
            until(blocked.entered.is_set);identifier=dialog.task
            with patch.object(QMessageBox,'question',side_effect=AssertionError('Running close must not wait for a save prompt')):
                self.close_fast(dialog,button)
            self.assertNotIn(identifier,manager.jobs);until(blocked.aborted.is_set)
            blocked.release.set();until(blocked.finished.is_set);app.processEvents()
            self.assertEqual(manager.repository.summary(self.row['article_key']),saved)
            reopen=SummaryDialog(manager,self.row,'profile:'+str(self.profile));self.assertEqual(reopen.result['text'],old['text']);reopen.close();app.processEvents()
    def test_all_assistant_analysis_windows_close_without_replacing_saved_history(self):
        manager=self.manager()
        for cls,kind in ((KnowledgeDialog,'knowledge'),(ScientificQuestionsDialog,'questions'),(ReviewerDialog,'reviewer')):
            old=dict(text='Previously saved '+kind,provider='deepseek',model='fixture')
            identifier=manager.repository.save_history(kind,'Saved',dict(topic='DNA repair'),old)
            saved=manager.repository.history(identifier)
            dialog=cls(manager);dialog.saved_history_id=identifier;dialog.result=old;dialog.dirty=False;dialog.show()
            blocked=Blocked();dialog.start(blocked);until(blocked.entered.is_set);job=dialog.task
            self.close_fast(dialog,True);self.assertNotIn(job,manager.jobs)
            blocked.release.set();until(blocked.finished.is_set);app.processEvents()
            self.assertEqual(manager.repository.history(identifier),saved)
    def test_child_translation_is_cancelled_with_summary_window(self):
        manager=self.manager();dialog=SummaryDialog(manager,self.row,'profile:'+str(self.profile));dialog.show()
        blocked=Blocked();dialog.language_control.task=manager.submit(dialog.language_control,blocked,'done','failed')
        until(blocked.entered.is_set);job=dialog.language_control.task;self.close_fast(dialog)
        self.assertNotIn(job,manager.jobs);until(blocked.aborted.is_set);blocked.release.set();until(blocked.finished.is_set);app.processEvents()
        self.assertIsNone(manager.repository.summary(self.row['article_key']))
    def test_finder_closes_and_preserves_previous_answer(self):
        from ai_finder_ui import FinderChat
        manager=self.manager();inputs=dict(messages=[dict(role='assistant',content='Saved scientific answer',references=[])],attachments=[],notes={})
        identifier=manager.repository.save_history('finder','Saved chat',inputs,dict(text='Saved scientific answer'))
        dialog=FinderChat(manager,manager.repository.history(identifier));dialog.show();blocked=Blocked()
        dialog.task=manager.submit(dialog,blocked,'completed','failed');until(blocked.entered.is_set);self.close_fast(dialog)
        blocked.release.set();until(blocked.finished.is_set);app.processEvents()
        saved=manager.repository.history(identifier)
        self.assertEqual(saved['result']['text'],'Saved scientific answer');self.assertEqual(saved['inputs']['messages'][0]['content'],'Saved scientific answer')
    def test_closing_finder_during_edited_question_restores_previous_answer_and_cards(self):
        from ai_finder_ui import FinderChat
        manager=self.manager();key=self.row['article_key'];inputs=dict(messages=[dict(role='user',content='Original question'),dict(role='assistant',content='Saved scientific answer',references=[dict(article_key=key,number=1)])],attachments=[],notes={})
        identifier=manager.repository.save_history('finder','Saved chat',inputs,dict(text='Saved scientific answer'));manager.repository.replace_finder_cards(identifier,[self.row])
        dialog=FinderChat(manager,manager.repository.history(identifier));dialog.show();dialog.models.addItem('Fixture','deepseek');dialog.models.setCurrentIndex(dialog.models.count()-1)
        manager.store.config=Mock(return_value=dict(model='fixture',enabled=True));dialog.edit_request=(0,'Edited scientific question');blocked=Blocked()
        with patch('ai_finder_ui.AnalysisEngine',return_value=Mock()),patch('ai_finder_ui.find_articles',blocked):
            dialog.send();until(blocked.entered.is_set);self.assertEqual(len(dialog.messages),1);self.assertEqual(dialog.articles,[]);self.close_fast(dialog)
        blocked.release.set();until(blocked.finished.is_set);app.processEvents();saved=manager.repository.history(identifier)
        self.assertEqual(saved['result']['text'],'Saved scientific answer');self.assertEqual(len(saved['inputs']['messages']),2);self.assertEqual(saved['inputs']['messages'][0]['content'],'Original question')
        self.assertEqual(manager.repository.finder_cards(identifier)[0]['article_key'],key)
    def test_keyword_recommendation_closes_without_overwriting_saved_results(self):
        manager=self.manager();old=dict(raw='Saved keyword suggestions')
        identifier=manager.repository.save_history('keywords','Saved keywords',dict(topic='DNA repair'),old)
        dialog=RecommendationDialog(manager);dialog.topic.setPlainText('Pending topic');dialog.show();blocked=Blocked()
        dialog.task=manager.submit(dialog,blocked,'loaded','failed');until(blocked.entered.is_set);self.close_fast(dialog)
        blocked.release.set();until(blocked.finished.is_set);app.processEvents();self.assertEqual(manager.repository.history(identifier)['result'],old)
    def test_closing_one_window_does_not_cancel_another(self):
        manager=self.manager();one=SummaryDialog(manager,self.row,'profile:'+str(self.profile));two=ReviewerDialog(manager)
        one.show();two.show();a=Blocked();b=Blocked();one.start(a);two.start(b);until(lambda:a.entered.is_set() and b.entered.is_set())
        other=two.task;self.close_fast(one);self.assertIn(other,manager.jobs);self.assertFalse(b.aborted.is_set())
        self.close_fast(two);a.release.set();b.release.set();until(lambda:a.finished.is_set() and b.finished.is_set());app.processEvents()
    def test_closing_running_report_keeps_old_saved_report_and_ignores_queued_partial(self):
        manager=self.manager();manager.store.save('deepseek','fixture-key','fixture',[dict(id='fixture',name='Fixture')]);manager.store.toggle('deepseek')
        old=dict(keys=[self.row['article_key']],window={},errors={},created_at='old')
        manager.repository.save_summary(self.row['article_key'],dict(text='Saved summary',provider='deepseek',model='fixture',report_content_modes=['brief']),'report:'+str(self.profile))
        manager.repository.save_report(self.profile,old)
        window=QWidget();window.database=self.db;window.home=SimpleNamespace(reload=Mock());window.settings_page=None
        coordinator=AIReportCoordinator(manager,window);manager.repository.save_batch(self.profile,dict(keys=[self.row['article_key']],window={},created_at='new'))
        blocked=Blocked();engine=SimpleNamespace(provider='deepseek',model='fixture',summarize=blocked)
        # Force this run to regenerate rather than reusing the old summary.
        self.db.save_settings(dict(ai_report_content='news'))
        with patch('ai_workflow_ui.AnalysisEngine',return_value=engine):coordinator.generate(self.profile)
        until(blocked.entered.is_set);generation=coordinator.generations[self.profile]
        dialog=AIReportDialog(coordinator,self.profile);dialog.show();self.close_fast(dialog,True)
        coordinator.partial(dict(profile_id=self.profile,generation=generation,keys=['late']))
        blocked.release.set();until(blocked.finished.is_set);until(lambda:self.profile not in coordinator.running)
        self.assertNotIn(self.profile,coordinator.drafts);self.assertEqual(manager.repository.report(self.profile),old)
        reopen=AIReportDialog(coordinator,self.profile);self.assertEqual(reopen.report,old);reopen.close();window.close();app.processEvents()
    def test_report_publishes_each_replacement_immediately_and_running_close_restores_old_content(self):
        manager=self.manager();manager.store.save('deepseek','fixture-key','fixture',[dict(id='fixture',name='Fixture')]);manager.store.toggle('deepseek')
        second=article('pmid:second');self.db.store_refresh_results(self.profile,[(second,1)],'pubmed')
        key=self.row['article_key'];old=dict(text='Saved brief and figure',provider='deepseek',model='old-model',diagram_generation=dict(status='completed',artifact='old-figure'))
        manager.repository.save_summary(key,old,'report:'+str(self.profile));saved=manager.repository.summary(key)
        old_report=dict(keys=[key],window={},errors={},created_at='old');manager.repository.save_report(self.profile,old_report)
        window=QWidget();window.database=self.db;window.home=SimpleNamespace(reload=Mock());window.settings_page=None;coordinator=AIReportCoordinator(manager,window)
        manager.repository.save_batch(self.profile,dict(keys=[key,second['article_key']],window={},created_at='new'))
        blocked=Blocked();new=dict(text='Replacement brief and figure',provider='deepseek',model='fixture')
        engine=SimpleNamespace(provider='deepseek',model='fixture',summarize=lambda db,row,**kw:new if row['article_key']==key else blocked())
        with patch('ai_workflow_ui.AnalysisEngine',return_value=engine):coordinator.generate(self.profile)
        until(lambda:blocked.entered.is_set() and coordinator.drafts[self.profile]['keys']==[key])
        dialog=AIReportDialog(coordinator,self.profile);dialog.show();dialog.open_article(key);self.assertEqual(dialog.language_summary['text'],new['text'])
        self.assertEqual(manager.repository.summary(key)['text'],new['text']);self.close_fast(dialog)
        blocked.release.set();until(blocked.finished.is_set);until(lambda:self.profile not in coordinator.running)
        self.assertEqual(manager.repository.summary(key),saved);self.assertEqual(manager.repository.report(self.profile),old_report)
        reopen=AIReportDialog(coordinator,self.profile);reopen.open_article(key);self.assertEqual(reopen.language_summary['text'],old['text']);reopen.close()
        # A completed attempt keeps its newly published article summaries.
        manager.repository.save_batch(self.profile,dict(keys=[key],window={},created_at='third'))
        with patch('ai_workflow_ui.AnalysisEngine',return_value=engine):coordinator.generate(self.profile)
        until(lambda:self.profile not in coordinator.running);self.assertEqual(manager.repository.summary(key)['text'],new['text'])
        final=AIReportDialog(coordinator,self.profile);final.save();self.assertEqual(manager.repository.summary(key)['text'],new['text'])
        self.assertNotIn('_pending_summaries',manager.repository.report(self.profile));final.show()
        extra=Blocked();final.editorial_task=manager.submit(final,extra,'editorial_loaded','editorial_failed');until(extra.entered.is_set);self.close_fast(final)
        extra.release.set();until(extra.finished.is_set);self.assertEqual(manager.repository.summary(key)['text'],new['text']);window.close();app.processEvents()
    def test_restore_preserves_parallel_writes_and_never_resurrects_deleted_content(self):
        from ai_repository import SummaryChanges
        manager=self.manager();key=self.row['article_key'];context='report:'+str(self.profile);old=dict(text='Saved brief')
        manager.repository.save_summary(key,old,context);changes=SummaryChanges(manager.repository);epoch=manager.repository.epoch(key)
        changes.save(key,dict(text='Running replacement'),context,epoch)
        manager.repository.save_summary(key,dict(text='Other window saved this'),context,epoch);changes.restore()
        self.assertEqual(manager.repository.summary(key)['text'],'Other window saved this')
        changes=SummaryChanges(manager.repository);changes.save(key,dict(text='Second replacement'),context,epoch);manager.repository.delete_summary(key);changes.restore()
        self.assertIsNone(manager.repository.summary(key))
        with self.assertRaises(InterruptedError):changes.save(key,old,context,epoch)
    def test_restore_handles_multiple_replacements_and_committed_results(self):
        from ai_repository import SummaryChanges
        manager=self.manager();key=self.row['article_key'];context='report:'+str(self.profile);old=dict(text='Saved brief')
        manager.repository.save_summary(key,old,context);changes=SummaryChanges(manager.repository);epoch=manager.repository.epoch(key)
        for text in ('New brief','New news and figure','Translated brief'):changes.save(key,dict(text=text),context,epoch)
        self.assertEqual(manager.repository.summary(key)['text'],'Translated brief');changes.restore();self.assertEqual(manager.repository.summary(key)['text'],old['text'])
        changes=SummaryChanges(manager.repository);changes.save(key,dict(text='Explicitly saved'),context,epoch);changes.commit();changes.save(key,dict(text='Still running'),context,epoch);changes.restore()
        self.assertEqual(manager.repository.summary(key)['text'],'Explicitly saved')

if __name__=='__main__':unittest.main(defaultTest='Tests')
