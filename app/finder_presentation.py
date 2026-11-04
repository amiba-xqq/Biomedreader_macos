"""Research-chat rendering: safe local citations and independent evidence scrolling."""
import re
from datetime import datetime
from html import escape
from PySide6.QtCore import Qt, QPoint, QSize, QVariantAnimation
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QTextBrowser, QScrollArea, QWidget, QLabel


def message_time(value, now=None):
    """Format a recorded instant against the computer's current local date.

    Older chats may not contain message timestamps. Do not invent a sending
    time from the chat's last save, which could just be a layout change.
    """
    try:
        recorded = datetime.fromisoformat(value).astimezone()
    except (ValueError, TypeError, OverflowError):
        return '时间未记录', '旧对话未记录本条消息的完成时间'
    current = (now or datetime.now().astimezone()).astimezone()
    clock = recorded.strftime('%H:%M')
    if recorded.date() == current.date():
        text = clock
    elif (recorded.year, recorded.month) == (current.year, current.month):
        text = f'{recorded.day}日 {clock}'
    elif recorded.year == current.year:
        text = f'{recorded.month}月{recorded.day}日 {clock}'
    else:
        text = f'{recorded.year}年{recorded.month}月{recorded.day}日 {clock}'
    return text, recorded.strftime('%Y-%m-%d %H:%M:%S') + '（本地时间）'


class MessageTime(QLabel):
    """A compact footer; the chat's single timer refreshes relative dates."""
    def __init__(self, value):
        super().__init__()
        self.value = value
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setStyleSheet('color:#869297;font-family:Arial,Microsoft YaHei;font-size:8.5pt;')
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.refresh()

    def refresh(self, now=None):
        text, tooltip = message_time(self.value, now)
        if self.text() != text:
            self.setText(text)
        self.setToolTip(tooltip)


class EvidenceDock(QWidget):
    """Keep the independent evidence viewport beside the *visible* reply.

    Only the child viewport moves. The full reply, splitter and text keep
    their geometry, so outer scrolling never reflows the document or moves
    its scroll anchor. Near the end of a reply the dock is clipped to that
    reply, never covering the next turn or the message footer.
    """
    def __init__(self, evidence, conversation):
        super().__init__()
        self.evidence = evidence
        self.conversation = conversation
        evidence.setParent(self)
        self.setMinimumWidth(125)

    def sizeHint(self):
        return QSize(200, 250)

    def minimumSizeHint(self):
        return QSize(125, 0)

    def sync_position(self):
        viewport = self.conversation.scroll.viewport()
        top = self.mapTo(viewport, QPoint()).y()
        visible_top = max(0, -top)
        visible_bottom = min(self.height(), viewport.height() - top)
        if visible_bottom > visible_top:
            offset = visible_top
            height = min(540, visible_bottom - visible_top)
        else:
            # Keep a usable scrollbar range while this turn is off screen.
            offset = 0
            height = min(540, max(1, viewport.height()), self.height())
        self.evidence.setGeometry(0, offset, self.width(), max(1, height))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.sync_position()


def citation_html(text, references):
    """Link only verified, turn-local numbers; never interpret model HTML."""
    numbers = {str(r.get('number')) for r in references if r.get('number') is not None}
    def replace(match):
        number = match.group(1)
        return (f'<a href="biomed-ref:{number}" style="color:#BA631F;text-decoration:none;'
                f'font-weight:600;">[{number}]</a>') if number in numbers else match.group(0)
    return '<div style="white-space:pre-wrap;">' + re.sub(r'\[(\d+)\]', replace, escape(text)).replace('\n', '<br>') + '</div>'


class ResearchText(QTextBrowser):
    """The outer conversation owns text scrolling, including wheel events."""
    def __init__(self, conversation):
        super().__init__()
        self.conversation = conversation

    def wheelEvent(self, event):
        bar = self.conversation.scroll.verticalScrollBar()
        distance = event.pixelDelta().y() or event.angleDelta().y() / 120 * bar.singleStep() * 3
        bar.setValue(bar.value() - round(distance))
        event.accept()


class EvidenceScroll(QScrollArea):
    """Never pass a wheel event to the conversation, even at either end."""
    def wheelEvent(self, event):
        super().wheelEvent(event)
        event.accept()


def highlight_card(card):
    previous = getattr(card, '_highlight', None)
    if previous:
        previous.stop()
    animation = QVariantAnimation(card)
    animation.setDuration(950)
    animation.setStartValue(QColor('#FFF0D5'))
    animation.setKeyValueAt(.45, QColor('#FFE0AB'))
    animation.setEndValue(QColor('#F8FAFB'))
    animation.valueChanged.connect(lambda color: card.setStyleSheet(
        'QFrame#FinderArticle {background:' + color.name() + ';border:1px solid #DDB783;border-radius:10px;}'))
    animation.finished.connect(lambda: card.setStyleSheet(''))
    card._highlight = animation
    animation.start()
