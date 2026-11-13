"""Inline errors for figure project windows."""
import sys

from PySide6.QtCore import Qt, QVariantAnimation
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel


def animations_enabled():
    if sys.platform!='win32':
        return True
    import ctypes
    enabled = ctypes.c_int()
    result = ctypes.windll.user32.SystemParametersInfoW(0x1042,0,ctypes.byref(enabled),0)
    return not result or bool(enabled.value)


class FigureStatus(QLabel):
    def __init__(self,text=''):
        super().__init__(text)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self.setObjectName('Muted')
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(560)
        self.animation.setStartValue(QColor('#FFF1F0'))
        self.animation.setKeyValueAt(.26,QColor('#FBCAC5'))
        self.animation.setEndValue(QColor('#FFF1F0'))
        self.animation.valueChanged.connect(self.highlight)

    def highlight(self,color):
        self.setStyleSheet(f'color:#A52219;background:{color.name()};border-radius:4px;')

    def setText(self,text):
        self.animation.stop()
        self.setStyleSheet('')
        super().setText(text)

    def clear(self):
        self.setText('')

    def error(self,text,*,animate=True):
        changed = self.text()!=text or not self.styleSheet()
        self.animation.stop()
        super().setText(text)
        self.highlight(QColor('#FFF1F0'))
        if changed and animate and animations_enabled():
            self.animation.start()
