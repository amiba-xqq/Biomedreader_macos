"""Assign workbook samples to independent qPCR repeats."""
import json
from pathlib import Path

from PySide6.QtCore import QMimeData, QSize, Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)
from figure_status import FigureStatus

MIME = 'application/x-biomed-qpcr-samples'


class SampleBasket(QListWidget):
    def __init__(self,page,name):
        super().__init__()
        self.page,self.name = page,name
        self.press_position = None
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setSpacing(4)
        self.setMinimumHeight(86)
        self.update_style()
        self.itemDoubleClicked.connect(lambda _:page.edit_conditions(self))

    def update_style(self,active=False):
        border = '#B76B32' if active else '#E4D7CA'
        color = '#FFF0E1' if self.name else '#F3EFEB'
        self.setStyleSheet(f'QListWidget {{border:2px solid {border};border-radius:9px;background:#FCFAF8;}}'
                           f'QListWidget::item {{background:{color};color:#553A26;border:1px solid #DECAAE;'
                           'border-radius:7px;padding:3px 8px;}'
                           'QListWidget::item:selected {background:#B45C23;color:white;border:1px solid #89431A;}')

    def selected_samples(self):
        return [item.data(Qt.ItemDataRole.UserRole) for item in self.selectedItems()]

    def mousePressEvent(self,event):
        self.press_position = None
        item = self.itemAt(event.position().toPoint())
        if item and event.button()==Qt.MouseButton.LeftButton:
            self.press_position = event.position().toPoint()
            if item.isSelected() and not event.modifiers():
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self,event):
        if self.press_position is not None and not event.modifiers():
            item = self.itemAt(event.position().toPoint())
            if item is not None:
                self.setCurrentItem(item)
        self.press_position = None
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self,event):
        if (self.press_position is None or not event.buttons() & Qt.MouseButton.LeftButton
                or (event.position().toPoint()-self.press_position).manhattanLength()<QApplication.startDragDistance()):
            return super().mouseMoveEvent(event)
        item = self.itemAt(self.press_position)
        self.press_position = None
        samples = self.selected_samples()
        if samples:
            data = QMimeData()
            data.setData(MIME,json.dumps(samples).encode())
            drag = QDrag(self)
            drag.setMimeData(data)
            if item:
                drag.setPixmap(self.viewport().grab(self.visualItemRect(item)))
            drag.exec(Qt.DropAction.MoveAction)

    def accepts_samples(self,event):
        source = event.source()
        return isinstance(source,SampleBasket) and source.page is self.page and source is not self and event.mimeData().hasFormat(MIME)

    def dragEnterEvent(self,event):
        if self.accepts_samples(event):
            self.update_style(True)
            event.acceptProposedAction()

    def dragMoveEvent(self,event):
        if self.accepts_samples(event):
            event.acceptProposedAction()

    def dragLeaveEvent(self,event):
        self.update_style()
        event.accept()

    def dropEvent(self,event):
        self.update_style()
        if self.accepts_samples(event):
            self.page.move(self.name,json.loads(bytes(event.mimeData().data(MIME))))
            event.acceptProposedAction()


