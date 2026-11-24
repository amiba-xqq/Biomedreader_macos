"""Assign named Olympus acquisitions to export folders with drag-and-drop cards."""

from pathlib import Path

from PySide6.QtCore import QPointF, QRect, QSize, Qt
from PySide6.QtGui import QColor, QTextLayout, QTextOption
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidgetItem, QMessageBox,
    QPushButton, QScrollArea, QSplitter, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout, QWidget,
)

from ddrimage_oir_groups import oir_files, selected_groups
from ddrimage_plot_ui import PlotGroupList


class OirCardDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        appearance = QStyleOptionViewItem(option)
        self.initStyleOption(appearance, index)
        text = appearance.text
        appearance.text = ""
        appearance.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem,
                                              appearance, painter, appearance.widget)
        rect = option.rect.adjusted(12, 5, -12, -5)
        text_layout = QTextLayout(text, appearance.font)
        wrapping = QTextOption()
        wrapping.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        text_layout.setTextOption(wrapping)
        text_layout.beginLayout()
        height = 0.0
        while True:
            line = text_layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(1, rect.width()))
            line.setPosition(QPointF(0, height))
            height += line.height()
        text_layout.endLayout()
        painter.save()
        painter.setClipRect(rect)
        selected = appearance.state & QStyle.StateFlag.State_Selected
        painter.setPen(QColor("white" if selected else "#553A26"))
        text_layout.draw(painter, QPointF(rect.left(), rect.top() + max(0.0, (rect.height()-height)/2)))
        painter.restore()


