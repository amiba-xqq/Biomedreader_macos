"""Large mixed caches, independent endpoint checkpoints and missing-record recovery."""
import unittest,sqlite3,tempfile
from pathlib import Path
from datetime import date
from unittest.mock import Mock,patch
import test_hidden_articles as shared
from database import Database
from literature import LiteratureClient,SearchReport
from refresh import refresh_search

def paper(index=0,source='PubMed'):
    article,score=LiteratureClient('').normalize_biorxiv(dict(doi=f'10.1101/test{index}',title='DNA replication',abstract='DNA repair and replication',authors='A',date=str(date.today()),version='1'),'DNA')
    article.update(source_name=source,journal_title='Cell',issn='0092-8674',publication_types=['Journal Article'])
    return article,score


from datetime import datetime,timezone

class Refresh1919Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=shared.QApplication.instance() or shared.QApplication([])
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.folder=Path(self.temporary.name)
        self.db=Database(self.folder/'data.sqlite3')
    def profile(self,**extra):
        values=dict(name='DNA replication',query_expression='DNA',journal_issns=['0092-8674'],scope_ids=[],minimum_jif=0,maximum_results=0,sort_mode='最新发表')
        values.update(extra);return self.db.save_profile(values)

    def mixed_profile(self):
        self.db.save_settings({'preprint_search_scope':'title_abstract','biorxiv_recent_years':'1','preprint_servers':'biorxiv','pubmed_search_scope':'default'})
        return self.profile(query_expression='DNA replication',search_sources='both',pubmed_maximum_results=0,biorxiv_maximum_results=0)

    def refresh_with(self,identifier,report,force=False):
        client=Mock();client.search.return_value=report
        def search(*args,**kwargs):
            if 'PubMed' in report.completed_sources and kwargs.get('cached_pubmed'):
                report.candidates=[(article,float(article.get('relevance_score') or 0)) for article in kwargs['cached_pubmed'].values()]+report.candidates
            return report
        client.search.side_effect=search
        profile=self.db.get_profile(identifier)
        result,checkpoint=refresh_search(self.db,client,profile,self.db.get_settings(),['0092-8674'],lambda _:None,lambda:False,force)
        self.db.store_refresh_results(identifier,result.candidates,'both_title_abstract_website',checkpoint,partial=bool(result.source_errors))
        return result,client,checkpoint

    def large_rows(self):
        template,_=paper(1);today=date.today()
        rows=[({**template,'article_key':f'pmid:{i}','doi':'','pmid':str(i),'title':'DNA replication '+str(i),
                'publication_date':str(today),'publication_year':today.year,'source_name':'PubMed'},1.0) for i in range(24000)]
        bio,_=paper(24001,'bioRxiv');bio.update(publication_date=str(today),publication_year=today.year)
        rows.append((bio,1.0));return rows

    def test_24001_cached_articles_use_daily_incremental_after_complete_baseline(self):
        identifier=self.mixed_profile();now=datetime.now(timezone.utc).isoformat()
        self.refresh_with(identifier,SearchReport(candidates=self.large_rows(),completed_sources={'PubMed':now,'bioRxiv':now}))
        result,client,_=self.refresh_with(identifier,SearchReport(completed_sources={'PubMed':now,'bioRxiv':now}))
        self.assertTrue(result.incremental);self.assertEqual(len(result.candidates),24001)
        self.assertEqual(client.search.call_args.kwargs['updated_since'],date.today())
        self.assertEqual(set(result.refresh_modes.values()),{'增量检索'})

    def test_24001_partial_baseline_keeps_biorxiv_incremental_and_resumes_pubmed(self):
        identifier=self.mixed_profile();now=datetime.now(timezone.utc).isoformat()
        result,_,checkpoint=self.refresh_with(identifier,SearchReport(candidates=self.large_rows(),
            source_errors=['PubMed（pub_date）：timeout'],completed_sources={'bioRxiv':now}))
        self.assertIsNone(checkpoint)
        self.assertIsNone(self.db.refresh_checkpoint(identifier,'both_title_abstract_website'))
        self.assertFalse(self.db.get_profile(identifier)['last_refreshed_at'])
        new,_=paper(25000);new['pmid']='25000'
        result,client,checkpoint=self.refresh_with(identifier,SearchReport(candidates=[(new,1)],completed_sources={'PubMed':now,'bioRxiv':now}))
        kwargs=client.search.call_args.kwargs
        self.assertEqual(kwargs['source_updated_since'],{'bioRxiv':date.today(),'PubMed':None})
        self.assertEqual(len(kwargs['cached_pubmed']),24000)
        self.assertEqual(result.refresh_modes['PubMed'],'续查未完成检索')
        self.assertEqual(result.refresh_modes['bioRxiv'],'增量检索')
        self.assertEqual(len(result.candidates),24002);self.assertIsNotNone(checkpoint)
        third,client,_=self.refresh_with(identifier,SearchReport(completed_sources={'PubMed':now,'bioRxiv':now}))
        self.assertEqual(set(third.refresh_modes.values()),{'增量检索'})

    def test_biorxiv_failure_does_not_erase_completed_pubmed_source(self):
        identifier=self.mixed_profile();now=datetime.now(timezone.utc).isoformat();row=paper(1)
        client=Mock();client.search.return_value=SearchReport(candidates=[row],source_errors=['bioRxiv：timeout'],completed_sources={'PubMed':now})
        with self.assertRaises(ValueError):refresh_search(self.db,client,self.db.get_profile(identifier),self.db.get_settings(),['0092-8674'],lambda _:None,lambda:False)
        states=self.db.source_refresh_checkpoints(identifier,'both_title_abstract_website')
        self.assertEqual(states['PubMed']['completed_at'],now)
        result,client,_=self.refresh_with(identifier,SearchReport(completed_sources={'PubMed':now,'bioRxiv':now}))
        self.assertEqual(client.search.call_args.kwargs['source_updated_since'],{'PubMed':date.today(),'bioRxiv':None})

    def test_full_search_ignores_recovery_and_daily_checkpoints(self):
        identifier=self.mixed_profile();now=datetime.now(timezone.utc).isoformat()
        self.refresh_with(identifier,SearchReport(candidates=[paper(1)],source_errors=['PubMed：timeout'],completed_sources={'bioRxiv':now}))
        result,client,_=self.refresh_with(identifier,SearchReport(candidates=[paper(2)],completed_sources={'PubMed':now,'bioRxiv':now}),force=True)
        self.assertFalse(result.incremental);self.assertIsNone(client.search.call_args.kwargs['updated_since'])
        self.assertNotIn('cached_pubmed',client.search.call_args.kwargs);self.assertEqual(len(result.candidates),1)

    def test_pubmed_recovery_fetches_only_missing_ids(self):
        client=LiteratureClient('');client.session=Mock();article,_=paper(1);article['pmid']='1'
        fetched,_=paper(2);fetched['pmid']='2'
        response=Mock();response.content=b'<PubmedArticleSet><PubmedArticle/></PubmedArticleSet>';client.session.get.return_value=response
        with patch.object(client,'pubmed_ids',return_value=['1','2']),patch.object(client,'normalize_pubmed',return_value=(fetched,1)),patch('literature.time.sleep'):
            report=client.search('DNA replication',['0092-8674'],0,sources=('PubMed',),cached_pubmed={'1':article})
        self.assertEqual(client.session.get.call_args.kwargs['params']['id'],'2')
        self.assertEqual(len(report.candidates),2);self.assertIn('PubMed',report.completed_sources)

    def test_per_source_records_and_checkpoint_rollback_together(self):
        identifier=self.mixed_profile();now=datetime.now(timezone.utc).isoformat()
        invalid={'article_key':'broken'}
        with self.assertRaises((KeyError,sqlite3.Error)):
            self.db.store_refresh_results(identifier,[(invalid,1)],'both_title_abstract_website',partial=True,
                source_checkpoints={'bioRxiv':dict(signature='test',started_at=now,completed_at=now)})
        self.assertFalse(self.db.source_refresh_checkpoints(identifier,'both_title_abstract_website'))

    def test_actual_parallel_clients_receive_their_own_date_bounds(self):
        client=LiteratureClient('');bio_day=date(2026,10,16);pubmed_day=date(2026,10,14)
        with patch('literature.search_website',return_value=[]) as website,patch.object(LiteratureClient,'pubmed_ids',return_value=[]) as ids:
            report=client.search('DNA replication',['0092-8674'],0,sources=('bioRxiv','PubMed'),
                source_updated_since={'bioRxiv':bio_day,'PubMed':pubmed_day},biorxiv_website_scope='title_abstract')
        self.assertEqual(website.call_args.kwargs['start_date'],bio_day)
        self.assertEqual(ids.call_args.kwargs['date_from'],pubmed_day)
        self.assertEqual(set(report.completed_sources),{'bioRxiv','PubMed'})

    def test_both_failed_sources_record_pending_baselines_for_recovery(self):
        identifier=self.mixed_profile();row,score=paper(1);row['pmid']='1'
        client=Mock();client.search.return_value=SearchReport(candidates=[(row,score)],
            source_errors=['bioRxiv：timeout','PubMed：timeout'],source_completion_tracked=True)
        with self.assertRaises(ValueError):refresh_search(self.db,client,self.db.get_profile(identifier),self.db.get_settings(),['0092-8674'],lambda _:None,lambda:False)
        states=self.db.source_refresh_checkpoints(identifier,'both_title_abstract_website')
        self.assertEqual(len(states),2);self.assertTrue(all(not state['completed_at'] for state in states.values()))
        now=datetime.now(timezone.utc).isoformat()
        result,client,_=self.refresh_with(identifier,SearchReport(completed_sources={'PubMed':now,'bioRxiv':now}))
        self.assertEqual(len(client.search.call_args.kwargs['cached_pubmed']),1)
        self.assertEqual(set(result.refresh_modes.values()),{'续查未完成检索'})

    def test_legacy_24001_cache_without_checkpoint_revalidates_ids_and_reuses_metadata(self):
        identifier=self.mixed_profile();rows=self.large_rows()
        self.db.store_refresh_results(identifier,rows,'both_title_abstract_website')
        now=datetime.now(timezone.utc).isoformat()
        report,client,_=self.refresh_with(identifier,SearchReport(completed_sources={'PubMed':now,'bioRxiv':now}))
        self.assertFalse(report.incremental)
        self.assertEqual(len(client.search.call_args.kwargs['cached_pubmed']),24000)
        self.assertEqual(report.refresh_modes['PubMed'],'续查未完成检索')
        self.assertIsNone(client.search.call_args.kwargs['updated_since'])
        self.assertTrue(self.refresh_with(identifier,SearchReport(completed_sources={'PubMed':now,'bioRxiv':now}))[0].incremental)

if __name__=='__main__':
    names=[name for name in Refresh1919Tests.__dict__ if name.startswith('test_')]
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(Refresh1919Tests(name) for name in sorted(names)))
    raise SystemExit(not result.wasSuccessful())
