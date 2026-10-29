"""PDF lifecycle and the actual PubMed requests produced by successive refreshes."""
import unittest,json,hashlib
from datetime import date,datetime,timezone
from pathlib import Path
from unittest.mock import Mock,patch
import test_hidden_articles as shared
from literature import SearchReport,LiteratureClient
from refresh import refresh_search
from source_modes import cache_mode,profile_settings
from ui import ArticleDialog

class Update1918Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=shared.QApplication.instance() or shared.QApplication([])
    setUp=shared.HiddenArticleTests.setUp

    def link(self):
        self.original=Path(self.temporary.name)/'download.pdf'
        self.original.write_bytes(b'%PDF-1.4\noriginal article\n%%EOF')
        self.db.set_favorite(self.article['article_key'],True,profile_id=self.profile)
        return self.db.link_pdf(self.article['article_key'],self.original)

    def test_final_cancellation_deletes_copy_not_original_or_cached_article(self):
        target=self.link();key=self.article['article_key']
        self.db.set_article_hidden(self.profile,self.article,True)
        self.db.set_favorite(key,False)
        self.assertFalse(target.exists());self.assertTrue(self.original.exists())
        self.assertIsNone(self.db.linked_pdf(key));self.assertEqual(self.db.result_count(self.profile,'pubmed'),1)
        self.assertTrue(self.db.hidden_article_tokens(self.profile))

    def test_cancellation_from_profile_removes_all_memberships(self):
        target=self.link();key=self.article['article_key']
        profile=dict(self.db.get_profile(self.profile));profile.pop('id');profile.update(name='Other',journal_issns=['0092-8674'])
        other=self.db.save_profile(profile)
        self.db.set_favorite(key,True,profile_id=other)
        self.db.set_favorite(key,False,profile_id=self.profile)
        self.assertFalse(target.exists());self.assertEqual(len(self.db.favorite_articles()),0)
        self.assertNotIn(key,self.db.favorite_keys_for_profile(other))

    def test_cancellation_also_removes_custom_group_membership(self):
        target=self.link();key=self.article['article_key']
        group,_=self.db.create_favorite_group('Custom','DNA')
        self.db.transfer_favorites([key],[group])
        self.db.set_favorite(key,False,profile_id=self.profile)
        self.assertFalse(target.exists());self.assertFalse(self.db.grouped_favorites().get('Custom'))
        self.db.set_favorite(key,False);self.assertFalse(target.exists())

    def test_locked_file_rolls_back_cancellation_and_preserves_link(self):
        target=self.link();key=self.article['article_key']
        with patch('local_maintenance.recycle_path',side_effect=PermissionError('file in use')):
            with self.assertRaises(PermissionError):self.db.set_favorite(key,False)
        self.assertTrue(target.exists());self.assertEqual(self.db.linked_pdf(key),target)
        self.assertEqual(len(self.db.favorite_articles()),1)
        self.assertIn(key,self.db.favorite_keys_for_profile(self.profile))

    def test_missing_pdf_removes_stale_link(self):
        target=self.link();target.unlink()
        self.db.set_favorite(self.article['article_key'],False)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM article_pdfs').fetchone()[0],0)

    def test_unsafe_filename_never_deletes_external_file(self):
        self.link();key=self.article['article_key']
        with self.db.connect() as connection:
            connection.execute("UPDATE article_pdfs SET filename='../download.pdf' WHERE article_key=?",(key,))
        self.db.set_favorite(key,False);self.assertTrue(self.original.exists())

    def test_shared_filename_retained_until_last_link_is_cancelled(self):
        target=self.link();other={**self.article,'article_key':'other','doi':'10.1/other','pmid':'99999'}
        self.db.store_refresh_results(self.profile,[(self.article,1),(other,1)],'pubmed')
        self.db.set_favorite('other',True)
        with self.db.connect() as connection:
            connection.execute("INSERT INTO article_pdfs VALUES(?,?,?,'data')",('other',target.name,datetime.now(timezone.utc).isoformat()))
        self.db.set_favorite(self.article['article_key'],False);self.assertTrue(target.exists())
        self.db.set_favorite('other',False);self.assertFalse(target.exists())

    def test_detail_disables_original_button_after_cancel(self):
        target=self.link();article={**self.article,'is_favorite':1}
        dialog=ArticleDialog(self.db,article,allow_pdf=True)
        before=dialog.browser.toPlainText()
        with patch('ui.confirm_unfavorite',return_value=True):dialog._toggle_favorite()
        self.assertFalse(target.exists());self.assertFalse(dialog.open_pdf_button.isEnabled())
        self.assertEqual(dialog.browser.toPlainText(),before)
        dialog.close();dialog.deleteLater();self.app.processEvents()

    def refresh(self,force=False,client=None):
        profile=self.db.get_profile(self.profile)
        client=client or Mock();client.search.return_value=SearchReport()
        report,checkpoint=refresh_search(self.db,client,profile,self.db.get_settings(),['0092-8674'],lambda _:None,lambda:False,force)
        self.db.store_refresh_results(self.profile,report.candidates,'pubmed',checkpoint)
        return report,client,checkpoint

    def test_preprint_only_preferences_do_not_invalidate_pubmed_checkpoint(self):
        self.refresh();self.db.save_settings({'biorxiv_recent_years':'5','preprint_servers':'both','preprint_search_scope':'title_abstract'})
        profile=self.db.get_profile(self.profile)
        with self.db.connect() as connection:
            connection.execute('UPDATE search_profiles SET biorxiv_maximum_results=? WHERE id=?',('50',self.profile))
        report,client,_=self.refresh()
        self.assertTrue(report.incremental);self.assertEqual(client.search.call_args.kwargs['updated_since'],date.today())
        self.assertEqual(client.search.call_args.kwargs['pubmed_maximum_results'],0)

    def test_legacy_signature_migrates_without_forced_full_refresh(self):
        profile=self.db.get_profile(self.profile)
        filters={name:profile.get(name) for name in ('query_expression','year_from','year_to','maximum_results','pubmed_maximum_results','biorxiv_maximum_results','article_type','minimum_jif')}
        filters.update(issns=['0092-8674'],sources='pubmed',biorxiv_years=2,schema=1)
        signature=hashlib.sha256(json.dumps(filters,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        now=datetime.now(timezone.utc).isoformat()
        self.db.store_refresh_results(self.profile,self.rows,'pubmed',dict(signature=signature,started_at=now,completed_at=now))
        report,_,checkpoint=self.refresh();self.assertTrue(report.incremental)
        self.assertNotEqual(checkpoint['signature'],signature)

    def test_first_then_second_refresh_uses_real_pubmed_date_parameters(self):
        def request():
            client=LiteratureClient('');client.session=Mock()
            response=Mock();response.json.return_value={'esearchresult':{'count':'0','idlist':[]}}
            client.session.get.return_value=response
            return client
        profile=self.db.get_profile(self.profile)
        first=request()
        with patch('literature.time.sleep'):
            report,checkpoint=refresh_search(self.db,first,profile,self.db.get_settings(),['0092-8674'],lambda _:None,lambda:False)
        self.assertFalse(report.incremental)
        self.assertNotIn('mindate',first.session.get.call_args.kwargs['params'])
        self.db.store_refresh_results(self.profile,report.candidates,'pubmed',checkpoint)
        second=request()
        with patch('literature.time.sleep'):
            report,_=refresh_search(self.db,second,profile,self.db.get_settings(),['0092-8674'],lambda _:None,lambda:False)
        self.assertTrue(report.incremental)
        for call in second.session.get.call_args_list:
            params=call.kwargs['params'];self.assertEqual(params['datetype'],'pdat')
            self.assertEqual(params['mindate'],date.today().strftime('%Y/%m/%d'))
            self.assertEqual(params['maxdate'],date.today().strftime('%Y/%m/%d'))

    def test_dense_pubmed_pagination_stays_inside_incremental_days(self):
        client=LiteratureClient('');client.session=Mock()
        payloads=[{'count':'10000','idlist':[]},{'count':'10000','idlist':[]},{'count':'1','idlist':['1']},{'count':'1','idlist':['2']}]
        responses=[]
        for payload in payloads:
            response=Mock();response.json.return_value={'esearchresult':payload};responses.append(response)
        client.session.get.side_effect=responses
        start=date(2026,10,14);end=date(2026,10,16)
        with patch('literature.time.sleep'):
            result=client.pubmed_ids('DNA','pub_date',None,SearchReport(),lambda _:None,0,date_from=start,date_to=end)
        self.assertEqual(result,['1','2'])
        for call in client.session.get.call_args_list:
            params=call.kwargs['params'];self.assertEqual(params['mindate'],'2026/10/14');self.assertEqual(params['maxdate'],'2026/10/16')
            self.assertNotIn('1000/',params['term'])

if __name__=='__main__':unittest.main(verbosity=2)
