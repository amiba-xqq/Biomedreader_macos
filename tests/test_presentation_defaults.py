"""Startup defaults must win over old stored views and session-only overrides."""
import unittest
import test_hidden_articles as shared
from PySide6.QtCore import QThreadPool
from ui import MainWindow, ResultsPage, ArticleCard


class PresentationDefaultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = shared.QApplication.instance() or shared.QApplication([])

    setUp = shared.HiddenArticleTests.setUp

    def page(self):
        page = ResultsPage(self.db, QThreadPool.globalInstance())
        self.addCleanup(lambda: (page.read_pool.waitForDone(), page.close(), page.deleteLater(), self.app.processEvents()))
        return page

    def test_upgrade_starts_compact_despite_legacy_table_and_density(self):
        self.db.set_profile_view(self.profile, 'table')
        self.db.save_settings({'keyword_card_density': 'loose'})
        page = self.page()
        page.load_profile(self.profile)
        self.assertEqual(page.view_mode.currentData(), 'cards')
        self.assertEqual(page.card_density.currentData(), 'compact')

    def test_each_configured_default_survives_reopen(self):
        for configured, view, density in [('cards_loose', 'cards', 'loose'),
                                           ('cards_compact', 'cards', 'compact'),
                                           ('table', 'table', 'compact')]:
            with self.subTest(configured=configured):
                self.db.save_settings({'keyword_default_view': configured})
                page = self.page()
                page.load_profile(self.profile)
                self.assertEqual((page.view_mode.currentData(), page.card_density.currentData()), (view, density))
                page.view_mode.setCurrentIndex(page.view_mode.findData('table' if view == 'cards' else 'cards'))
                page.card_density.setCurrentIndex(page.card_density.findData('loose' if density == 'compact' else 'compact'))
                reopened = self.page()
                reopened.load_profile(self.profile)
                self.assertEqual((reopened.view_mode.currentData(), reopened.card_density.currentData()), (view, density))
                self.assertEqual(self.db.get_settings()['keyword_default_view'], configured)

    def test_temporary_overrides_remain_profile_local_during_session(self):
        other = self.db.save_profile(dict(name='RNA', query_expression='RNA', journal_issns=[], scope_ids=[],
                                    minimum_jif=0, maximum_results=0, sort_mode='最新发表', search_sources='pubmed'))
        page = self.page()
        page.load_profile(self.profile)
        page.card_density.setCurrentIndex(page.card_density.findData('loose'))
        page.view_mode.setCurrentIndex(page.view_mode.findData('table'))
        page.load_profile(other)
        self.assertEqual((page.view_mode.currentData(), page.card_density.currentData()), ('cards', 'compact'))
        page.load_profile(self.profile)
        self.assertEqual((page.view_mode.currentData(), page.card_density.currentData()), ('table', 'loose'))

    def test_settings_selects_and_saves_all_three_options(self):
        window = MainWindow(self.db)
        self.addCleanup(lambda: (window.results.read_pool.waitForDone(), window.close(), window.deleteLater(), self.app.processEvents()))
        combo = window.settings_page.keyword_default_view
        self.assertEqual(combo.currentData(), 'cards_compact')
        self.assertEqual([combo.itemText(i) for i in range(combo.count())],
                         ['宽松的嵌入式卡片', '紧密的嵌入式卡片', '表格'])
        for value in ('cards_loose', 'table', 'cards_compact'):
            combo.setCurrentIndex(combo.findData(value))
            self.assertEqual(self.db.get_settings()['keyword_default_view'], value)

    def test_visited_doi_keeps_white_background_and_existing_faded_text(self):
        for source in ('PubMed', 'bioRxiv', 'medRxiv'):
            with self.subTest(source=source):
                card = ArticleCard({**self.article, 'source_name': source, 'doi_visited': True}, 'compact')
                self.assertIn('background:#FFFFFF', card.styleSheet())
                self.assertNotIn('#F1ECE6', card.styleSheet())
                self.assertIn('#82776F', card.abstract.styleSheet())
                self.assertIn('#D97983' if source != 'PubMed' else '#82776F', card.title_label.styleSheet())
                card.close()
                card.deleteLater()


if __name__ == '__main__':
    unittest.main(verbosity=2)
