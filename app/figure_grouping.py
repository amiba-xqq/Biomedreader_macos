"""Sample aliases and normalization references for figure projects."""
from figure_status import FigureStatus
from PySide6.QtCore import QSignalBlocker,Qt
from PySide6.QtWidgets import (QAbstractItemView,QCheckBox,QDialog,QDialogButtonBox,QHeaderView,QHBoxLayout,QLabel,
                               QPushButton,QStyledItemDelegate,QTableWidget,QTableWidgetItem,QVBoxLayout)
from figure_data import sample_group


def fill_choices(editor,values,text):
    editor.clear()
    editor.addItems(values)
    editor.setEditText(text)


class GroupingEditor(QStyledItemDelegate):
    def createEditor(self,parent,option,index):
        from figure_ui import QComboBox
        editor = QComboBox(parent)
        editor.setEditable(True)
        editor.activated.connect(lambda _:self.commitData.emit(editor))
        return editor

    def setEditorData(self,editor,index):
        table = self.parent()
        values = (table.item(row,index.column()).text() for row in range(table.rowCount()))
        fill_choices(editor,list(dict.fromkeys(value for value in values if value.strip())),index.data())
        editor.lineEdit().selectAll()

    def setModelData(self,editor,model,index):
        model.setData(index,editor.currentText().strip())


class GroupingDialog(QDialog):
    def __init__(self,parent,names,current,default,qpcr):
        from figure_ui import QComboBox
        super().__init__(parent)
        self.setWindowTitle('样本分组与归一化')
        self.resize(980,550)
        self.result_settings = None
        layout = QVBoxLayout(self)
        self.enabled = QCheckBox('按样本设置分组与归一化')
        self.enabled.setChecked(bool(current))
        layout.addWidget(self.enabled)
        note = QLabel('双击分组名称或处理条件可输入或选择已有值。拖选或按住 Ctrl 选择多个单元格后可批量填写。'
                      +('「不归一化」时按 2⁻ΔCt 作图，仍使用内参基因。' if qpcr else '「不归一化」时保留原始数值。'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.names = names
        self.table = QTableWidget(len(names),4)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setItemDelegateForColumn(1,GroupingEditor(self.table))
        self.table.setItemDelegateForColumn(2,GroupingEditor(self.table))
        self.table.setHorizontalHeaderLabels(['样本','分组名称','处理条件','归一化参考样本'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(40)
        for index,name in enumerate(names):
            group,treatment,reference = sample_group({'sample_grouping':current},name) if name in current else (
                name,name,default if qpcr else '')
            item = QTableWidgetItem(name)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(index,0,item)
            self.table.setItem(index,1,QTableWidgetItem(group))
            self.table.setItem(index,2,QTableWidgetItem(treatment))
            chooser = QComboBox()
            chooser.addItem('不归一化','')
            for sample in names:
                chooser.addItem(sample,sample)
            chooser.setCurrentIndex(max(0,chooser.findData(reference)))
            reference_item = QTableWidgetItem()
            reference_item.setFlags(reference_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(index,3,reference_item)
            self.table.setCellWidget(index,3,chooser)
        tools = QHBoxLayout()
        swap = QPushButton('互换分组名称与处理条件')
        swap.clicked.connect(self.swap_columns)
        tools.addWidget(swap)
        tools.addStretch()
        self.batch_value = QComboBox()
        self.batch_value.setEditable(True)
        self.batch_value.lineEdit().setPlaceholderText('输入或选择批量填写值')
        self.batch_value.setMinimumWidth(190)
        tools.addWidget(self.batch_value)
        apply = QPushButton('填写所选格子')
        apply.clicked.connect(self.fill_selection)
        tools.addWidget(apply)
        layout.addLayout(tools)
        references = QHBoxLayout()
        self.batch_reference = QComboBox()
        self.batch_reference.addItem('不归一化','')
        for name in names:
            self.batch_reference.addItem(name,name)
        apply_reference = QPushButton('应用到所选行')
        apply_reference.clicked.connect(self.fill_references)
        references.addWidget(QLabel('批量归一化参考'))
        references.addWidget(self.batch_reference)
        references.addWidget(apply_reference)
        references.addStretch()
        layout.addLayout(references)
        layout.addWidget(self.table,1)
        for widget in (swap,self.batch_value,apply,self.batch_reference,apply_reference):
            self.enabled.toggled.connect(widget.setEnabled)
            widget.setEnabled(self.enabled.isChecked())
        self.enabled.toggled.connect(self.table.setEnabled)
        self.table.setEnabled(self.enabled.isChecked())
        self.table.itemChanged.connect(self.refresh_batch_choices)
        self.refresh_batch_choices()
        self.message = FigureStatus()
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.finish)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def refresh_batch_choices(self):
        values = (self.table.item(row,column).text().strip() for row in range(self.table.rowCount())
                  for column in (1,2))
        fill_choices(self.batch_value,list(dict.fromkeys(value for value in values if value)),
                     self.batch_value.currentText())

    def swap_columns(self):
        with QSignalBlocker(self.table):
            for row in range(self.table.rowCount()):
                group,treatment = (self.table.item(row,column) for column in (1,2))
                group_text,treatment_text = group.text(),treatment.text()
                group.setText(treatment_text)
                treatment.setText(group_text)
        self.refresh_batch_choices()

    def fill_selection(self):
        value = self.batch_value.currentText().strip()
        cells = [item for item in self.table.selectedItems() if item.column() in (1,2)]
        if not value or not cells:
            self.message.error('请选择分组名称或处理条件格子，并输入填写值。')
            return
        with QSignalBlocker(self.table):
            for cell in cells:
                cell.setText(value)
        self.refresh_batch_choices()
        self.message.clear()


    def finish(self):
        settings = {}
        if self.enabled.isChecked():
            for index,name in enumerate(self.names):
                group = self.table.item(index,1).text().strip()
                treatment = self.table.item(index,2).text().strip()
                reference = self.table.cellWidget(index,3).currentData()
                if not group or not treatment or ' / ' in group or ' / ' in treatment:
                    self.message.error('请填写分组和处理条件，名称中请避免使用「 / 」。')
                    return
                settings[name] = [group,treatment,reference]
        self.result_settings = settings
        self.accept()

    def fill_references(self):
        rows = {index.row() for index in self.table.selectedIndexes()}
        if not rows:
            self.message.error('请先拖选单元格或按住 Ctrl 选择多个样本行。')
            return
        for row in rows:
            self.table.cellWidget(row,3).setCurrentIndex(self.batch_reference.currentIndex())
        self.message.clear()


class ReferenceDialog(QDialog):
    def __init__(self,parent,names,current):
        from figure_ui import QComboBox
        super().__init__(parent)
        self.setWindowTitle('Grouped 矩阵归一化')
        layout = QVBoxLayout(self)
        label = QLabel('每个类别分别除以所选对照组的均值。')
        layout.addWidget(label)
        self.chooser = QComboBox()
        self.chooser.setAccessibleName('归一化参考组')
        self.chooser.addItem('不归一化','')
        for name in names:
            self.chooser.addItem(name,name)
        self.chooser.setCurrentIndex(max(0,self.chooser.findData(current)))
        layout.addWidget(self.chooser)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def reference(self):
        return self.chooser.currentData()
