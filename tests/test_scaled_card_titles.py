import unittest
from runtime_fixture import app,article,Runtime22Tests
from PySide6.QtCore import QPoint,Qt,QCoreApplication,QEvent
from PySide6.QtWidgets import QWidget,QVBoxLayout,QLabel
from ui import ArticleCard,APP_STYLE
from keyword_cards import KeywordBoard,KeywordCard
from card_board import CardBoard
from interface_scale import InterfaceScale,scale_style

def drain():
    for _ in range(15):app.processEvents()

class Tests(unittest.TestCase):
    setUp=Runtime22Tests.setUp
    def tearDown(self):
        app.biomed_scale=1;app.setStyleSheet(APP_STYLE);Runtime22Tests.tearDown(self)
    def test_pending_title_refresh_is_safe_when_card_is_destroyed(self):
        import shiboken6
        card=ArticleCard(article('pmid:disposed'));title=card.title_label;title.queue_refresh()
        card.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        self.assertFalse(shiboken6.isValid(title));app.processEvents()
    def test_dynamic_cards_created_after_zoom_have_scaled_complete_titles(self):
        for zoom in (1,1.5,2):
            app.biomed_scale=zoom;app.setStyleSheet(scale_style(APP_STYLE,zoom))
            for density in ('compact','loose'):
                row=article('pmid:scale')
                row.update(title='LMNA R527C pathogenic variant causes an inflammation-driven segmental progeroid syndrome via triggering DNA-sensing pathways. '+('Mechanisms of transcription and DNA replication coordination. '*4),abstract='Abstract evidence. '*80,jif=18.2)
                card=ArticleCard(row,density);card.resize(390,card.height());card.show();drain()
                title=card.title_label
                self.assertAlmostEqual(title.font().pointSizeF(),(11 if density=='compact' else 12)*zoom,delta=.1)
                self.assertEqual(title.text(),row['title']);self.assertNotIn('…',title.text())
                self.assertGreaterEqual(title.height(),title.heightForWidth(title.width()))
                self.assertGreaterEqual(card.abstract.y()-title.mapTo(card,QPoint()).y()-title.height(),5)
                self.assertGreaterEqual(card.abstract.height(),round(35*zoom))
                if zoom==2:self.assertGreater(card.height(),round(card._base_card_height*zoom))
                card.close();card.deleteLater();drain()
    def test_directory_and_keyword_titles_grow_and_survive_narrow_columns(self):
        for zoom in (1,2):
            app.biomed_scale=zoom;app.setStyleSheet(scale_style(APP_STYLE,zoom))
            board=KeywordBoard();board.resize(1100,700)
            title='Transcription replication conflict coordination and clinical research 研究方向与临床意义 '*3
            profile=dict(id=1,name=title,article_count=100,sort_mode='最新发表',last_refreshed_at='')
            card=KeywordCard(board,profile,'从未刷新');board.cards.append(card);board.show();board.reflow();drain()
            self.assertAlmostEqual(card.title_label.font().pointSizeF(),11*zoom,delta=.1)
            self.assertGreaterEqual(card.title_label.height(),card.title_label.heightForWidth(card.title_label.width()))
            self.assertLess(card.title_label.geometry().bottom(),card.ai_report_button.mapTo(card,QPoint()).y())
            directory=CardBoard(columns=5);directory.resize(1100,700)
            item=directory.add_card('id',title,'8篇收藏文章',lambda x:None);directory.show();directory.reflow();drain()
            self.assertAlmostEqual(item.title_label.font().pointSizeF(),13*zoom,delta=.1)
            self.assertGreaterEqual(item.title_label.height(),item.title_label.heightForWidth(item.title_label.width()))
            board.close();directory.close();board.deleteLater();directory.deleteLater();drain()
    def test_repeated_scale_changes_do_not_compound_title_font(self):
        app.biomed_scale=1;app.setStyleSheet(APP_STYLE)
        window=QWidget();window.sidebar=QWidget(window);window.results=QWidget(window);window.favorites=QWidget(window)
        layout=QVBoxLayout(window);row=article('pmid:repeat');row['title']='Complete title without clipping '*8
        card=ArticleCard(row);layout.addWidget(card);window.resize(600,500);window.show()
        controller=InterfaceScale(window,self.db,APP_STYLE)
        try:
            for value in (200,150,100,200,100):
                self.db.save_settings(dict(interface_auto_scale='0',interface_scale_percent=str(value)))
                controller.apply();drain()
                self.assertAlmostEqual(card.title_label.font().pointSizeF(),12*value/100,delta=.1)
                self.assertEqual(card.title_label.text(),row['title'])
        finally:controller.stop();window.close();window.deleteLater();drain()

if __name__=='__main__':unittest.main(defaultTest='Tests')
