"""No live emails: exercise the exact desktop feedback wire format and UI retry."""
import json
import unittest
from unittest.mock import Mock,patch
import requests
from PySide6.QtWidgets import QApplication
from feedback import FEEDBACK_URL,FeedbackTask,FeedbackPage,submit_feedback,validated_feedback

app=QApplication.instance() or QApplication([])

class FeedbackTransportTests(unittest.TestCase):
    def data(self):
        return validated_feedback('Bug 报告','反馈传输测试','这是用于检查反馈请求格式的测试内容，不包含任何私人资料。')

    def response(self,status=200,payload=None):
        result=Mock(status_code=status)
        result.json.return_value=payload
        return result

    def test_utf8_chinese_limit_fits_worker_byte_limit_and_never_redirects(self):
        data=self.data();data['body']='文'*6000
        session=Mock();response=self.response(payload=dict(ok=True,id=data['id'],notified=True))
        session.post.return_value=response
        self.assertTrue(submit_feedback(session,data)['notified'])
        call=session.post.call_args
        self.assertEqual(call.args,(FEEDBACK_URL,))
        self.assertNotIn('workers.dev',call.args[0])
        self.assertFalse(call.kwargs['allow_redirects'])
        payload=call.kwargs['data']
        self.assertLess(len(payload),32000)
        self.assertEqual(json.loads(payload.decode('utf-8')),{**data,'website':''})
        self.assertEqual(session.get_adapter.return_value.max_retries.total,0)
        response.close.assert_called_once()

    def test_rejected_http_status_is_specific_and_never_claims_success(self):
        for status in (400,403,404,409,413,429,500,503,302):
            with self.subTest(status=status):
                data=self.data();session=Mock();response=self.response(status)
                session.post.return_value=response
                with self.assertRaisesRegex(ValueError,str(status)):
                    submit_feedback(session,data)
                response.close.assert_called_once()

    def test_confirmation_must_match_request_id(self):
        for payload in (None,[],dict(ok=True,id='another-request'),dict(ok=False,id='x')):
            data=self.data();session=Mock();session.post.return_value=self.response(payload=payload)
            with self.assertRaisesRegex(ValueError,'未确认'):
                submit_feedback(session,data)

    def test_timeout_preserves_request_for_manual_retry(self):
        data=self.data();session=Mock();session.post.side_effect=requests.exceptions.ReadTimeout()
        context=Mock();context.__enter__=Mock(return_value=session);context.__exit__=Mock(return_value=False)
        errors=[];success=[];task=FeedbackTask(data)
        task.signals.failed.connect(errors.append);task.signals.finished.connect(success.append)
        with patch('feedback.session_for_updates',return_value=context):task.run()
        self.assertFalse(success);self.assertIn('相同编号',errors[0])
        self.assertEqual(task.data,data)
        session.post.assert_called_once()

    def test_failed_form_keeps_text_and_reuses_id_on_retry(self):
        page=FeedbackPage();page.title.setText('反馈草稿测试');page.body.setPlainText('填写后发生故障时应保留全部内容并使用相同编号重试。');page.consent.setChecked(True)
        pool=Mock()
        try:
            with patch('feedback.QThreadPool.globalInstance',return_value=pool),patch('feedback.QMessageBox.warning'):
                page.send();first_id=page.draft['id']
                page.failed('HTTP 404')
                self.assertTrue(page.submit.isEnabled())
                self.assertIn('保留全部内容',page.body.toPlainText())
                page.send();self.assertEqual(page.draft['id'],first_id)
                page.failed('HTTP 404')
                page.body.appendPlainText('已补充新的说明。');page.send()
                self.assertNotEqual(page.draft['id'],first_id)
        finally:
            page.task=None;page.close();page.deleteLater();app.processEvents()

if __name__=='__main__':unittest.main()
