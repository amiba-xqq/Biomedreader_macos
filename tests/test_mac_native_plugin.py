import unittest,json,zipfile,xml.etree.ElementTree as ET
from pathlib import Path
import test_hidden_articles
from mac_word_plugin import pack,unpack,dispatch
class NativePluginTests(unittest.TestCase):
    def test_unicode_framing(self):
        values=['', '中文😀\nline', '000\nquote"']
        self.assertEqual(unpack(pack(values)),values)
        with self.assertRaises(ValueError):unpack('9\nshort')
    def test_render_keeps_record_metadata_and_rejects_missing_group(self):
        records=[dict(article=dict(article_key='a',title='Fixture',volume='12',pages='4-9'),group_id=1),dict(article=dict(article_key='b'),group_id=2)]
        def call(action,payload):return dict(valid=[True,False],keys=['a'],articles=[records[0]['article']],markers=['1'],references=['Fixture. 12:4-9.'],html={'markers':['<sup>1</sup>']})
        parts=dispatch('render',['Cell',*[json.dumps(r) for r in records]],call)
        self.assertEqual(parts[0],'2');self.assertEqual(parts[1:4],['1','1','1']);self.assertIn('<volume>12</volume>',parts[5]);self.assertIn('<pages>4-9</pages>',parts[5]);self.assertEqual(parts[6],'0')
    def test_production_template_ribbon_and_mac_callbacks_present(self):
        root=Path(__file__).resolve().parents[1]/'app/assets/mac_word'
        with zipfile.ZipFile(root/'BiomedReader.dotm') as z:
            self.assertIsNone(z.testzip());self.assertTrue(z.read('word/vbaProject.bin'));ET.fromstring(z.read('customUI/customUI14.xml'))
        source=(root/'BiomedReader.bas').read_text(encoding='utf8')
        self.assertIn('#Const BR_TEST = False',source);self.assertIn('AppleScriptTask(',source)
        for command in ('OpenReader','InsertReference','UpdateReferences','ChooseFont','ExportEndNote','SelectJournal'):self.assertIn('Public Sub '+command,source)
        self.assertIn('without altering line endings',(root/'BiomedReader.applescript').read_text(encoding='utf8'))
