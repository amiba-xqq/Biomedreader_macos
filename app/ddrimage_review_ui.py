"""Review detected nuclei and fiber pairs against the original images."""

from copy import deepcopy
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QGraphicsScene, QGraphicsView, QHBoxLayout,
    QLabel, QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from ddrimage_review_data import ReviewRun, load_review, neighboring_nuclei, nucleus_labels


class ReviewCanvas(QGraphicsView):
    picked = Signal(QPointF, object, object)
    view_changed = Signal()

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor("#211C19"))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.press = None
        self.dragged = False
        self.fitted = True
        self.setAccessibleName("图像矫正预览")

    def fit_image(self):
        if not self.sceneRect().isEmpty():
            self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.fitted = True

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fitted:
            self.fit_image()

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120
        current = self.transform().m11()
        if steps and current > 0:
            factor = min(18, max(0.025, current * 1.22 ** steps)) / current
            self.scale(factor, factor)
            self.fitted = False
            self.view_changed.emit()
        event.accept()

    def mousePressEvent(self, event):
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self.press = self.last = event.position()
            self.dragged = False
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.press is None:
            return super().mouseMoveEvent(event)
        pos = event.position()
        if self.dragged or (pos - self.press).manhattanLength() >= QApplication.startDragDistance():
            self.dragged = True
            delta = pos - self.last
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - round(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - round(delta.y()))
            self.last = pos
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.view_changed.emit()
        event.accept()

    def mouseReleaseEvent(self, event):
        if self.press is None:
            return super().mouseReleaseEvent(event)
        if not self.dragged and event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            self.picked.emit(self.mapToScene(event.position().toPoint()), event.button(), event.modifiers())
        self.press = None
        self.unsetCursor()
        event.accept()


def pixmap(array):
    sample = np.ascontiguousarray(array, dtype=np.uint8)
    height, width, channels = sample.shape
    fmt = QImage.Format.Format_RGB888 if channels == 3 else QImage.Format.Format_RGBA8888
    return QPixmap.fromImage(QImage(sample.data, width, height, sample.strides[0], fmt).copy())


