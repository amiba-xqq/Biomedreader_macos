"""Palette controls for statistical figures."""
from figure_status import FigureStatus
import re

from PySide6.QtCore import QRect,QRectF,Qt
from PySide6.QtGui import QColor,QPainter,QPixmap
from PySide6.QtWidgets import (QColorDialog,QDialog,QDialogButtonBox,QFormLayout,QHBoxLayout,QLabel,QLineEdit,
                               QPushButton,QStyledItemDelegate,QStyleOptionViewItem,QVBoxLayout,QWidget)

from figure_render import COLORS


def gradient(name,count):
    from matplotlib import colormaps
    from matplotlib.colors import to_hex
    cmap = colormaps[name]
    samples = [.5] if count==1 else [i/(count-1) for i in range(count)]
    return [to_hex(cmap(position)).upper() for position in samples]


def custom_colors(text):
    tokens = [part for part in re.split(r'[,，;；\s]+',text.strip()) if part]
    if not 1<=len(tokens)<=32 or any(not re.fullmatch(r'#?[0-9a-fA-F]{6}',token) for token in tokens):
        raise ValueError('请输入 1–32 个六位十六进制颜色，例如 #E64B35, #4DBBD5。')
    return ['#'+token.lstrip('#').upper() for token in tokens]


class ColorStrip(QWidget):
    def __init__(self):
        super().__init__()
        self.colors = []
        self.setMinimumSize(105,22)
        self.setMaximumHeight(24)

    def set_colors(self,colors):
        self.colors = list(colors)
        self.setToolTip('  '.join(colors))
        self.update()

    def paintEvent(self,event):
        if not self.colors:
            return
        painter = QPainter(self)
        width = (self.width()-2)/len(self.colors)
        for i,value in enumerate(self.colors):
            painter.fillRect(QRectF(1+i*width,2,width+.2,self.height()-4),QColor(value))
        painter.setPen(QColor('#B8A99A'))
        painter.drawRect(self.rect().adjusted(0,1,-1,-2))
        painter.end()


class ColormapDelegate(QStyledItemDelegate):
    def __init__(self,parent):
        super().__init__(parent)
        self.strips = {}

    def sizeHint(self,option,index):
        size = super().sizeHint(option,index)
        size.setWidth(size.width()+148)
        size.setHeight(max(size.height(),24))
        return size

    def paint(self,painter,option,index):
        super().paint(painter,option,index)
        name = index.data(Qt.ItemDataRole.DisplayRole)
        if name not in self.strips:
            strip = QPixmap(128,1)
            samples = QPainter(strip)
            for column,value in enumerate(gradient(name,128)):
                samples.setPen(QColor(value))
                samples.drawPoint(column,0)
            samples.end()
            self.strips[name] = strip
        text_end = option.rect.left()+option.fontMetrics.horizontalAdvance(name)+18
        right = option.rect.right()-9
        width = min(128,right-text_end)
        if width>=20:
            bounds = QRect(right-width,option.rect.center().y()-6,width,13)
            painter.save()
            painter.drawPixmap(bounds,self.strips[name])
            painter.setPen(QColor('#B8A99A'))
            painter.drawRect(bounds.adjusted(0,0,-1,-1))
            painter.restore()


class PaletteDialog(QDialog):
    def __init__(self,parent,settings,current):
        from matplotlib import colormaps
        from figure_ui import QComboBox,integer
        super().__init__(parent)
        self.setWindowTitle('统计图配色')
        self.resize(620,330)
        self.result_settings = None
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItem('Nature 风格 · NPG','nature')
        self.mode.addItem('Matplotlib 渐变','gradient')
        self.mode.addItem('自定义颜色','custom')
        form.addRow('色板类型',self.mode)
        self.maps = QComboBox()
        for name in sorted(colormaps):
            self.maps.addItem(name)
        self.maps.setItemDelegate(ColormapDelegate(self.maps))
        self.maps.setMaxVisibleItems(12)
        self.maps.setCurrentText(settings.get('map','viridis'))
        form.addRow('渐变色表',self.maps)
        self.count = integer(maximum=32,minimum=1,initial=settings.get('count',8))
        form.addRow('渐变取色数量',self.count)
        color_row = QHBoxLayout()
        self.custom = QLineEdit(', '.join(current or COLORS))
        color_row.addWidget(self.custom,1)
        picker = QPushButton('选色／屏幕取色')
        picker.clicked.connect(self.pick)
        color_row.addWidget(picker)
        form.addRow('十六进制颜色',color_row)
        self.picker = picker
        self.strip = ColorStrip()
        form.addRow('当前色条',self.strip)
        layout.addLayout(form)
        self.note = FigureStatus('颜色数可与组数不同。图表从头循环使用色向量。')
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        standard = QDialogButtonBox.StandardButton
        self.buttons = QDialogButtonBox(standard.Ok|standard.Cancel)
        self.buttons.accepted.connect(self.finish)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.mode.setCurrentIndex(max(0,self.mode.findData(settings.get('mode','custom' if current else 'nature'))))
        self.mode.currentIndexChanged.connect(self.refresh)
        self.maps.currentIndexChanged.connect(self.refresh)
        self.count.valueChanged.connect(self.refresh)
        self.custom.textChanged.connect(self.refresh)
        self.refresh()

    def pick(self):
        initial = QColor('#E64B35')
        try:
            colors = custom_colors(self.custom.text())
            initial = QColor(colors[-1])
        except ValueError:
            colors = []
        picker = QColorDialog(initial,self)
        picker.setWindowTitle('选取颜色 · 可点击 Pick Screen Color 在屏幕上取色')
        picker.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog)
        if picker.exec():
            selected = picker.selectedColor().name(QColor.NameFormat.HexRgb).upper()
            self.custom.setText(', '.join([*colors[:31],selected]))

    def selected(self):
        mode = self.mode.currentData()
        if mode=='gradient':
            return dict(mode=mode,map=self.maps.currentText(),count=self.count.value()),gradient(self.maps.currentText(),self.count.value())
        if mode=='custom':
            return dict(mode=mode),custom_colors(self.custom.text())
        return dict(mode='nature'),list(COLORS)

    def refresh(self,*_):
        mode = self.mode.currentData()
        self.maps.setEnabled(mode=='gradient')
        self.count.setEnabled(mode=='gradient')
        self.custom.setEnabled(mode=='custom')
        self.picker.setEnabled(mode=='custom')
        try:
            _,colors = self.selected()
        except ValueError as error:
            self.strip.set_colors([])
            self.note.error(str(error))
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
            return
        self.strip.set_colors(colors)
        self.note.setText('颜色数可与组数不同。图表从头循环使用色向量。')
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)

    def finish(self):
        self.result_settings = self.selected()
        self.accept()
