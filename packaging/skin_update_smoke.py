from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QObject, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from database import Database
from interface_scale import scaled
from ui import APP_STYLE, MainWindow


def click(button):
    QTest.mouseClick(button,Qt.MouseButton.LeftButton)
    QApplication.processEvents()


def wait_until(predicate):
    expires = monotonic()+15
    while not predicate():
        assert monotonic()<expires, 'worker or window did not reach the expected state'
        QTest.qWait(20)


class OtherTask(QObject):
    result = None

    def completed(self,result):
        self.result = result


with TemporaryDirectory() as folder:
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat,QSettings.Scope.UserScope,folder)
    app = QApplication([])
    app.setStyleSheet(APP_STYLE)
    db = Database(Path(folder)/'library.sqlite3')
    db.create_favorite_group('普通分组','独有短关键词')
    db.create_favorite_group('长标题测试 '*45, '独有长关键词 '*90)
    window = MainWindow(db)
    window.resize(1370,850)
    window.show()
    window.show_skins()
    app.processEvents()
    page = window.skin_page
    ai = next(button for button in page.findChildren(QPushButton) if button.text()=='AI DIY皮肤包')
    click(ai)
    dialog = page.ai_editor
    assert dialog.parentWidget() is window and dialog.isVisible()
    assert dialog.windowModality()==Qt.WindowModality.NonModal and app.activeModalWidget() is None

    started,finish = Event(),Event()
    buffer = BytesIO()
    Image.new('RGB',(420,265),'#987254').save(buffer,'JPEG')
    image = buffer.getvalue()
    members = [f'cards/{index:02}.jpg' for index in range(8)]
    assets = dict.fromkeys(['wallpaper.jpg',*members],image)

    def render(*args):
        started.set()
        args[-1](1,9)
        assert finish.wait(15)
        return {'name':'并行测试','wallpaper':'wallpaper.jpg','cards':members},assets

    other = OtherTask(window)
    with patch('skin_ui.private_config',return_value={'model':'test-model'}), patch('skin_ui.generate_pack',side_effect=render):
        dialog.name.setText('并行测试')
        dialog.prompt.setPlainText('米色背景')
        click(dialog.generate)
        wait_until(started.is_set)
        assert dialog.task in window.ai_manager.jobs and not dialog.save.isEnabled()
        dialog.showMinimized()
        app.processEvents()
        assert dialog.isMinimized() and app.activeModalWidget() is None
        click(window.nav_buttons[1])
        assert window.pages.currentWidget() is window.favorites
        board = window.favorites.group_container
        assert len(board.cards)==2 and all(card.isVisible() for card in board.cards)
        assert {(card.width(),card.height()) for card in board.cards}=={(scaled(248),scaled(180))}
        for card in board.cards:
            assert [label.text() for label in card.findChildren(QLabel)] == [card.title_label.text(),'0 篇收藏文章']
            assert '关键词' not in card.toolTip()
        assert any(card.title_label.text().endswith('…') for card in board.cards)
        assert any('长标题测试 ' * 10 in card.toolTip() for card in board.cards)
        for index in range(4):
            db.create_favorite_group(f'分组 {index}','关键词')
        window.favorites.reload()
        window.resize(1000,760)
        QTest.qWait(80)
        assert len({card.y() for card in board.cards})>1
        assert {(card.width(),card.height()) for card in board.cards}=={(scaled(248),scaled(180))}
        click(board.cards[0])
        assert window.favorites.current_group
        window.ai_manager.submit(other,lambda:42,'completed','completed')
        wait_until(lambda:other.result is not None)
        assert other.result==42 and dialog.task in window.ai_manager.jobs

        window.show_skins()
        click(ai)
        assert page.ai_editor is dialog and not dialog.isMinimized()
        dialog.showMinimized()
        finish.set()
        wait_until(lambda:dialog.task is None)
        assert dialog.card_previews.count()==8 and dialog.save.isEnabled() and dialog.isMinimized()
        click(ai)
        click(dialog.save)
        wait_until(lambda:page.ai_editor is None)
        assert page.store.active().name=='并行测试'
    click(ai)
    assert page.ai_editor is not None and page.ai_editor is not dialog
    assert window.close() and page.ai_editor is None
    print('Passed fixed favorite group cards, parallel interaction and worker execution, minimized completion, restore, save, and cleanup.')
