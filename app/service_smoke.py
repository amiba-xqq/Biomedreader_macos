"""Packaged checks of the public service routes and client success handling."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

import requests

from ai_service import AIAPIError
from feedback import run_feedback_self_test, submit_feedback, validated_feedback
from lab_service import LAB_CUSTOM_URL, LabAPI


def response(status, payload):
    reply = requests.Response()
    reply.status_code = status
    reply._content = json.dumps(payload).encode()
    return reply


def verify_success_handling():
    origin = LAB_CUSTOM_URL + '/api/lab'
    feedback = validated_feedback('使用问题', '自动测试', 'Synthetic fixture for client response validation.')

    def handle(session, method, url, **kwargs):
        payload = kwargs['json']
        assert method == 'POST' and not kwargs['allow_redirects']
        if url == origin + '/pi/login':
            assert payload['provider'] == 'deepseek' and payload['api_key'] == 'synthetic-test-key'
            role = 'pi'
        elif url == origin + '/student/login':
            assert payload['user_id'] == 'synthetic-student-access'
            role = 'student'
        else:
            raise AssertionError('Unexpected login URL')
        assert payload['login_nonce']
        return response(200, {'ok': True, 'token': 'synthetic-session', 'identity': {'role': role}})

    api = LabAPI(gateway=LAB_CUSTOM_URL)
    with patch.object(requests.Session, 'request', handle):
        pi = api.login_pi('deepseek', 'synthetic-test-key')
        student = api.login_student('synthetic-student-access')
    assert pi['identity'] == {'role': 'pi', 'gateway_url': LAB_CUSTOM_URL}
    assert student['identity'] == {'role': 'student', 'gateway_url': LAB_CUSTOM_URL}

    def feedback_response(session, url, **kwargs):
        assert url == LAB_CUSTOM_URL + '/api/feedback'
        assert json.loads(kwargs['data']) == {**feedback, 'website': ''}
        assert not kwargs['allow_redirects']
        return response(200, {'ok': True, 'id': feedback['id'], 'notified': True})

    with requests.Session() as session, patch.object(requests.Session, 'post', feedback_response):
        assert submit_feedback(session, feedback)['notified'] is True


def run(destination):
    results = {}
    try:
        verify_success_handling()
        results['synthetic_client_success'] = True
    except Exception as error:
        results['synthetic_client_success'] = type(error).__name__

    api = LabAPI(gateway=LAB_CUSTOM_URL)
    for name, call, expected in (
        ('pi_route', lambda: api.login_pi('invalid-smoke-provider', 'synthetic-test-key'), '此平台'),
        ('student_route', lambda: api.login_student('invalid-smoke-' + uuid4().hex), '学生访问ID无效'),
    ):
        try:
            call()
            results[name] = 'unexpected_acceptance'
        except AIAPIError as error:
            results[name] = True if expected in str(error) else type(error).__name__
        except Exception as error:
            results[name] = type(error).__name__

    with TemporaryDirectory() as temp:
        probe = Path(temp) / 'feedback.json'
        results['feedback_route'] = run_feedback_self_test(probe) == 0
        results['feedback_http_status'] = json.loads(probe.read_text(encoding='utf-8')).get('http_status')
    results['live_signin_completed'] = False
    results['live_email_sent'] = False
    passed = all(results[name] is True for name in ('synthetic_client_success', 'pi_route', 'student_route', 'feedback_route'))
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=2), encoding='utf-8')
    return 0 if passed else 1