class RepeatSourcePage(QWidget):
    def __init__(self,samples,initial,enabled):
        super().__init__()
        self.baskets,self.labels,self.sections = {},{},{}
        self.placements = {name:list(initial.get(name,('',''))) for name in samples}
        layout = QVBoxLayout(self)
        self.enabled = QCheckBox('使用此数据块')
        self.enabled.setChecked(enabled)
        layout.addWidget(self.enabled)
        panel = QWidget()
        body = QVBoxLayout(panel)
        tools = QHBoxLayout()
        for text,handler in (('＋ 新建重复篮子',self.add),('剩余样本归为一组',self.group_remaining),
                             ('移动选中样本…',self.move_selected),('设置处理名称…',self.edit_conditions)):
            button = QPushButton(text)
            button.clicked.connect(lambda _,run=handler:run())
            tools.addWidget(button)
        tools.addStretch()
        body.addLayout(tools)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        area = QWidget()
        self.groups = QVBoxLayout(area)
        scroll.setWidget(area)
        body.addWidget(scroll,1)
        self.add_basket('')
        for repeat in dict.fromkeys(placement[0] for placement in initial.values()):
            if repeat:
                self.add_basket(repeat)
        if len(self.baskets)==1:
            self.add()
        self.groups.addStretch()
        self.refresh()
        self.status = FigureStatus()
        body.addWidget(self.status)
        layout.addWidget(panel,1)
        self.enabled.toggled.connect(panel.setEnabled)
        panel.setEnabled(enabled)

    def add_basket(self,name):
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0,3,0,3)
        header = QHBoxLayout()
        label = QLabel()
        header.addWidget(label,1)
        if name:
            remove = QPushButton('删除篮子')
            remove.setAccessibleName(f'删除 {name} 篮子')
            remove.clicked.connect(lambda:self.remove_basket(name))
            header.addWidget(remove)
        layout.addLayout(header)
        basket = SampleBasket(self,name)
        basket.setAccessibleName(name or '待分配样本')
        layout.addWidget(basket)
        self.labels[name],self.baskets[name] = label,basket
        self.sections[name] = section
        index = self.groups.count()
        if index and self.groups.itemAt(index-1).spacerItem():
            index -= 1
        self.groups.insertWidget(index,section)

    def remove_basket(self,name):
        for placement in self.placements.values():
            if placement[0]==name:
                placement[0] = ''
        section = self.sections.pop(name)
        self.groups.removeWidget(section)
        section.hide()
        section.deleteLater()
        self.baskets.pop(name)
        self.labels.pop(name)
        self.refresh()
        self.status.setText('已删除篮子，样本已回到待分配区。')

    def add(self):
        number = 1
        while f'重复 {number}' in self.baskets:
            number += 1
        name = f'重复 {number}'
        self.add_basket(name)
        self.refresh()
        return name

    def refresh(self):
        for name,basket in self.baskets.items():
            selected = set(basket.selected_samples())
            basket.clear()
            for sample,(repeat,condition) in self.placements.items():
                if repeat!=name:
                    continue
                item = QListWidgetItem(f'{sample}  ·  {condition}' if condition else sample)
                item.setData(Qt.ItemDataRole.UserRole,sample)
                item.setSizeHint(QSize(max(88,basket.fontMetrics().horizontalAdvance(item.text())+24),35))
                basket.addItem(item)
                item.setSelected(sample in selected)
            caption = name or '待分配 · 有样本入篮后，此区样本不参与分析'
            self.labels[name].setText(f'{caption}（{basket.count()}）')

    def move(self,name,samples):
        for sample in samples:
            self.placements[sample][0] = name
        self.refresh()

    def selected(self):
        return list(dict.fromkeys(sample for basket in self.baskets.values() for sample in basket.selected_samples()))

    def move_selected(self):
        samples = self.selected()
        if not samples:
            self.status.error('请先选择要移动的样本。')
            return
        choices = ['待分配',*[name for name in self.baskets if name]]
        name,accepted = QInputDialog.getItem(self,'移动选中样本','目标篮子',choices,min(1,len(choices)-1),False)
        if accepted:
            self.move('' if name=='待分配' else name,samples)
            self.status.clear()

    def group_remaining(self):
        remaining = [sample for sample,(repeat,_) in self.placements.items() if not repeat]
        if not remaining:
            self.status.error('没有剩余的待分配样本。')
            return
        empty = next((name for name,basket in self.baskets.items() if name and not basket.count()),None)
        self.move(empty or self.add(),remaining)
        self.status.clear()

    def edit_conditions(self,basket=None):
        samples = basket.selected_samples() if basket else self.selected()
        if not samples:
            self.status.error('请先选择要设置处理名称的样本。')
            return
        old = self.placements[samples[0]][1] if len(samples)==1 else ''
        name,accepted = QInputDialog.getText(self,'处理名称','例如 IR 或 noIR，留空使用样本名称',text=old)
        if accepted:
            for sample in samples:
                self.placements[sample][1] = name.strip()
            self.refresh()
            self.status.clear()

    def assignments(self):
        if any(repeat for repeat,_ in self.placements.values()):
            return {sample:list(entry) for sample,entry in self.placements.items() if entry[0]}
        return {sample:['重复 1',condition] for sample,(_,condition) in self.placements.items()}


class RepeatDialog(QDialog):
    def __init__(self,parent,inventory,options):
        super().__init__(parent)
        self.setWindowTitle('设置 qPCR 重复组')
        self.resize(920,565)
        self.pages = {}
        self.result_settings = {}
        self.result_sources = {}
        saved = options.get('qpcr_sample_repeats',{})
        source_settings = options.get('qpcr_repeats',{})
        layout = QVBoxLayout(self)
        hint = QLabel('每个素材单独设置，默认整份素材算一个重复。可用 Ctrl 或 Shift 多选卡片，再拖入篮子。'
                      '同一重复的 IP 和对照放在一起。删除篮子会将卡片移回待分配区。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        tabs = QTabWidget()
        layout.addWidget(tabs,1)
        for file,file_id,choices in inventory:
            filename = file['name']
            data_tabs = QTabWidget()
            data_tabs.tabBar().setVisible(len(choices)>1)
            index = tabs.addTab(data_tabs,Path(filename).stem)
            tabs.setTabToolTip(index,filename)
            self.pages[file_id] = {}
            default = source_settings.get(file_id) or next(iter(choices))
            for key,(label,samples) in choices.items():
                assigned = saved.get(file_id,{})
                page = RepeatSourcePage(samples,assigned.get(key,{}),key in assigned if assigned else key==default)
                self.pages[file_id][key] = page
                data_tabs.addTab(page,label)
        self.status = FigureStatus()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.finish)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def finish(self):
        settings = {}
        sources = {}
        for file_id,pages in self.pages.items():
            selected = {key:page.assignments() for key,page in pages.items() if page.enabled.isChecked()}
            if not selected:
                self.status.error('请为每个文件至少勾选一个有数据的工作表或数据块。')
                return
            first = next(iter(selected))
            if len(selected)>1 or any(
                    len(rows)<len(pages[key].placements) or len({repeat for repeat,_ in rows.values()})>1
                    or any(condition for _,condition in rows.values()) for key,rows in selected.items()):
                settings[file_id] = selected
            elif first!=next(iter(pages)):
                sources[file_id] = first
        self.result_settings = settings
        self.result_sources = sources
        self.accept()
