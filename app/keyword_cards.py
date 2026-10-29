"""A five-column default board with independent card widths and local drag ordering."""
from interface_scale import scaled,factor
import json
import uuid
from datetime import datetime
from PySide6.QtCore import Qt, QPoint, QRect, QMimeData, Signal, QTimer
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QWidget, QFrame, QLabel, QVBoxLayout, QProgressBar, QApplication, QMenu, QLayout,QPushButton,QHBoxLayout

MIME = 'application/x-biomed-reader-keyword-card'


class WidthHandle(QFrame):
    """Keep resize presses separate from card activation and drag gestures."""
    def __init__(self, card):
        super().__init__(card)
        self.card = card
        self.origin = None
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setToolTip('拖动右边缘调整本张卡片宽度')
        self.setStyleSheet('background:rgba(242,140,32,55);border-radius:3px;')

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.origin = event.globalPosition().toPoint()
            self.initial_width = self.card.width()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.origin is not None:
            width = self.initial_width + event.globalPosition().toPoint().x() - self.origin.x()
            width = max(140, min(1800, self.card.board.width()-20, width))
            self.card.custom_width = round(width/factor())
            self.card.board.reflow()
            event.accept()

    def mouseReleaseEvent(self, event):
        if self.origin is not None and event.button() == Qt.MouseButton.LeftButton:
            self.origin = None
            self.card.board.width_changed.emit(self.card.identifier, self.card.custom_width)
            event.accept()


class KeywordCard(QFrame):
    clicked = Signal(int)
    report_requested=Signal(int)

    def __init__(self, board, profile, refreshed):
        super().__init__(board)
        self.board = board
        self.identifier = int(profile['id'])
        self.custom_width = int(profile.get('card_width') or 0)
        self.press_position = None
        self.busy = False
        self.setObjectName('Card')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip('单击查看缓存；按住卡片拖动调整顺序；拖动右边缘调整宽度；右键恢复自动宽度。')
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        layout.setContentsMargins(10,10,16,10)
        layout.setSpacing(5)
        stripe = QFrame()
        stripe.setFixedHeight(5)
        stripe.setStyleSheet('background:#F28C20;border:none;border-radius:2px;')
        title = QLabel(profile['name'])
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        title.setFixedHeight(56)
        title.setStyleSheet('font-size:11pt;font-weight:700;border:none;')
        title.setToolTip(profile['name'])
        subtitle = QLabel(f"{profile['article_count']} 篇缓存（所有来源）\n默认排序：{profile['sort_mode']}\n最后刷新：{refreshed}")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet('font-size:9pt;color:#8B7768;border:none;')
        self.search_label = QLabel('正在联网搜索……')
        self.search_label.setStyleSheet('font-size:9pt;color:#C96812;border:none;')
        self.search_label.setWordWrap(True)
        self.search_progress = QProgressBar()
        self.search_progress.setRange(0,0)
        self.search_progress.setTextVisible(False)
        self.search_progress.setFixedHeight(7)
        self.search_progress.setStyleSheet('QProgressBar {background:#FFF3E0;border:none;} QProgressBar::chunk {background:#F28C20;}')
        for widget in (stripe,title):
            layout.addWidget(widget)
        layout.addStretch()
        for widget in (subtitle,self.search_label,self.search_progress):
            layout.addWidget(widget)
        self.elapsed_label=QLabel()
        self.elapsed_label.setStyleSheet('font-size:9pt;color:#8B7768;border:none;')
        layout.addWidget(self.elapsed_label)
        self.refreshed_at=profile.get('last_refreshed_at')
        self.update_elapsed()
        self.elapsed_timer=QTimer(self);self.elapsed_timer.setInterval(60000)
        self.elapsed_timer.timeout.connect(self.update_elapsed);self.elapsed_timer.start()
        for child in self.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row=QHBoxLayout();row.addStretch();self.ai_report_button=QPushButton('AI报告');self.ai_report_button.setProperty('secondary',True)
        self.ai_report_button.clicked.connect(lambda:self.report_requested.emit(self.identifier));row.addWidget(self.ai_report_button);layout.addLayout(row)
        self.handle = WidthHandle(self)
        self.set_busy('')

    def update_elapsed(self):
        try:
            stamp=datetime.fromisoformat(self.refreshed_at.replace('Z','+00:00')).astimezone()
            days=max(0,(datetime.now().astimezone().date()-stamp.date()).days)
            self.elapsed_label.setText(f'距上次刷新：{days} 天');self.elapsed_label.show()
        except (AttributeError,ValueError,TypeError):self.elapsed_label.hide()

    def set_busy(self, message):
        self.busy = bool(message)
        self.search_label.setVisible(self.busy)
        self.search_progress.setVisible(self.busy)
        self.search_label.setToolTip(message)
        self.board.schedule_reflow()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.handle.setGeometry(self.width()-7,12,6,max(1,self.height()-24))
        self.handle.raise_()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.press_position = event.position().toPoint()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.press_position is None or not event.buttons() & Qt.MouseButton.LeftButton:
            return
        if (event.position().toPoint()-self.press_position).manhattanLength() < QApplication.startDragDistance():
            return
        self.press_position = None
        drag = QDrag(self)
        drag.setMimeData(self.board.mime_for(self.identifier))
        drag.setPixmap(self.grab())
        drag.setHotSpot(QPoint(min(30,self.width()-1),20))
        drag.exec(Qt.DropAction.MoveAction)
        self.board.scroll_timer.stop()
        self.board.indicator.hide()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.press_position is not None:
            self.press_position = None
            if self.rect().contains(event.position().toPoint()):
                self.clicked.emit(self.identifier)
            event.accept()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        reset = menu.addAction('恢复本张卡片的自动宽度')
        if menu.exec(event.globalPos()) == reset:
            self.custom_width = 0
            self.board.width_changed.emit(self.identifier,0)
            self.board.reflow()