class ReviewDialog(QDialog):
    def __init__(self, result, parent):
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.WindowMinMaxButtonsHint
                         | Qt.WindowType.WindowCloseButtonHint)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowTitle("矫正图像统计 · DDRimage")
        self.resize(1240, 840)
        self.result = result
        self.index = load_review(result)
        self.run = ReviewRun(Path(self.index["source"]), result, self.index)
        self.edits = deepcopy(self.index["edits"])
        self.fibers = self.index["script"] == "DNA_fiber.py"
        self.by_folder = {}
        for key, frame in self.index["frames"].items():
            folder = str(Path(frame["image"]).parent)
            self.by_folder.setdefault(folder, []).append(key)
        self.key = None
        self.selected = set()
        self.shortcut_anchor = None
        self.original = None
        self.overlay = []
        self.position = 0

        root = QVBoxLayout(self)
        heading = QLabel(self.heading())
        heading.setObjectName("PageTitle")
        root.addWidget(heading)
        header = QHBoxLayout()
        self.folder = QComboBox()
        self.folder.setAccessibleName("原图子文件夹")
        for name in sorted(self.by_folder, key=str.casefold):
            self.folder.addItem(name, name)
        header.addWidget(QLabel("原图子文件夹"))
        header.addWidget(self.folder, 1)
        self.position_label = QLabel()
        self.position_label.setMargin(4)
        header.addWidget(self.position_label)
        fit = QPushButton("适合窗口")
        fit.clicked.connect(lambda: self.canvas.fit_image())
        header.addWidget(fit)
        root.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        picture = QWidget()
        picture_layout = QVBoxLayout(picture)
        picture_layout.setContentsMargins(0, 0, 0, 0)
        self.original_button = None
        if self.fibers:
            self.original_button = QPushButton("显示原图")
            self.original_button.setProperty("secondary", True)
            self.original_button.setCheckable(True)
            self.original_button.toggled.connect(self.toggle_fiber_original)
            picture_layout.addWidget(self.original_button, 0, Qt.AlignmentFlag.AlignLeft)
        image_row = QHBoxLayout()
        picture_layout.addLayout(image_row, 1)
        image_row.setContentsMargins(0, 0, 0, 0)
        self.previous = QPushButton("‹")
        self.next = QPushButton("›")
        self.previous.setAccessibleName("上一张图像")
        self.next.setAccessibleName("下一张图像")
        self.previous.setToolTip("上一张图像")
        self.next.setToolTip("下一张图像")
        self.previous.clicked.connect(lambda: self.navigate(-1))
        self.next.clicked.connect(lambda: self.navigate(1))
        self.canvas = self.create_canvas()
        self.canvas.picked.connect(self.pick)
        image_row.addWidget(self.previous)
        image_row.addWidget(self.canvas, 1)
        image_row.addWidget(self.next)
        splitter.addWidget(picture)

        side = QWidget()
        side.setMinimumWidth(205)
        controls = QVBoxLayout(side)
        self.create_controls(controls)
        controls.addStretch()
        self.apply = QPushButton("应用矫正并重新分析")
        self.apply.clicked.connect(self.accept)
        controls.addWidget(self.apply)
        close = QPushButton("取消")
        close.clicked.connect(self.reject)
        controls.addWidget(close)
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([975, 245])
        root.addWidget(splitter, 1)
        self.folder.currentIndexChanged.connect(self.change_folder)
        self.change_folder()

    def heading(self):
        return "矫正 DNA Fiber 配对" if self.fibers else "矫正细胞核轮廓"

    def create_canvas(self):
        return ReviewCanvas()

    def create_controls(self, controls):
        help_label = QLabel("左键选中配对。右键单击任一框可排除整对，再次右键恢复。" if self.fibers else
                            "左键点选核。右键排除，再次右键恢复。\n"
                            "Ctrl＋左键依次点击，可将相邻核并入当前选中的核。")
        if not self.fibers:
            help_label.setToolTip("两核几何中心的距离不超过较大核的最大几何直径时，可以快捷合并。")
        help_label.setWordWrap(True)
        controls.addWidget(help_label)
        gestures = QLabel("滚轮缩放，按住鼠标拖动查看。已排除区域变黑，保留轮廓，仍可点选恢复。")
        gestures.setWordWrap(True)
        controls.addWidget(gestures)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        controls.addWidget(self.summary)
        self.exclude = QPushButton("排除选中" if self.fibers else "排除选中的核")
        self.restore = QPushButton("恢复选中")
        self.merge = QPushButton("合并选中的核")
        self.merge.setVisible(not self.fibers)
        self.reset = QPushButton("重置当前图像")
        self.exclude.clicked.connect(lambda: self.set_excluded(True))
        self.restore.clicked.connect(lambda: self.set_excluded(False))
        self.merge.clicked.connect(self.merge_selected)
        self.reset.clicked.connect(self.reset_current)
        for button in (self.exclude, self.restore, self.merge, self.reset):
            controls.addWidget(button)

    def change_folder(self):
        self.position = 0
        self.load_frame()

    def navigate(self, step):
        self.position += step
        self.load_frame()

    def load_frame(self):
        import tifffile

        frames = self.by_folder[self.folder.currentData()]
        self.key = frames[self.position]
        frame = self.index["frames"][self.key]
        self.selected.clear()
        self.shortcut_anchor = None
        self.overlay = []
        self.original = None
        self.canvas.scene().clear()
        self.previous.setEnabled(self.position > 0)
        self.next.setEnabled(self.position + 1 < len(frames))
        self.position_label.setText(f"{Path(frame['image']).name} · 页 {frame['page'] + 1} · {self.position + 1}/{len(frames)}")
        try:
            self.run.verify_sources([frame])
            source = self.run.image_path(frame["display"])
            with tifffile.TiffFile(source) as tif:
                if self.fibers:
                    series = tif.series[0]
                    pixels = series.asarray()
                    if series.axes == "SYX":
                        pixels = np.moveaxis(pixels, 0, -1)
                    pixels = np.rint(pixels * (255.0 / frame["maximum"])).clip(0, 255)
                else:
                    pixels = tif.pages[frame["page"]].asarray()
            if not self.fibers:
                with np.load(self.result / frame["labels"], allow_pickle=False) as saved:
                    self.original = saved["labels"]
                if tuple(pixels.shape[:2]) != self.original.shape:
                    raise ValueError("原图尺寸与核轮廓不一致。")
            image = self.canvas.scene().addPixmap(pixmap(pixels))
            self.canvas.setSceneRect(image.boundingRect())
            self.draw_overlay()
            self.canvas.fit_image()
        except (OSError, RuntimeError, ValueError, KeyError) as error:
            self.canvas.setSceneRect(0, 0, 0, 0)
            self.update_actions()
            self.summary.setText(f"无法查看此图：{error}")

    def current_edit(self):
        return self.edits.get(self.key, {})

    def toggle_fiber_original(self, original):
        self.original_button.setText("显示配对标注" if original else "显示原图")
        for item in self.overlay:
            item.setVisible(not original)
        self.update_actions()

    def draw_overlay(self):
        for item in self.overlay:
            self.canvas.scene().removeItem(item)
        self.overlay.clear()
        edit = self.current_edit()
        excluded = set(edit.get("excluded", []))
        if self.fibers:
            for pair, boxes in enumerate(self.index["frames"][self.key]["boxes"], 1):
                for points, color in zip(boxes, ("#FF5E61", "#70EF8A")):
                    pen = QPen(QColor("#B3B3B3" if pair in excluded else color))
                    if pair in excluded:
                        pen.setStyle(Qt.PenStyle.DashLine)
                    pen.setCosmetic(True)
                    pen.setWidth(3 if pair in self.selected else 2)
                    polygon = QPolygonF([QPointF(*point) for point in points])
                    brush = QBrush(Qt.GlobalColor.black) if pair in excluded else QBrush()
                    item = self.canvas.scene().addPolygon(polygon, pen, brush)
                    item.setZValue(1 if pair in excluded else 2)
                    item.setVisible(not self.original_button.isChecked())
                    self.overlay.append(item)
        else:
            labels = nucleus_labels(self.original, {"merged": edit.get("merged", [])})
            pad = np.pad(labels, 1)
            boundary = (labels > 0) & (
                (labels != pad[:-2, 1:-1]) | (labels != pad[2:, 1:-1])
                | (labels != pad[1:-1, :-2]) | (labels != pad[1:-1, 2:]))
            border = np.zeros((*labels.shape, 4), dtype=np.uint8)
            hidden = np.isin(labels, list(excluded))
            border[hidden] = (0, 0, 0, 255)
            border[boundary] = (86, 227, 211, 255)
            border[boundary & hidden] = (155, 145, 150, 255)
            border[boundary & np.isin(labels, list(self.selected))] = (255, 191, 79, 255)
            self.overlay.append(self.canvas.scene().addPixmap(pixmap(border)))
            self.labels = labels
        self.update_actions()

    def pick(self, point, button=Qt.MouseButton.LeftButton, modifiers=Qt.KeyboardModifier.NoModifier):
        if not self.overlay or (self.original_button is not None and self.original_button.isChecked()):
            return
        if self.fibers:
            selected = next((i // 2 + 1 for i, item in enumerate(self.overlay)
                             if item.polygon().containsPoint(point, Qt.FillRule.OddEvenFill)), 0)
        else:
            x, y = int(point.x()), int(point.y())
            height, width = self.original.shape
            selected = int(self.labels[y, x]) if 0 <= point.x() < width and 0 <= point.y() < height else 0
        if not selected:
            return
        if button == Qt.MouseButton.RightButton:
            self.selected = {selected}
            self.shortcut_anchor = None
            edit = self.current_edit()
            restore = self.selected_members(edit) <= set(edit.get("excluded", []))
            self.set_excluded(not restore)
            return
        if not self.fibers and modifiers & Qt.KeyboardModifier.ControlModifier:
            self.merge_shortcut(selected)
            return
        self.selected.symmetric_difference_update({selected})
        self.shortcut_anchor = selected if selected in self.selected else None
        self.draw_overlay()

    def merge_shortcut(self, selected):
        excluded = self.current_edit().get("excluded", [])
        if selected in excluded:
            self.summary.setText("请先恢复已排除的核。")
            return
        anchor = self.shortcut_anchor
        if anchor is None or anchor not in self.selected or anchor in excluded:
            self.selected = {selected}
            self.shortcut_anchor = selected
            self.draw_overlay()
        elif anchor != selected:
            if neighboring_nuclei(self.labels, anchor, selected):
                self.selected = {anchor, selected}
                self.merge_selected()
            else:
                self.summary.setText("该核与当前核不相邻，已跳过。")

    def selected_members(self, edit):
        groups = {min(group): group for group in edit.get("merged", [])}
        return {member for label in self.selected for member in groups.get(label, [label])}

    def update_actions(self):
        edit = self.current_edit()
        excluded = set(edit.get("excluded", []))
        selected = self.selected_members(edit)
        raw = self.original_button is not None and self.original_button.isChecked()
        self.exclude.setEnabled(not raw and bool(selected - excluded))
        self.restore.setEnabled(not raw and bool(selected & excluded))
        self.merge.setEnabled(len(self.selected) >= 2 and not selected & excluded)
        self.reset.setEnabled(bool(edit) and self.canvas.scene().items() != [])
        self.apply.setEnabled(self.edits != self.index["edits"])
        count = sum(self.edits.get(k) != self.index["edits"].get(k)
                    for k in self.edits.keys() | self.index["edits"].keys())
        self.summary.setText(f"选中 {len(self.selected)} 个\n待应用 {count} 个图像页")

    def set_excluded(self, value):
        edit = deepcopy(self.current_edit())
        members = self.selected_members(edit)
        old = set(edit.get("excluded", []))
        edit["excluded"] = sorted(old | members if value else old - members)
        self.store(edit)

    def merge_selected(self):
        edit = deepcopy(self.current_edit())
        members = self.selected_members(edit)
        groups = [group for group in edit.get("merged", []) if not members.intersection(group)]
        edit["merged"] = sorted([*groups, sorted(members)])
        self.selected = {min(members)}
        self.shortcut_anchor = min(members)
        self.store(edit)

    def reset_current(self):
        self.selected.clear()
        self.shortcut_anchor = None
        self.store({})

    def store(self, edit):
        edit = {key: value for key, value in edit.items() if value}
        if edit:
            self.edits[self.key] = edit
        else:
            self.edits.pop(self.key, None)
        self.draw_overlay()

    def reject(self):
        if self.edits != self.index["edits"] and QMessageBox.question(
            self, "放弃未应用的矫正", "关闭窗口并放弃本次未应用的修改？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        super().reject()

    def request(self):
        return {"result": str(self.result), "revision": self.index["revision"], "edits": self.edits}
