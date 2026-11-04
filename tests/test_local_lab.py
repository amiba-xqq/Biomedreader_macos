import json,unittest,time
from datetime import date,timedelta
from unittest.mock import patch,Mock
from runtime_fixture import Runtime22Tests,app
from ai_ui import AIManager
from ai_store import AIStore,key_fingerprint
from lab_local import save_account,account,activity,record,prune
from lab_service import LabClient,LAB_URL
from lab_ui import LoginDialog,LabManagementDialog,LabPanel
from cache_cleanup import collect_unused

class Tests(unittest.TestCase):
    setUp=Runtime22Tests.setUp
    tearDown=Runtime22Tests.tearDown
    def login(self,keys,role='pi',student_id='',user='pi'):
        store=AIStore(self.db)
        identity=save_account(self.db,dict(user_id=user,role=role,name='Fixture',providers=list(keys) or ['deepseek'],gateway_url=LAB_URL),keys,student_id)
        store.save_lab_session('fixture-session-token',identity)
        store.save('deepseek',store.key('deepseek'),'fixture-model',[dict(id='fixture-model',name='Fixture')]);store.toggle('deepseek')
        return store,identity
    def test_ciphertext_and_multiple_accounts_are_preserved(self):
        a,ia=self.login({'deepseek':'fixture-private-key-A'})
        b,ib=self.login({'deepseek':'fixture-private-key-B'})
        self.assertNotEqual(ia['local_owner'],ib['local_owner'])
        self.assertEqual(account(self.db,ia['local_owner'])['keys']['deepseek'],'fixture-private-key-A')
        self.assertEqual(b.key('deepseek'),'fixture-private-key-B')
        with self.db.connect() as c:
            data=' '.join(str(tuple(row)) for row in c.execute('SELECT * FROM ai_lab_accounts'))
        self.assertNotIn('fixture-private-key-A',data);self.assertNotIn('fixture-private-key-B',data)
        a.save_lab_session('fixture-session-token',ia)
        self.assertEqual(a.key('deepseek'),'fixture-private-key-A')
    def test_direct_transport_uses_local_key_and_missing_student_key_renews(self):
        store,identity=self.login({'deepseek':'fixture-key-direct'})
        with patch('lab_service.LabAPI.request',side_effect=AssertionError('No Cloudflare call allowed')):
            with store.client('deepseek') as client:
                self.assertNotIsInstance(client,LabClient);self.assertEqual(client.base,'https://api.deepseek.com')
        store,identity=self.login({},role='student',student_id='student.secret',user='student')
        with patch('lab_service.LabAPI.renew_student',return_value=dict(identity=identity,keys={'deepseek':'fixture-renewed-key'})):
            with store.client('deepseek') as client:
                self.assertNotIsInstance(client,LabClient);self.assertEqual(client.key,'fixture-renewed-key')
    def test_local_usage_isolation_cleanup_and_calendar_expiry(self):
        store,ia=self.login({'deepseek':'fixture-key-A'})
        store.record('deepseek',key_fingerprint('fixture-key-A'),'fixture-model',dict(input_tokens=20,output_tokens=10,total_tokens=30,known=True),owner=ia['local_owner'])
        store,ib=self.login({'deepseek':'fixture-key-B'})
        store.record('deepseek',key_fingerprint('fixture-key-B'),'fixture-model',dict(total_tokens=90,known=True),owner=ib['local_owner'])
        self.assertEqual(sum(r['total_tokens'] for r in activity(self.db,ia)['daily']),30)
        self.assertEqual(sum(r['total_tokens'] for r in activity(self.db,ib)['daily']),90)
        with self.db.connect() as c:
            c.execute('INSERT INTO ai_lab_daily VALUES(?,?,?,?,?,?,?)',(ia['local_owner'],'2020-01-01',1,1,2,1,0))
            collect_unused(c,prune_unreported_summaries=True)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM ai_lab_accounts').fetchone()[0],2)
        self.assertEqual(len(activity(self.db,ia)['daily']),1)
        self.assertEqual(account(self.db,ia['local_owner'])['keys']['deepseek'],'fixture-key-A')
    def test_same_student_key_two_ids_do_not_share_usage(self):
        store,ia=self.login({'deepseek':'fixture-shared-key'},'student','ID-A','student')
        record(self.db,ia['local_owner'],dict(total_tokens=20,known=True),'completed')
        store,ib=self.login({'deepseek':'fixture-shared-key'},'student','ID-B','student')
        self.assertNotEqual(ia['local_owner'],ib['local_owner']);self.assertFalse(activity(self.db,ib)['daily'])
        store.record('deepseek',key_fingerprint('fixture-shared-key'),'fixture-model',dict(total_tokens=90,known=True),owner=ib['local_owner'])
        self.assertEqual(store.usage('deepseek')['total_tokens'],90)
        store.save_lab_session('fixture-session-token',ia)
        self.assertEqual(store.usage('deepseek')['total_tokens'],0)
    def test_management_is_offline_self_only_and_no_bar_chart(self):
        store,identity=self.login({'deepseek':'fixture-pi-key'})
        record(self.db,identity['local_owner'],dict(total_tokens=20,known=True),'completed')
        manager=AIManager(self.db)
        with patch('lab_service.LabAPI.request',side_effect=AssertionError('offline view must not call gateway')):
            dialog=LabManagementDialog(manager)
            self.assertFalse(hasattr(dialog,'chart_mode'));self.assertFalse(hasattr(dialog,'chart'))
            self.assertEqual(sum(v['total_tokens'] for v in dialog.activity.totals().values()),20)
            dialog.close()
        panel=LabPanel(manager);self.assertFalse(panel.balance_button.isHidden());panel.close()
    def test_close_login_cancels_immediately_and_late_result_does_not_login(self):
        manager=AIManager(self.db);manager.cancel=Mock();dialog=LoginDialog(manager,'student');dialog.task=123
        self.assertFalse(hasattr(dialog,'connection'))
        start=time.monotonic();dialog.reject();self.assertLess(time.monotonic()-start,.3);manager.cancel.assert_called_once_with(123)
        dialog.loaded(dict(token='late-session',identity=dict(role='student',user_id='late'),keys={}))
        self.assertIsNone(manager.store.lab_state())
    def test_switching_active_account_stops_old_engine(self):
        from ai_analysis import AnalysisEngine
        store,ia=self.login({'deepseek':'same-fixture-key'},'student','ID-A','student')
        engine=AnalysisEngine(store,'deepseek')
        self.login({'deepseek':'same-fixture-key'},'student','ID-B','student')
        with self.assertRaises(InterruptedError):engine.call('Evidence','Scientific question')
    def test_pi_balance_uses_active_cache_and_skips_unsupported_platforms(self):
        from lab_balance_ui import LabBalanceDialog
        store,identity=self.login({'deepseek':'fixture-key-A','openai':'fixture-key-openai'})
        manager=AIManager(self.db);jobs=[];manager.submit=lambda owner,work,*args:jobs.append(work) or 1
        dialog=LabBalanceDialog(manager)
        with patch('ai_service.AIClient.balance',return_value={'balance_infos':[{'currency':'CNY','total_balance':'12.3'}]}),patch('lab_service.LabAPI.request',side_effect=AssertionError('No Cloudflare for balances')):
            result=jobs[0]()
        self.assertEqual(len(result),2);self.assertIn('12.3',result[0][1]);self.assertIn('未接入',result[1][1])
        dialog.loaded(result);self.assertEqual(dialog.table.rowCount(),2);dialog.close()
    def test_login_windows_do_not_advertise_student_key_download(self):
        from PySide6.QtWidgets import QLabel
        manager=AIManager(self.db)
        for role in ('pi','student'):
            dialog=LoginDialog(manager,role)
            text=' '.join(label.text() for label in dialog.findChildren(QLabel))
            self.assertNotIn('下载',text);self.assertNotIn('授权后',text)
            self.assertIn('会连接到 Cloudflare',text);self.assertFalse(hasattr(dialog,'connection'));dialog.close()

if __name__=='__main__':unittest.main(defaultTest='Tests')
