import threading,time,queue,unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from runtime_fixture import Runtime22Tests,article,app
from PySide6.QtWidgets import QWidget
from ai_ui import AIManager
from ai_cancel import Cancellation,AICancelled,interruptible,scope,check_cancelled,CANCEL_MESSAGE

def until(predicate,timeout=2):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        app.processEvents()
        if predicate():return
        time.sleep(.01)
    raise AssertionError('Timed out waiting for fixture')

class Tests(unittest.TestCase):
    setUp=Runtime22Tests.setUp
    tearDown=Runtime22Tests.tearDown

    def test_cancel_blocked_http_promptly_and_discard_late_result(self):
        from ai_service import AIClient
        manager=AIManager(self.db);owner=QWidget();owner.stop=threading.Event()
        entered=threading.Event();release=threading.Event();finished=threading.Event()
        response=Mock(status_code=200);response.json.return_value={'generated':'late'}
        session=Mock();session.headers={}
        def blocked(*args,**kw):entered.set();release.wait(3);return response
        session.request.side_effect=blocked
        def work():
            try:
                with AIClient('deepseek','fixture-not-a-real-key',session) as client:
                    data=client.request('POST','/chat/completions',{})
                manager.repository.save_history('keywords','late',{},data)
                return data
            finally:finished.set()
        owner.completed=Mock();owner.failed=Mock()
        identifier=manager.submit(owner,work,'completed','failed');until(entered.is_set)
        start=time.monotonic();manager.cancel(identifier)
        until(lambda:owner.failed.called,.7)
        self.assertLess(time.monotonic()-start,.7);self.assertTrue(owner.failed.call_args.args[0].startswith('已取消本地AI任务'))
        release.set();until(finished.is_set);app.processEvents()
        owner.completed.assert_not_called();self.assertFalse(manager.repository.histories('keywords'));response.close.assert_called()
        owner.deleteLater()

    def test_cancel_before_queued_job_starts_never_calls_provider(self):
        token=Cancellation();token.cancel();work=Mock()
        with self.assertRaises(AICancelled):interruptible(work,token)
        work.assert_not_called()

    def test_summary_and_reviewer_cancel_buttons_restore_controls(self):
        from ai_workflow_ui import SummaryDialog
        from expert_ui import ReviewerDialog
        self.db.store_refresh_results(self.profile,[(self.row,1)],'pubmed')
        manager=AIManager(self.db)
        dialogs=[SummaryDialog(manager,self.row,'profile:'+str(self.profile)),ReviewerDialog(manager)]
        for dialog in dialogs:
            entered=threading.Event();release=threading.Event();done=threading.Event()
            def work():
                try:entered.set();release.wait(2);return {'text':'late result'}
                finally:done.set()
            dialog.start(work);until(entered.is_set)
            dialog.stop_button.click();until(lambda:dialog.task is None,.7)
            self.assertTrue(dialog.run_button.isEnabled());self.assertIsNone(dialog.result)
            release.set();until(done.is_set);dialog.approved=True;dialog.close()

    def test_codex_wait_is_interruptible_without_model_calls(self):
        from codex_bridge import RPC
        rpc=object.__new__(RPC);rpc.events=Mock();token=Cancellation()
        def cancel_during_poll(*,timeout):
            self.assertLessEqual(timeout,.1)
            token.cancel()
            raise queue.Empty
        rpc.events.get.side_effect=cancel_during_poll
        with scope(token):
            with self.assertRaises(AICancelled):rpc.next(600)
        rpc.events.get.assert_called_once()

    def test_cancel_article_browser_stops_loading_and_pending_pdf(self):
        from ai_content import ArticleWebReader
        reader=ArticleWebReader(self.root,None);reader.view=Mock();download=Mock()
        request=dict(event=threading.Event(),error='用户取消本机网页读取。',downloads=[download])
        reader.active=request;reader.expire(request)
        reader.view.stop.assert_called_once();download.cancel.assert_called_once()
        self.assertIsNone(reader.active);self.assertTrue(request['event'].is_set())
        reader.deleteLater()

    def test_codex_cancel_sends_interrupt_for_its_own_turn(self):
        from codex_bridge import RPC
        rpc=object.__new__(RPC);rpc.process=Mock();rpc.process.poll.return_value=None
        rpc.active_turn=('own-thread','own-turn');rpc.send=Mock();rpc.close=Mock()
        rpc.abort();payload=rpc.send.call_args.args[0]
        self.assertEqual(payload['method'],'turn/interrupt');self.assertEqual(payload['params'],dict(threadId='own-thread',turnId='own-turn'));rpc.close.assert_called_once()

    def test_report_first_article_visible_while_second_running_and_kept_on_cancel(self):
        from ai_workflow_ui import AIReportCoordinator,AIReportDialog
        rows=[article('pmid:'+str(i)) for i in (1,2,3)]
        self.db.store_refresh_results(self.profile,[(row,1) for row in rows],'pubmed')
        manager=AIManager(self.db);manager.store.save('deepseek','fixture-no-real-key','fixture',[dict(id='fixture',name='Fixture')]);manager.store.toggle('deepseek')
        window=QWidget();window.database=self.db;window.home=SimpleNamespace(reload=Mock());window.settings_page=None
        coordinator=AIReportCoordinator(manager,window)
        coordinator.manager.repository.save_batch(self.profile,dict(keys=[r['article_key'] for r in rows],window={},created_at='fixture-batch'))
        second=threading.Event();release=threading.Event();finished=threading.Event();calls=[]
        class Engine:
            provider='deepseek';model='fixture'
            def summarize(inner,database,row,**kwargs):
                calls.append(row['article_key'])
                if len(calls)==2:
                    second.set();release.wait(3);finished.set()
                return dict(text='Finished '+row['title'],provider='deepseek',model='fixture',report_content_modes=['brief'])
        with patch('ai_workflow_ui.AnalysisEngine',return_value=Engine()):
            coordinator.generate(self.profile);dialog=AIReportDialog(coordinator,self.profile)
            until(lambda:second.is_set() and dialog.rows.count()==1)
            self.assertIn(self.profile,coordinator.running);self.assertIsNotNone(manager.repository.summary(rows[0]['article_key']))
            self.assertEqual(dialog.report['keys'],[rows[0]['article_key']]);self.assertIn('已处理：1/3',dialog.heading.text())
            dialog.open_article(rows[0]['article_key']);self.assertIn('Finished',dialog.language_summary['text'])
            start=time.monotonic();coordinator.cancel(self.profile);until(lambda:self.profile not in coordinator.running,.7)
            self.assertLess(time.monotonic()-start,.7);self.assertEqual(coordinator.drafts[self.profile]['status'],'cancelled')
            release.set();until(finished.is_set);time.sleep(.05);app.processEvents()
            self.assertEqual(len(calls),2);self.assertIsNone(manager.repository.summary(rows[1]['article_key']))
            self.assertIsNotNone(manager.repository.summary(rows[0]['article_key']))
            dialog.approved=True;dialog.close();window.close()

    def test_english_figure_requirement_reaches_external_drawing_route(self):
        from model_reference import DIAGRAM_INSTRUCTIONS
        from image_routes import draw
        self.assertIn('英文',DIAGRAM_INSTRUCTIONS);self.assertIn('不使用中文',DIAGRAM_INSTRUCTIONS)
        engine=SimpleNamespace(provider='deepseek',model='fixture',store=SimpleNamespace(database=self.db),cancelled=lambda:False,call=Mock(return_value='mechanism'))
        with patch('image_routes.selected_route',return_value='ark'),patch('image_routes.config',return_value=dict(enabled=True,key='fixture',model='image-fixture')),patch('image_routes.generate',return_value=b'image') as generate,patch('model_artifact.validate_figure',return_value={'format':'png'}):
            draw(engine,'fixture evidence',{'images':['fixture']},lambda text:None)
        self.assertIn('英文',engine.call.call_args.args[0]);self.assertIn('must be in English',generate.call_args.args[2])

if __name__=='__main__':unittest.main()
