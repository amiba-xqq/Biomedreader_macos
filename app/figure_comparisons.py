"""Selection and inspection of named statistical comparisons."""
import csv
import io

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,QDialog,QDialogButtonBox,QHeaderView,QHBoxLayout,QLabel,QListWidget,
    QPushButton,QTableWidget,QTableWidgetItem,QVBoxLayout,
)


class ComparisonDialog(QDialog):
    def __init__(self,parent,groups,pairs):
        from figure_ui import QComboBox
        super().__init__(parent)
        self.setWindowTitle('选择比较组对')
        self.resize(630,430)
        self.pairs = [list(pair) for pair in pairs if len(pair)==2 and all(name in groups for name in pair)]
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.first,self.second = QComboBox(),QComboBox()
        for box in (self.first,self.second):
            box.addItems(groups)
            row.addWidget(box,1)
        self.second.setCurrentIndex(1)
        add = QPushButton('添加组对')
        add.clicked.connect(self.add)
        row.addWidget(add)
        layout.addLayout(row)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.list,1)
        remove = QPushButton('移除所选组对')
        remove.clicked.connect(self.remove)
        layout.addWidget(remove,0,Qt.AlignmentFlag.AlignLeft)
        self.status = QLabel('组名来自当前表头或已映射的组别列。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        standard = QDialogButtonBox.StandardButton
        self.buttons = QDialogButtonBox(standard.Ok|standard.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.refresh()

    def add(self):
        a,b = self.first.currentText(),self.second.currentText()
        if not a or not b or a==b:
            self.status.setText('请选择两个不同的组。')
            return
        if any({a,b}==set(pair) for pair in self.pairs):
            self.status.setText('此组对已添加。')
            return
        self.pairs.append([a,b])
        self.status.setText('可以继续添加其它组对。')
        self.refresh()

    def remove(self):
        selected = {self.list.row(item) for item in self.list.selectedItems()}
        self.pairs = [pair for i,pair in enumerate(self.pairs) if i not in selected]
        self.refresh()

    def refresh(self):
        self.list.clear()
        self.list.addItems([a+'  ↔  '+b for a,b in self.pairs])
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(self.pairs))


def result_rows(data):
    return list(csv.DictReader(io.StringIO(data.decode('utf-8-sig')))) if data else []


def show_results(parent,data):
    records = result_rows(data)
    if not records:
        return
    columns = [('target','类别／基因'),('group_a','组 A'),('group_b','组 B'),
               ('test','统计方法'),('p_unadjusted','原始 P 值'),('p_adjusted','校正 P 值'),('status','状态')]
    if 'p_adjusted' not in records[0]:
        columns = [column for column in columns if column[0]!='p_adjusted']
    dialog = QDialog(parent)
    dialog.setWindowTitle('统计检验结果')
    dialog.resize(980,500)
    layout = QVBoxLayout(dialog)
    table = QTableWidget(len(records),len(columns))
    table.setHorizontalHeaderLabels([heading for _,heading in columns])
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    for i,row in enumerate(records):
        for j,(key,_) in enumerate(columns):
            value = row.get(key,'')
            table.setItem(i,j,QTableWidgetItem(value or '—'))
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    layout.addWidget(table)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()
