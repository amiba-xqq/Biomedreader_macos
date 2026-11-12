import unittest
from unittest.mock import Mock,patch
from runtime_fixture import app
from feedback import FeedbackTask,validated_feedback,FEEDBACK_URL
from release_notes import ReleaseNotesDialog,SECTIONS

class Tests(unittest.TestCase):
    def test_feedback_uses_only_root_domain_and_preserves_server_error(self):
        self.assertEqual(FEEDBACK_URL,'https://biomedreader.cn/api/feedback')
        data=validated_feedback('Bug 报告','Test issue','A complete harmless report for a mock server.')
        result=Mock(status_code=429);session=Mock();session.post.return_value=result
        context=Mock();context.__enter__=Mock(return_value=session);context.__exit__=Mock(return_value=False)
        errors=[];task=FeedbackTask(data);task.signals.failed.connect(errors.append)
        with patch('feedback.session_for_updates',return_value=context):task.run()
        self.assertEqual(session.post.call_count,1)
        self.assertEqual(session.post.call_args.args[0],FEEDBACK_URL)
        self.assertFalse(session.post.call_args.kwargs['allow_redirects']);self.assertIn('过于频繁',errors[0])
    def test_both_release_views_are_consolidated_and_exclude_requested_content(self):
        headings=[heading for heading,*_ in SECTIONS]
        self.assertEqual(len(headings),len(set(headings)))
        dialog=ReleaseNotesDialog()
        for index in (0,1):
            dialog.mode.setCurrentIndex(index);text=dialog.browser.toPlainText()
            for banned in ('Biomni','biomni','本机加密','本地缓存API','下载真实API','15天','卡片标题缩放','反馈连接修复','反馈连接与卡片标题缩放'):
                self.assertNotIn(banned,text)
            for required in ('AI Protocol','AI 作图','Scientific Agent Skills','Word','EndNote'):
                self.assertIn(required,text)
            self.assertLess(len(text),3500)
        dialog.close()

if __name__=='__main__':unittest.main(defaultTest='Tests')
