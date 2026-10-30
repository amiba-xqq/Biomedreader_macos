"""Research-chat rendering: safe local citations and independent evidence scrolling."""
import re
from html import escape
from PySide6.QtCore import Qt, QVariantAnimation
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QTextBrowser, QScrollArea


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
