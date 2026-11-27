import io
import json
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from requests import Response

from ai_service import AIClient, AIAPIError
from image_routes import ark_request_error, bailian_request_error, SEEDREAM_55
from skin_ai import EXCLUSIONS, RESTRICTED_WORDS, generate_pack


def reply(status,body):
    response = Response()
    response.status_code = status
    response._content = json.dumps(body).encode('utf-8')
    return response


class SkinPromptTests(unittest.TestCase):
    def test_drawing_requests_use_only_the_visible_brief(self):
        image = io.BytesIO()
        Image.new('RGB',(320,240),'#658272').save(image,'JPEG')
        plan = {'prompt':'写实近距离自然景色，包含花草鸟树。'+EXCLUSIONS,
                'cards':['树间鸟巢。不要画暴力海报']*8}
        for provider in ('ark','bailian','glm','openai','codex'):
            with self.subTest(provider=provider), patch(
                'skin_ai.private_config',return_value={'model':SEEDREAM_55 if provider=='ark' else 'fixture'}), patch(
                'skin_ai.AnalysisEngine') as engine, patch('skin_ai.image_routes.generate_on_channel',return_value=image.getvalue()) as render:
                engine.return_value.call.return_value = json.dumps(plan,ensure_ascii=False)
                engine.return_value.draw_pixels.return_value = image.getvalue()
                meta,_ = generate_pack(Mock(),'deepseek',None,provider,'自然','生成写实的近距离自然景色，可以包含花草鸟树等相关内容',[])
                self.assertEqual(len(meta['cards']),8)
                self.assertIn(EXCLUSIONS,engine.return_value.call.call_args.args[0])
                if provider in ('openai','codex'):
                    instructions = [call.args[0]+'\n'+call.args[2] for call in engine.return_value.draw_pixels.call_args_list]
                else:
                    instructions = [call.args[2] for call in render.call_args_list]
                self.assertEqual(len(instructions),9)
                self.assertTrue(all('近距离自然景色' in prompt for prompt in instructions))
                self.assertTrue(all(not RESTRICTED_WORDS.search(prompt) for prompt in instructions))

    def test_content_responses_are_reported_without_retry_or_raw_messages(self):
        ark = reply(400,{'error':{'code':'InputTextSensitiveContentDetected','message':'private-example'}})
        ark.headers['x-request-id'] = 'fixture-request-id'
        message = ark_request_error(ark)
        self.assertIn('绘图提示未通过服务商的内容审核',message)
        self.assertIn('fixture-request-id',message)
        self.assertNotIn('private-example',message)
        self.assertIn('参考图未通过',ark_request_error(reply(400,{'error':{'code':'InputImageSensitiveContentDetected'}})))
        self.assertIn('内容审核',bailian_request_error(reply(400,{'code':'DataInspectionFailed'})))
        session = Mock()
        session.headers = {}
        session.request.return_value = reply(400,{'error':{'code':'moderation_blocked','message':'private-example'}})
        client = AIClient('openai','fixture-key',session)
        with self.assertRaisesRegex(AIAPIError,'内容审核'):
            client.generate_pixels('fixture','创作桌面背景','花草树木',{'images':[]})
        session.request.assert_called_once()


if __name__=='__main__':
    unittest.main()
