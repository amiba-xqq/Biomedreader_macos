import unittest
from unittest.mock import Mock,patch
from runtime_fixture import Runtime22Tests,app
from ai_ui import AIManager
from lab_ui import LoginDialog,LabManagementDialog
from lab_local import save_account,account
from lab_service import LAB_CUSTOM_URL,LAB_URL,LabAPI
from PySide6.QtWidgets import QPushButton,QLabel

class Tests(unittest.TestCase):
    setUp=Runtime22Tests.setUp
    tearDown=Runtime22Tests.tearDown
    def test_both_login_defaults_custom_domain_and_still_offer_workers(self):
        manager=AIManager(self.db)
        for role in ('pi','student'):
            dialog=LoginDialog(manager,role)
            self.assertEqual(dialog.gateway.currentData(),LAB_CUSTOM_URL)
            self.assertGreaterEqual(dialog.gateway.findData(LAB_URL),0)
            dialog.close()
    def test_management_changes_sync_keys_members_and_keep_selected_gateway(self):
        manager=AIManager(self.db);store=manager.store
        members=[dict(user_id='pi',role='pi',name='PI',enabled=1)]
        identity=save_account(self.db,dict(role='pi',user_id='pi',name='PI',providers=['deepseek'],gateway_url=LAB_CUSTOM_URL),{'deepseek':'fixture-original-key'},members=members)
        store.save_lab_session('fixture-token',identity)
        queue=[]
        def submit(owner,work,done,failed,*args,**kwargs):queue.append((owner,work,done));return len(queue)
        manager.submit=submit;dialog=LabManagementDialog(manager)
        text=' '.join(w.text() for w in dialog.findChildren(QPushButton)+dialog.findChildren(QLabel))
        self.assertNotIn('同步云端权限',text);self.assertNotIn('提取真实Key',text);self.assertFalse(hasattr(dialog,'allow_local'))
        remote_keys={'deepseek':'fixture-original-key'}
        def credentials(api):
            self.assertEqual(api.gateway,LAB_CUSTOM_URL)
            return dict(identity=dict(identity,allow_local_keys=True),keys=dict(remote_keys))
        def get_members(api):self.assertEqual(api.gateway,LAB_CUSTOM_URL);return list(members)
        def drain():
            self.assertEqual(len(queue),1)
            owner,work,done=queue.pop(0);getattr(owner,done)(work())
            self.assertFalse(queue);self.assertIsNone(dialog.task)
        with patch.object(LabAPI,'credentials',credentials),patch.object(LabAPI,'members',get_members):
            members.append(dict(user_id='student',role='student',name='Student',enabled=1))
            # Synchronization begins before the newly-created ID popup closes.
            with patch('lab_ui.CredentialDialog') as popup:
                popup.return_value.exec.side_effect=lambda:self.assertEqual(len(queue),1)
                dialog.credential_loaded(dict(user_id='student',access_id='student.fixture-access'))
            drain();self.assertEqual(dialog.table.rowCount(),2)
            for enabled in (0,1):
                members[1]['enabled']=enabled;dialog.action_loaded(dict(ok=True));drain()
                saved=account(self.db,store.lab_state()['identity']['local_owner'])
                self.assertEqual(saved['members'][1]['enabled'],enabled)
            remote_keys['openai']='fixture-added-key';dialog.key_loaded(dict(ok=True));drain()
            saved=account(self.db,store.lab_state()['identity']['local_owner'])
            self.assertEqual(saved['keys']['openai'],'fixture-added-key')
            self.assertEqual(saved['identity']['gateway_url'],LAB_CUSTOM_URL)
            members.pop();dialog.action_loaded(dict(ok=True));drain();self.assertEqual(dialog.table.rowCount(),1)
        dialog.close()

if __name__=='__main__':unittest.main(defaultTest='Tests')
