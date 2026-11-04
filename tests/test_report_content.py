import unittest,json
from types import SimpleNamespace
from unittest.mock import Mock,patch
from runtime_fixture import Runtime22Tests,app,article
from ai_analysis import AnalysisEngine
from ai_ui import AIManager
from ai_workflow_ui import AIReportDialog

data={'scientific_question':'What remains unknown?','highlights':['Finding'],'innovations':['Method'],'news':{'background':'Background','main_discovery':'First. Second.','research':[{'heading':'Evidence','body':'Body'}],'contribution':'Contribution'}}
class Tests(unittest.TestCase):
 setUp=Runtime22Tests.setUp
 tearDown=Runtime22Tests.tearDown
 def test_report_default_brief_and_continuation_preserves_other_part(self):
  engine=object.__new__(AnalysisEngine);engine.calls=0;engine.tokens=0
  engine.reduce=Mock(return_value=(json.dumps(data),False));engine.result=lambda text,**kw:dict(text=text,calls=0,total_tokens=0,**kw)
  document=dict(text='Original research text',coverage='Full text',url='fixture',fulltext=True)
  with patch('ai_analysis.read_article',return_value=document) as read:
   brief=engine.summarize(self.db,self.row,include_diagram=False,content_mode='brief')
   self.assertIn('只生成中英文简报',engine.reduce.call_args.args[2]);self.assertEqual(brief['report_content_modes'],['brief'])
   news=engine.summarize(self.db,self.row,include_diagram=False,content_mode='news',previous=brief)
   self.assertEqual(read.call_count,1);self.assertEqual(set(news['report_content_modes']),{'brief','news'});self.assertEqual(news['presentation']['highlights'],['Finding'])
   self.assertIn('上次分析',engine.reduce.call_args.args[0])
 def test_missing_report_part_is_blank_and_can_be_requested(self):
  self.db.store_refresh_results(self.profile,[(self.row,1)],'pubmed');manager=AIManager(self.db)
  manager.repository.save_summary(self.row['article_key'],{'text':'fixture','presentation':data},'profile:'+str(self.profile))
  report=dict(keys=[self.row['article_key']],window={},errors={},content_modes={self.row['article_key']:['brief']})
  coordinator=SimpleNamespace(manager=manager,drafts={self.profile:report},running=set(),changed=manager.changed,window=SimpleNamespace(settings_page=None))
  dialog=AIReportDialog(coordinator,self.profile);dialog.open_article(self.row['article_key']);dialog.mode.setCurrentIndex(dialog.mode.findData('news'))
  self.assertEqual(dialog.presentation.news.toPlainText(),'');self.assertEqual(dialog.render_button.text(),'生成新闻稿')
  dialog.approved=True;dialog.close()
if __name__=='__main__':unittest.main()
