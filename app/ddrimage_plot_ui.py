"""Drag DDRimage groups into the plot and choose comparisons by group name."""

from pathlib import Path

from PySide6.QtCore import QMimeData, QSize, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from ddrimage_plot_groups import plot_folders
from figure_comparisons import ComparisonDialog


class PlotGroupList(QListWidget):
    mime_type = "application/x-biomed-ddrimage-plot-groups"

    def __init__(self, owner, caption, tint):
        super().__init__(owner)
        self.owner = owner
        self.setAccessibleName(caption)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSpacing(4)
        self.setStyleSheet(
            "QListWidget { background:#FFFCF8; border:1px solid #E6D7C7; border-radius:9px; }"
            f"QListWidget::item {{ color:#51392C; background:{tint}; border:1px solid #DECBB9;"
            " border-radius:7px; padding:5px 9px; }"
            "QListWidget::item:selected { background:#A95122; color:white; }"
        )

    def set_names(self, names):
        self.clear()
        for name in names:
            card = QListWidgetItem(name)
            card.setSizeHint(QSize(120, 32))
            self.addItem(card)

    def names(self):
        return [self.item(i).text() for i in range(self.count())]

    def startDrag(self, actions):
        if not self.selectedItems():
            return
        mime = QMimeData()
        mime.setData(self.mime_type, b"groups")
        drag = QDrag(self)
        drag.setMimeData(mime)
        card = self.selectedItems()[0]
        drag.setPixmap(self.viewport().grab(self.visualItemRect(card)))
        drag.exec(Qt.DropAction.MoveAction)

    def can_drop(self, event):
        source = event.source()
        return isinstance(source, PlotGroupList) and source.owner is self.owner and event.mimeData().hasFormat(self.mime_type)

    def dragEnterEvent(self, event):
        if self.can_drop(event):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if self.can_drop(event):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not self.can_drop(event):
            event.ignore()
            return
        point = event.position().toPoint()
        target = self.itemAt(point)
        row = self.count() if target is None else self.row(target) + (point.y() >= self.visualItemRect(target).center().y())
        self.receive(event.source(), row)
        event.acceptProposedAction()

    def receive(self, source, row):
        selected = sorted(source.row(card) for card in source.selectedItems())
        if self is source:
            row -= sum(i < row for i in selected)
        cards = [source.takeItem(i) for i in reversed(selected)]
        self.clearSelection()
        for offset, card in enumerate(reversed(cards)):
            self.insertItem(row + offset, card)
            card.setSelected(True)
        self.owner.update_buttons()


class PlotOrderDialog(QDialog):
    def __init__(self, parent, original, chosen):
        super().__init__(parent)
        self.setWindowTitle("设置作图分组")
        self.resize(720, 470)
        layout = QVBoxLayout(self)
        hint = QLabel("拖动卡片排列组别，列表从上到下对应图中从左到右。可用 Ctrl 或 Shift 多选，"
                      "再把不需要作图的组拖到右侧。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        columns = QHBoxLayout()
        self.chosen = PlotGroupList(self, "参与作图", "#FFF1E1")
        self.omitted = PlotGroupList(self, "暂不作图", "#F1EDEA")
        self.chosen.set_names(chosen)
        self.omitted.set_names(name for name in original if name not in chosen)
        for cards in (self.chosen, self.omitted):
            column = QVBoxLayout()
            heading = QLabel(cards.accessibleName())
            heading.setStyleSheet("font-weight:700;")
            column.addWidget(heading)
            column.addWidget(cards, 1)
            columns.addLayout(column, 1)
        layout.addLayout(columns, 1)
        actions = QHBoxLayout()
        for text, action in (
            ("上移", lambda: self.shift(-1)), ("下移", lambda: self.shift(1)),
            ("移出作图", lambda: self.omitted.receive(self.chosen, self.omitted.count())),
            ("加入作图", lambda: self.chosen.receive(self.omitted, self.chosen.count())),
            ("恢复 CSV 顺序", lambda: self.reset_groups(original)),
        ):
            button = QPushButton(text)
            button.setProperty("secondary", True)
            button.clicked.connect(action)
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.update_buttons()

    def shift(self, offset):
        rows = sorted(self.chosen.row(card) for card in self.chosen.selectedItems())
        if rows and rows[0] + offset >= 0 and rows[-1] + offset < self.chosen.count():
            dest = rows[0] - 1 if offset < 0 else rows[-1] + 2
            self.chosen.receive(self.chosen, dest)

    def reset_groups(self, original):
        self.chosen.set_names(original)
        self.omitted.clear()
        self.update_buttons()

    def update_buttons(self):
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.chosen.count() > 0)