class KeywordBoard(QWidget):
    order_changed = Signal(object)
    width_changed = Signal(int,int)

    def __init__(self,columns=5,card_height=245):
        super().__init__()
        self.cards = []
        self.columns=columns;self.card_height=card_height
        self.scroll_area = None
        self.scroll_step = 0
        self.scroll_timer = QTimer(self)
        self.scroll_timer.setInterval(50)
        self.scroll_timer.timeout.connect(self.auto_scroll)
        self.token = uuid.uuid4().hex
        self.setAcceptDrops(True)
        self.setMinimumWidth(columns*140+(columns-1)*16+20)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.reflow)
        self.empty = QLabel('尚未保存关键词组\n点击右上角“添加关键词组”开始',self)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet('font-size:14pt;color:#8B7768;')
        self.indicator = QFrame(self)
        self.indicator.setStyleSheet('background:#F28C20;border:none;border-radius:2px;')
        self.indicator.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,True)
        self.indicator.hide()

    def schedule_reflow(self):
        self.timer.start(0)

    def clear(self):
        self.scroll_timer.stop()
        for card in self.cards:
            card.hide();card.deleteLater()
        self.cards = []
        self.indicator.hide()

    def reflow(self):
        gap=scaled(16);edge=scaled(10);minimum=scaled(140)
        columns=max(1,min(self.columns,(self.width()-2*edge+gap)//(minimum+gap)))
        available=max(minimum,self.width()-2*edge)
        automatic=max(minimum,(available-(columns-1)*gap)//columns)
        x,y,row_height=edge,edge,0
        self.setMinimumWidth(minimum+2*edge)
        for card in self.cards:
            width=min(available,scaled(card.custom_width) if card.custom_width else automatic)
            height=scaled(self.card_height+30 if card.busy else self.card_height)
            if x>edge and x+width>edge+available:
                x,y,row_height=edge,y+row_height+gap,0
            card.setGeometry(x,y,width,height);card.show()
            x+=width+gap;row_height=max(row_height,height)
        self.empty.setVisible(not self.cards)
        self.empty.setGeometry(edge,edge,available,scaled(180))
        self.setMinimumHeight(max(scaled(210),y+row_height+edge))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.schedule_reflow()

    def mime_for(self, identifier):
        mime = QMimeData()
        mime.setData(MIME,json.dumps(dict(board=self.token,id=identifier)).encode())
        return mime

    def dragged_id(self, mime):
        if not mime.hasFormat(MIME) or len(mime.data(MIME))>256:
            return None
        try:
            data = json.loads(bytes(mime.data(MIME)))
            if data['board']==self.token and any(card.identifier==data['id'] for card in self.cards):
                return data['id']
        except (ValueError,KeyError,TypeError):
            pass
        return None

    def insertion_at(self, position, moving):
        remaining = [card for card in self.cards if card.identifier!=moving]
        for index, card in enumerate(remaining):
            rect = card.geometry()
            if position.y()<rect.top() or (position.y()<=rect.bottom() and position.x()<rect.center().x()):
                return index,remaining
        return len(remaining),remaining

    def dragEnterEvent(self, event):
        if self.dragged_id(event.mimeData()) is not None:
            event.setDropAction(Qt.DropAction.MoveAction);event.accept()

    def dragMoveEvent(self, event):
        moving = self.dragged_id(event.mimeData())
        if moving is None:
            event.ignore();return
        if self.scroll_area:
            point = self.mapTo(self.scroll_area.viewport(),event.position().toPoint())
            self.scroll_step = -20 if point.y()<30 else 20 if point.y()>self.scroll_area.viewport().height()-30 else 0
            if self.scroll_step:
                self.scroll_timer.start()
            else:
                self.scroll_timer.stop()
        index,remaining = self.insertion_at(event.position().toPoint(),moving)
        if remaining:
            card = remaining[min(index,len(remaining)-1)]
            x = card.x()-7 if index<len(remaining) else card.x()+card.width()+5
            self.indicator.setGeometry(x,card.y(),4,card.height())
            self.indicator.show();self.indicator.raise_()
        event.setDropAction(Qt.DropAction.MoveAction);event.accept()

    def dragLeaveEvent(self, event):
        self.scroll_timer.stop()
        self.indicator.hide();event.accept()

    def auto_scroll(self):
        if self.scroll_area:
            bar = self.scroll_area.verticalScrollBar()
            bar.setValue(bar.value()+self.scroll_step)

    def dropEvent(self, event):
        self.scroll_timer.stop()
        moving = self.dragged_id(event.mimeData())
        self.indicator.hide()
        if moving is None:
            event.ignore();return
        index,remaining = self.insertion_at(event.position().toPoint(),moving)
        card = next(card for card in self.cards if card.identifier==moving)
        remaining.insert(index,card)
        order = [card.identifier for card in remaining]
        if order != [card.identifier for card in self.cards]:
            self.order_changed.emit(order)
            self.cards = remaining
            self.reflow()
        event.setDropAction(Qt.DropAction.MoveAction);event.accept()
