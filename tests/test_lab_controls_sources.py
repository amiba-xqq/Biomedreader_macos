import unittest
from datetime import date,timedelta,datetime,timezone
from unittest.mock import Mock,patch
from runtime_fixture import Runtime22Tests,app
from ai_ui import AIManager
from ai_store import AIStore,key_fingerprint
from lab_ui import LoginDialog,LabPanel,LabManagementDialog
from lab_local import save_account,activity,record
from lab_login_budget import remaining,success
from cache_cleanup import collect_unused

class Tests(unittest.TestCase):
    setUp=Runtime22Tests.setUp
    tearDown=Runtime22Tests.tearDown
    def login(self):
        self.manager=AIManager(self.db)
        identity=save_account(self.db,dict(user_id='pi',role='pi',name='PI',providers=['deepseek','openai']),{'deepseek':'fixture-key-A'})
        self.manager.store.save_lab_session('fixture-session',identity);return identity
    def test_role_counts_persist_independently_and_reset_next_day(self):
        today=date.today()
        for i in range(20):success(self.db,'pi','pi-'+str(i))
        self.assertEqual(remaining(self.db,'pi'),0);self.assertEqual(remaining(self.db,'student'),20)
        success(self.db,'pi','pi-19');self.assertEqual(remaining(self.db,'pi'),0)
        manager=AIManager(self.db);dialog=LoginDialog(manager,'pi')
        self.assertEqual(dialog.login.text(),'登录（0）');self.assertFalse(dialog.login.isEnabled())
        with self.db.connect() as c:collect_unused(c,prune_unreported_summaries=True)
        self.assertEqual(remaining(self.db,'pi'),0)
        tomorrow=today+timedelta(days=1)
        with patch('lab_ui.date') as ui_clock,patch('lab_login_budget.date') as budget_clock:
            ui_clock.today.return_value=tomorrow;budget_clock.today.return_value=tomorrow
            dialog.check_day();self.assertEqual(dialog.login.text(),'登录（20）');self.assertTrue(dialog.login.isEnabled())
        dialog.close()
    def test_network_failure_does_not_consume_and_server_zero_disables(self):
        manager=AIManager(self.db);dialog=LoginDialog(manager,'student')
        self.assertEqual(dialog.login.text(),'登录（20）')
        dialog.failed('无法连接课题组服务');self.assertEqual(remaining(self.db,'student'),20)
        dialog.failed('该账户今日已登录20次，请明日再登录。')
        self.assertEqual(dialog.login.text(),'登录（0）');self.assertFalse(dialog.login.isEnabled());dialog.close()
    def test_success_counts_once_and_both_entry_buttons_stay_locked_until_logout(self):
        manager=AIManager(self.db);jobs=[];manager.submit=lambda owner,work,*args:jobs.append(work) or 1
        panel=LabPanel(manager);dialog=LoginDialog(manager,'pi');dialog.secret.setText('fixture-key-A')
        identity=dict(user_id='pi',role='pi',name='PI',providers=['deepseek'])
        with patch('lab_service.LabAPI.login_pi',return_value=dict(token='fixture-session',identity=identity,logins_today=1)),patch('lab_service.LabAPI.credentials',return_value=dict(keys={'deepseek':'fixture-key-A'},identity=identity)),patch('lab_service.LabAPI.members',return_value=[]),patch('lab_service.LabAPI.usage_activity',return_value=dict(daily=[])):
            dialog.start();result=jobs[0]()
        self.assertEqual(remaining(self.db,'pi'),19);dialog.loaded(result)
        self.assertEqual(remaining(self.db,'pi'),19)
        self.assertTrue(all(not b.isEnabled() for b in panel.login_buttons.values()))
        with patch('lab_ui.LoginDialog') as other:
            panel.login('student');other.assert_not_called()
        manager.store.use_lab(False);manager.changed.emit()
        self.assertTrue(all(not b.isEnabled() for b in panel.login_buttons.values()))
        panel.logout();self.assertTrue(all(b.isEnabled() for b in panel.login_buttons.values()))
        self.assertEqual(remaining(self.db,'pi'),19);panel.close()
    def test_provider_filter_new_calls_sum_and_unknown_legacy(self):
        identity=self.login();store=self.manager.store;owner=identity['local_owner']
        store.record('deepseek',key_fingerprint('fixture-key-A'),'fixture',dict(input_tokens=20,output_tokens=10,total_tokens=30,known=True),owner=owner)
        store.record('openai','fixture-other-fingerprint','fixture',dict(input_tokens=50,output_tokens=20,total_tokens=70,known=True),owner=owner)
        record(self.db,owner,dict(total_tokens=5,known=True),'completed')
        dialog=LabManagementDialog(self.manager)
        self.assertEqual(sum(v['total_tokens'] for v in dialog.activity.totals().values()),105)
        for provider,expected in [('deepseek',30),('openai',70),('legacy',5)]:
            dialog.usage_source.setCurrentIndex(dialog.usage_source.findData(provider))
            self.assertEqual(sum(v['total_tokens'] for v in dialog.activity.totals().values()),expected)
        dialog.reload();self.assertEqual(dialog.usage_source.currentData(),'legacy')
        dialog.usage_source.setCurrentIndex(0);self.assertEqual(sum(v['total_tokens'] for v in dialog.activity.totals().values()),105)
        dialog.close()
    def test_old_daily_is_classified_only_when_raw_call_evidence_exists(self):
        identity=self.login();owner=identity['local_owner'];day=date.today().isoformat()
        with self.db.connect() as c:
            c.execute('INSERT INTO ai_lab_daily VALUES(?,?,?,?,?,?,?)',(owner,day,80,20,100,3,0))
            for provider,incoming,outgoing,total in [('deepseek',20,10,30),('openai',40,10,50)]:
                cur=c.execute('INSERT INTO ai_call_usage(provider,key_fingerprint,model,called_at,input_tokens,output_tokens,cached_tokens,total_tokens,usage_known,outcome) VALUES(?,?,?,?,?,?,?,?,?,?)',(provider,'fixture','fixture',datetime.now(timezone.utc).isoformat(),incoming,outgoing,0,total,1,'completed'))
                c.execute('INSERT INTO ai_lab_call_owners VALUES(?,?,?)',(cur.lastrowid,owner,day))
        rows=activity(self.db,identity)['daily'];totals={r['provider']:r['total_tokens'] for r in rows}
        self.assertEqual(totals,dict(deepseek=30,openai=50,legacy=20))
        self.assertEqual(activity(self.db,identity)['daily'],rows)
        self.manager.store.record('deepseek','fixture','fixture',dict(input_tokens=2,output_tokens=1,total_tokens=3,known=True),owner=owner)
        self.assertEqual(sum(r['total_tokens'] for r in activity(self.db,identity)['daily']),103)
    def test_balance_bill_column_fits_header_and_button(self):
        from lab_balance_ui import LabBalanceDialog
        identity=self.login();self.manager.submit=Mock(return_value=1)
        dialog=LabBalanceDialog(self.manager);dialog.loaded([('deepseek','CNY 12.3'),('openai','普通API Key未提供余额查询接口，请查看官方账单')]);dialog.show()
        for _ in range(15):app.processEvents()
        width=dialog.table.columnWidth(2);button=dialog.table.cellWidget(0,2)
        self.assertGreaterEqual(width,button.sizeHint().width())
        header=dialog.table.horizontalHeader();self.assertGreaterEqual(width,header.fontMetrics().horizontalAdvance('官方用量与账单')+8)
        self.assertGreaterEqual(button.width(),button.sizeHint().width()-4);dialog.close()

if __name__=='__main__':unittest.main(defaultTest='Tests')
