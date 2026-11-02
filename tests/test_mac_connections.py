"""Regression coverage for background Word actions and Finder-launched Codex."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import test_hidden_articles


class MacWordActivationTests(unittest.TestCase):
    def test_running_reader_receives_reference_actions_without_reopen(self):
        import mac_word_plugin as plugin
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'alive').touch()
            with patch.object(plugin.sys,'platform','darwin'),patch.object(plugin.subprocess,'run') as launch:
                for action in ('render','selected'):plugin.ensure_application(action,root)
                launch.assert_not_called()
                plugin.ensure_application('show',root)
                args=launch.call_args.args[0]
                self.assertEqual(args[:2],['/usr/bin/open','-a'])
                self.assertNotIn('-g',args)

    def test_stopped_reader_starts_in_background(self):
        import mac_word_plugin as plugin
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for stale in (False,True):
                if stale:
                    (root/'alive').touch();old=time.time()-120;os.utime(root/'alive',(old,old))
                with patch.object(plugin.sys,'platform','darwin'),patch.object(plugin.subprocess,'run') as launch:
                    plugin.ensure_application('render',root)
                    args=launch.call_args.args[0]
                    self.assertIn('-g',args);self.assertEqual(args[-2:],['--args','--word-background'])

    def test_render_handler_never_activates_window(self):
        from word_bridge import WordBridge
        window=Mock();fake=SimpleNamespace(window=window)
        result=WordBridge.execute(fake,dict(action='render',records=[]))
        self.assertEqual(result['references'],[])
        window.showNormal.assert_not_called();window.activateWindow.assert_not_called();window.raise_.assert_not_called()
        WordBridge.execute(fake,dict(action='show'))
        window.showNormal.assert_called_once();window.activateWindow.assert_called_once()


class MacCodexTests(unittest.TestCase):
    def test_bundled_component_is_found_with_empty_finder_path(self):
        import codex_runtime as runtime
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);binary=root/'codex-runtime/codex';binary.parent.mkdir();binary.write_text('fixture');binary.chmod(0o755)
            with patch.object(runtime.sys,'platform','darwin'),patch.object(runtime.sys,'_MEIPASS',str(root),create=True),patch.object(runtime.shutil,'which',return_value=None):
                self.assertEqual(runtime.executable(),str(binary))

    def test_user_cli_location_and_path_node_runtime_are_supported(self):
        import codex_runtime as runtime
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);binary=root/'.nvm/versions/node/v24/bin/codex';binary.parent.mkdir(parents=True);binary.write_text('fixture');binary.chmod(0o755)
            with patch.object(runtime.sys,'platform','darwin'),patch.object(runtime.sys,'_MEIPASS',str(root/'bundle'),create=True),patch.object(runtime.Path,'home',return_value=root),patch.object(runtime.shutil,'which',return_value=None):
                self.assertEqual(runtime.executable(),str(binary))
                with patch.dict(os.environ,{'OPENAI_API_KEY':'not-a-key','PATH':'/usr/bin'}):
                    env=runtime.process_environment(str(binary),root/'private')
                self.assertIn(str(binary.parent),env['PATH'].split(os.pathsep))
                self.assertNotIn('OPENAI_API_KEY',env)
                self.assertEqual(env['CODEX_HOME'],str(root/'private'))

    def test_official_new_and_legacy_login_hosts_only(self):
        from codex_runtime import login_url
        for url in ('https://auth.openai.com/oauth/authorize?state=fixture','https://chatgpt.com/oauth/authorize?state=fixture'):
            self.assertEqual(login_url(url),url)
        for url in ('https://chatgpt.com.evil.example/','https://auth.openai.com@evil.example/','http://chatgpt.com/','file:///tmp/login',None,'https://chatgpt.com:444/login'):
            with self.assertRaises(ValueError):login_url(url)

    def test_browser_login_uses_returned_url_then_reads_account_models(self):
        import codex_bridge as bridge
        client=bridge.CodexClient();client.rpc=Mock()
        url='https://chatgpt.com/oauth/authorize?state=fixture'
        def request(method,params):
            if method=='account/login/start':return dict(authUrl=url,loginId='fixture')
            if method=='account/read':return dict(account=dict(type='chatgpt'))
            if method=='model/list':return dict(data=[dict(id='account-model',model='account-model',displayName='Account model')])
            raise AssertionError(method)
        client.rpc.request.side_effect=request
        client.rpc.next.return_value=dict(method='account/login/completed',params=dict(success=True))
        with patch.object(bridge.subprocess,'run') as browser,patch('sys.platform','darwin'):
            rows=client.login()
        self.assertEqual(browser.call_args.args[0],['/usr/bin/open',url])
        self.assertEqual(rows[0]['id'],'account-model')
        self.assertFalse(any(c.args[0] in ('turn/start','thread/start') for c in client.rpc.request.call_args_list))