class OirCards(PlotGroupList):
    def __init__(self, owner, caption, tint):
        super().__init__(owner, caption, tint)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.setItemDelegate(OirCardDelegate(self))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumHeight(112)

    def add_file(self, relative):
        file = Path(relative)
        card = QListWidgetItem(file.name)
        card.setData(Qt.ItemDataRole.UserRole, relative)
        card.setToolTip(relative)
        self.addItem(card)
        self.resize_card(card)

    def paths(self):
        return [self.item(row).data(Qt.ItemDataRole.UserRole) for row in range(self.count())]

    def resize_card(self, card):
        width = max(100, self.viewport().width() - 42)
        metrics = self.fontMetrics()
        flags = int(Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere)
        height = metrics.boundingRect(QRect(0, 0, width, 6000), flags, card.text()).height()
        card.setSizeHint(QSize(120, max(36, height + 19)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        for row in range(self.count()):
            self.resize_card(self.item(row))

    def receive(self, source, row):
        super().receive(source, row)
        for card in self.selectedItems():
            self.resize_card(card)


class OirGroupDialog(QDialog):
    def __init__(self, parent, files, saved_groups):
        super().__init__(parent)
        self.files = files
        self.groups = []
        self.grouping = None
        self.setWindowTitle("设置 Olympus OIR 导出分组")
        self.resize(1060, 730)
        layout = QVBoxLayout(self)
        title = QLabel("Olympus OIR 导出分组")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        hint = QLabel("将 OIR 卡片拖入右侧分组，可用 Ctrl 或 Shift 多选。每组导出到一个同名子文件夹，"
                      "待分配的文件会跳过。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_column = QVBoxLayout(left)
        left_column.addWidget(QLabel("待分配"))
        self.waiting = OirCards(self, "待分配的 OIR 文件", "#F3EFEB")
        left_column.addWidget(self.waiting)
        splitter.addWidget(left)
        right = QWidget()
        right_column = QVBoxLayout(right)
        add = QPushButton("＋ 新建分组")
        add.clicked.connect(lambda: self.add_group())
        right_column.addWidget(add, 0, Qt.AlignmentFlag.AlignLeft)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        group_host = QWidget()
        self.group_layout = QVBoxLayout(group_host)
        self.group_layout.setContentsMargins(0, 0, 0, 0)
        self.group_layout.addStretch()
        scroll.setWidget(group_host)
        right_column.addWidget(scroll, 1)
        splitter.addWidget(right)
        splitter.setSizes([445, 590])
        layout.addWidget(splitter, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存分组")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        basic = self.buttons.addButton("逐文件导出", QDialogButtonBox.ButtonRole.ResetRole)
        basic.clicked.connect(self.accept)
        self.buttons.accepted.connect(self.save_groups)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        for group in saved_groups or []:
            self.add_group(group["name"], [path for path in group["files"] if path in files])
        if not self.groups:
            self.add_group()
        used = {path for _, _, cards in self.groups for path in cards.paths()}
        for path in files:
            if path not in used:
                self.waiting.add_file(path)
        self.update_buttons()

    def add_group(self, name=None, paths=()):
        used = {entry.text() for _, entry, _ in self.groups}
        initial = next(f"分组 {n}" for n in range(1, len(used) + 2) if f"分组 {n}" not in used)
        card = QFrame()
        card.setObjectName("Card")
        column = QVBoxLayout(card)
        row = QHBoxLayout()
        editor = QLineEdit(name or initial)
        editor.setAccessibleName("分组文件夹名称")
        row.addWidget(editor, 1)
        use_selected = QPushButton("加入所选")
        use_selected.setProperty("secondary", True)
        row.addWidget(use_selected)
        remove = QPushButton("删除分组")
        remove.setProperty("secondary", True)
        row.addWidget(remove)
        column.addLayout(row)
        basket = OirCards(self, f"{editor.text()}的 OIR 文件", "#FFF0E1")
        column.addWidget(basket)
        for path in paths:
            basket.add_file(path)
        record = (card, editor, basket)
        self.groups.append(record)
        self.group_layout.insertWidget(self.group_layout.count() - 1, card)
        remove.clicked.connect(lambda: self.remove_group(record))
        use_selected.clicked.connect(lambda: basket.receive(self.waiting, basket.count()))
        editor.textChanged.connect(lambda text: basket.setAccessibleName(f"{text}的 OIR 文件"))
        self.update_buttons()

    def remove_group(self, record):
        card, _, basket = record
        for relative in basket.paths():
            self.waiting.add_file(relative)
        self.groups.remove(record)
        self.group_layout.removeWidget(card)
        card.hide()
        card.deleteLater()
        self.update_buttons()

    def update_buttons(self):
        included = sum(basket.count() for _, _, basket in self.groups)
        self.status.setText(f"{included} 个已分组 · {self.waiting.count()} 个待分配")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(included > 0)

    def save_groups(self):
        groups = [{"name": editor.text().strip(), "files": basket.paths()}
                  for _, editor, basket in self.groups if basket.count()]
        try:
            selected_groups(groups, self.files)
        except (TypeError, ValueError) as error:
            self.status.setText(str(error))
            return
        self.grouping = groups
        self.accept()


class OirGroupField(QPushButton):
    def __init__(self, source, recursive, saved, parent=None):
        super().__init__(parent)
        self.source = source
        self.recursive = recursive
        self.saved = saved if isinstance(saved, dict) else {}
        self.dialog = None
        self.clicked.connect(self.configure)
        self.refresh()

    def folder(self):
        raw = self.source().strip()
        return Path(raw).expanduser().resolve() if raw else None

    def value(self):
        root = self.folder()
        return self.saved.get("groups") if root and str(root) == self.saved.get("source") else None

    def refresh(self):
        groups = self.value()
        self.setText(f"设置 OIR 分组（{len(groups)} 组）…" if groups else "设置 OIR 分组…")

    def configure(self):
        if self.dialog is not None:
            return
        root = self.folder()
        if not root or not root.is_dir():
            QMessageBox.information(self, "设置 OIR 分组", "请先选择存放 OIR 文件的文件夹。")
            return
        try:
            files = oir_files(root, self.recursive())
        except OSError as error:
            QMessageBox.warning(self, "读取 OIR 文件夹失败", str(error))
            return
        if not files:
            QMessageBox.information(self, "设置 OIR 分组", "当前扫描范围内没有 .oir 文件。")
            return
        dialog = OirGroupDialog(self, files, self.value())
        self.dialog = dialog
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.finished.connect(self.finish)
        dialog.open()

    def finish(self, result):
        dialog, self.dialog = self.dialog, None
        if result == QDialog.DialogCode.Accepted:
            self.saved = {"source": str(self.folder()), "groups": dialog.grouping}
            self.refresh()
