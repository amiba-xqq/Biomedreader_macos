"""Reusable draggable cards using the same gesture and ordering behavior as search cards."""
from PySide6.QtCore import Qt,Signal
from PySide6.QtWidgets import QFrame,QVBoxLayout,QLabel,QWidget,QProgressBar
from keyword_cards import KeywordBoard,KeywordCard

class DirectoryCard(QFrame):
    clicked=Signal(object)
    mousePressEvent=KeywordCard.mousePressEvent
    mouseMoveEvent=KeywordCard.mouseMoveEvent
    mouseReleaseEvent=KeywordCard.mouseReleaseEvent
    def __init__(self,board,identifier,title,subtitle):
        super().__init__(board)
        self.board=board;self.identifier=identifier;self.custom_width=0;self.busy=False;self.press_position=None
        self.setObjectName('Card');self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip('单击打开；按住拖动调整卡片顺序。')
        layout=QVBoxLayout(self);layout.setContentsMargins(14,14,14,14)
        heading=QLabel(title);heading.setWordWrap(True);heading.setTextFormat(Qt.TextFormat.PlainText)
        heading.setStyleSheet('font-size:13pt;font-weight:700;border:none;')
        note=QLabel(subtitle);note.setWordWrap(True);note.setStyleSheet('color:#8B7768;border:none;')
        layout.addWidget(heading);layout.addStretch();layout.addWidget(note)
        self.import_status=QLabel();self.import_status.setWordWrap(True);self.import_status.setStyleSheet('font-size:9pt;color:#C96812;border:none;')
        self.import_progress=QProgressBar();self.import_progress.setFixedHeight(8);self.import_progress.setTextVisible(False)
        self.import_progress.setStyleSheet('QProgressBar {background:#FFF3E0;border:none;} QProgressBar::chunk {background:#F28C20;}')
        layout.addWidget(self.import_status);layout.addWidget(self.import_progress)
        self.import_status.hide();self.import_progress.hide()
        for child in self.findChildren(QWidget):child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,True)

    def set_import_progress(self,completed,total,text):
        self.busy=True;self.import_progress.setRange(0,max(1,total));self.import_progress.setValue(completed)
        self.import_status.setText(text);self.import_status.show();self.import_progress.show()

class CardBoard(KeywordBoard):
    def __init__(self,columns=4):
        super().__init__(columns=columns,card_height=180)
        self.empty.setText('暂无卡片，可添加一个分组或目录。')
    def count(self):return len(self.cards)
    def add_card(self,identifier,title,subtitle,clicked):
        card=DirectoryCard(self,identifier,title,subtitle)
        card.clicked.connect(clicked);self.cards.append(card)
        self.schedule_reflow();return card