class PlotGroupField(QPushButton):
    changed = Signal()

    def __init__(self, workflow, source_path, saved):
        super().__init__("设置作图分组…")
        self.workflow = workflow
        self.source_path = source_path
        self.saved = saved if isinstance(saved, dict) else {}
        self.setSizePolicy(QSizePolicy.Policy.Maximum, self.sizePolicy().verticalPolicy())
        self.clicked.connect(self.configure)

    def group_names(self):
        source = self.source_path().strip()
        if not source:
            return "", []
        try:
            path = Path(source).expanduser().resolve(strict=True)
            return str(path), [folder.name for folder in plot_folders(path, self.workflow)]
        except (OSError, ValueError):
            return "", []

    def groups(self):
        source, names = self.group_names()
        if source != self.saved.get("source"):
            return names, names
        chosen = [name for name in self.saved.get("selected", []) if name in names]
        known = set(self.saved.get("selected", [])) | set(self.saved.get("omitted", []))
        return names, chosen + [name for name in names if name not in known]

    def configure(self):
        source, _ = self.group_names()
        original, chosen = self.groups()
        if not original:
            QMessageBox.information(self, "设置作图分组", "请先选择包含分组子文件夹的统计结果目录。")
            return
        dialog = PlotOrderDialog(self, original, chosen)
        if dialog.exec():
            self.saved = {"source": source, "selected": dialog.chosen.names(), "omitted": dialog.omitted.names()}
            self.refresh()

    def refresh(self):
        names, chosen = self.groups()
        summary = f"（{len(chosen)}/{len(names)} 组）" if names else ""
        self.setText("设置作图分组" + summary + "…")
        self.changed.emit()

    def value(self):
        names, chosen = self.groups()
        if names and not chosen:
            raise ValueError("请至少保留一个作图分组")
        return "default" if names == chosen else ",".join(str(names.index(name) + 1) for name in chosen)


class PlotComparisonField(QWidget):
    def __init__(self, groups, saved):
        super().__init__()
        self.groups = groups
        self.pairs = saved.get("pairs", []) if isinstance(saved, dict) else []
        mode = saved.get("mode", "all") if isinstance(saved, dict) else saved
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.mode = QComboBox()
        for label, value in (("全部组两两比较", "all"), ("不进行统计检验", "none"), ("手动选择组对", "manual")):
            self.mode.addItem(label, value)
        self.mode.setCurrentIndex(max(0, self.mode.findData(mode)))
        self.picker = QPushButton()
        self.picker.clicked.connect(self.configure)
        layout.addWidget(self.mode)
        layout.addWidget(self.picker)
        layout.addStretch()
        self.mode.currentIndexChanged.connect(self.refresh)
        self.groups.changed.connect(self.refresh)
        self.refresh()

    def visible_pairs(self, names):
        return [pair for pair in self.pairs if len(pair) == 2 and all(name in names for name in pair)]

    def configure(self):
        _, chosen = self.groups.groups()
        if len(chosen) < 2:
            return
        dialog = ComparisonDialog(self, chosen, self.pairs)
        dialog.status.setText("这里只列出参与作图的组。改变作图顺序后，组对仍按组名对应。")
        if dialog.exec():
            hidden_pairs = [pair for pair in self.pairs if pair not in self.visible_pairs(chosen)]
            self.pairs = [*hidden_pairs, *dialog.pairs]
            self.refresh()

    def refresh(self):
        _, chosen = self.groups.groups()
        count = len(self.visible_pairs(chosen))
        self.picker.setText("选择组对" + (f"（{count}）" if count else ""))
        self.picker.setEnabled(self.mode.currentData() == "manual" and len(chosen) > 1)

    def value(self):
        mode = self.mode.currentData()
        if mode != "manual":
            return mode
        names, chosen = self.groups.groups()
        pairs = self.visible_pairs(chosen)
        if not pairs and len(chosen) > 1:
            raise ValueError("请选取当前作图组对，或切换为不进行统计检验")
        return ",".join(f"({names.index(a) + 1},{names.index(b) + 1})" for a, b in pairs) or "none"

    def settings(self):
        return {"mode": self.mode.currentData(), "pairs": self.pairs}
