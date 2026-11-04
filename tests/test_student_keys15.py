import unittest,threading,time
from datetime import datetime,timedelta
from unittest.mock import patch,Mock
from runtime_fixture import Runtime22Tests,app
from ai_store import AIStore
from ai_ui import AIManager
from lab_local import save_account,account,record,activity
from lab_service import LAB_URL,LAB_CUSTOM_URL,LabAPI,StudentDisabledError
from student_keys import logged_in,clear_keys,expire_on_startup,ensure
from lab_ui import LoginDialog

class Tests(unittest.TestCase):
    setUp=Runtime22Tests.setUp
    tearDown=Runtime22Tests.tearDown
    def login(self,keys=True,gateway=LAB_CUSTOM_URL):
        self.manager=AIManager(self.db);store=self.manager.store
        identity=save_account(self.db,dict(role='student',user_id='student',name='Student',providers=['deepseek'],gateway_url=gateway),{'deepseek':'fixture-old-key'} if keys else {},'student.fixture-id')
        store.save_lab_session('fixture-session',identity)
        store.save('deepseek',store.key('deepseek'),'fixture-model',[dict(id='fixture-model',name='Fixture')]);store.toggle('deepseek')
        return store,identity
    def response(self,identity):return dict(keys={'deepseek':'fixture-new-key'},identity=dict(identity,allow_local_keys=True))
    def test_fixed_15_day_periods_only_on_startup_no_logout(self):
        store,identity=self.login();start=datetime(2026,10,1,10).astimezone();logged_in(self.db,identity,start)
        self.assertFalse(expire_on_startup(store,start+timedelta(days=14)))
        self.assertTrue(expire_on_startup(store,start+timedelta(days=20)));self.assertIsNotNone(store.lab_state())
        self.assertFalse(account(self.db,identity['local_owner'])['keys'])
        with patch.object(LabAPI,'renew_student',return_value=self.response(identity)):ensure(store)
        self.assertFalse(expire_on_startup(store,start+timedelta(days=29)))
        self.assertTrue(expire_on_startup(store,start+timedelta(days=30)))
        with patch.object(LabAPI,'renew_student',return_value=self.response(identity)):ensure(store)
        self.assertTrue(expire_on_startup(store,start+timedelta(days=61)))
        with self.db.connect() as c:self.assertEqual(c.execute('SELECT last_period FROM ai_lab_key_expiry').fetchone()[0],4)
    def test_renew_uses_login_gateway_and_preserves_usage_and_encryption(self):
        for gateway in (LAB_URL,LAB_CUSTOM_URL):
            store,identity=self.login(False,gateway);record(self.db,identity['local_owner'],dict(total_tokens=30,known=True),'completed',provider='deepseek')
            def renewal(api,access):
                self.assertEqual(api.gateway,gateway);self.assertEqual(access,'student.fixture-id');return self.response(identity)
            with patch.object(LabAPI,'renew_student',renewal):ensure(store)
            self.assertEqual(store.key('deepseek'),'fixture-new-key');self.assertEqual(store.lab_state()['identity']['gateway_url'],gateway)
            with self.db.connect() as c:self.assertNotIn('fixture-new-key',c.execute('SELECT encrypted_credentials FROM ai_lab_accounts WHERE owner=?',(identity['local_owner'],)).fetchone()[0])
            self.assertGreaterEqual(sum(r['total_tokens'] for r in activity(self.db,identity)['daily']),30)
    def test_parallel_renew_once_and_cancelled_result_not_saved(self):
        store,identity=self.login(False);count=[];errors=[]
        def renew(api,access):count.append(1);time.sleep(.1);return self.response(identity)
        def work():
            try:ensure(store)
            except Exception as e:errors.append(e)
        with patch.object(LabAPI,'renew_student',renew):
            threads=[threading.Thread(target=work) for _ in range(4)]
            for t in threads:t.start()
            for t in threads:t.join()
        self.assertFalse(errors);self.assertEqual(len(count),1)
        clear_keys(self.db,identity['local_owner'])
        from ai_cancel import Cancellation,scope
        token=Cancellation()
        def cancelled(api,access):token.cancel();return self.response(identity)
        with scope(token),patch.object(LabAPI,'renew_student',cancelled):
            with self.assertRaises(InterruptedError):ensure(store)
        self.assertFalse(account(self.db,identity['local_owner'])['keys'])
    def test_disabled_never_generates_and_keeps_usage(self):
        store,identity=self.login(False)
        with patch.object(LabAPI,'renew_student',side_effect=StudentDisabledError(dict(user_id='student',role='student',disabled=True))):
            with self.assertRaises(StudentDisabledError):ensure(store)
        self.assertTrue(store.lab_state()['identity']['disabled'])
        with self.assertRaisesRegex(ValueError,'PI停用'):store.key('deepseek')
    def test_engine_defers_network_and_updates_credentials_before_call(self):
        store,identity=self.login(False)
        from ai_analysis import AnalysisEngine
        fake=Mock();fake.__enter__=Mock(return_value=fake);fake.__exit__=Mock(return_value=False);fake.complete.return_value=('Scientific answer',dict(known=True,total_tokens=4),False)
        seen=[]
        def factory(p,k):seen.append(k);return fake
        with patch.object(LabAPI,'renew_student',return_value=self.response(identity)) as renew:
            engine=AnalysisEngine(store,'deepseek',client_factory=factory);renew.assert_not_called()
            self.assertEqual(engine.call('instruction','evidence'),'Scientific answer')
            self.assertEqual(seen,['fixture-new-key']);self.assertEqual(renew.call_count,1)
    def test_login_clears_old_key_before_auth_and_locks_selected_host(self):
        store,identity=self.login();store.logout_lab();jobs=[];self.manager.submit=lambda owner,work,*a:jobs.append(work) or 1
        dialog=LoginDialog(self.manager,'student');self.assertEqual(dialog.gateway.currentData(),LAB_CUSTOM_URL);dialog.secret.setText('student.fixture-id')
        def sign(api,access):
            self.assertEqual(api.gateway,LAB_CUSTOM_URL);self.assertFalse(account(self.db,identity['local_owner'])['keys'])
            return dict(token='new-session',identity=identity)
        with patch.object(LabAPI,'login_student',sign),patch.object(LabAPI,'credentials',return_value=self.response(identity)),patch.object(LabAPI,'members',return_value=[]):
            dialog.start();result=jobs[0]();dialog.loaded(result)
        self.assertEqual(store.lab_state()['identity']['gateway_url'],LAB_CUSTOM_URL)
        self.assertEqual(store.key('deepseek'),'fixture-new-key');dialog.close()
    def test_explicit_host_never_falls_back_on_transport_failure(self):
        from ai_service import AIClient,AITransportError
        hosts=[]
        def request(client,*a):hosts.append(client.base);raise AITransportError('fixture transport failure')
        with patch.object(AIClient,'request',request),patch('lab_service.time.sleep'):
            with self.assertRaises(AITransportError):LabAPI(gateway=LAB_CUSTOM_URL).login_student('fixture')
        self.assertEqual(len(hosts),3);self.assertTrue(all(v==LAB_CUSTOM_URL+'/api/lab' for v in hosts))

if __name__=='__main__':unittest.main(defaultTest='Tests')
