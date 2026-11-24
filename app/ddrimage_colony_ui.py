"""Draw and adjust circular colony counting regions on the original image."""

from math import hypot, isfinite
from pathlib import Path

from PySide6.QtCore import QRectF, QSignalBlocker, Qt
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QDoubleSpinBox, QFormLayout, QLabel, QPushButton

from ddrimage_preview import colony_pixmap
from ddrimage_review_ui import ReviewCanvas, ReviewDialog


class CircleCanvas(ReviewCanvas):
    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.editing = False
        self.setAccessibleName("单克隆形成圆形 ROI 预览")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            point = self.mapToScene(event.position().toPoint())
            if self.editor.begin_edit(point):
                self.editing = True
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.editing:
            self.editor.drag_circle(self.mapToScene(event.position().toPoint()))
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.editing and event.button() == Qt.MouseButton.LeftButton:
            self.editing = False
            self.editor.finish_edit(self.mapToScene(event.position().toPoint()))
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class ColonyReviewDialog(ReviewDialog):
    def heading(self):
        return "矫正单克隆形成 ROI"

    def create_canvas(self):
        return CircleCanvas(self)

    def create_controls(self, controls):
        self.active = None
        self.drag = None
        self.draft = None
        hint = QLabel("拖动圆内可移动。拖动圆边可改变半径。点「圈定正圆」后，从圆心拖向边缘。"
                      "\n右键排除或恢复，滚轮缩放，按住中键或右键拖动画布。")
        hint.setWordWrap(True)
        controls.addWidget(hint)
        self.add_roi = QPushButton("圈定正圆")
        self.add_roi.setCheckable(True)
        controls.addWidget(self.add_roi)
        form = QFormLayout()
        self.fields = []
        for label in ("圆心 X (px)", "圆心 Y (px)", "半径 (px)"):
            number = QDoubleSpinBox()
            number.setDecimals(2)
            number.setRange(0, 1_000_000)
            number.setKeyboardTracking(False)
            number.setAccessibleName(label)
            number.editingFinished.connect(self.commit_fields)
            form.addRow(label, number)
            self.fields.append(number)
        controls.addLayout(form)
        self.remove = QPushButton("移除选中 ROI")
        self.remove.clicked.connect(self.remove_circle)
        controls.addWidget(self.remove)
        reset = QPushButton("恢复本图的初始 ROI")
        reset.clicked.connect(self.reset_current)
        controls.addWidget(reset)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        controls.addWidget(self.summary)

    def load_frame(self):
        frames = self.by_folder[self.folder.currentData()]
        self.key = frames[self.position]
        self.frame = self.index["frames"][self.key]
        self.active = self.drag = self.draft = None
        self.overlay = []
        self.canvas.scene().clear()
        self.canvas.setSceneRect(QRectF())
        self.add_roi.setChecked(False)
        self.previous.setEnabled(self.position > 0)
        self.next.setEnabled(self.position + 1 < len(frames))
        self.position_label.setText(f"{Path(self.frame['image']).name} · {self.position + 1}/{len(frames)}")
        try:
            self.run.verify_sources([self.frame])
            image = colony_pixmap(self.run.image_path(self.frame["display"]))
            self.canvas.setSceneRect(self.canvas.scene().addPixmap(image).boundingRect())
            self.active = 0 if self.circles() else None
            self.draw_overlay()
            self.canvas.fit_image()
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            self.update_actions()
            self.summary.setText(f"无法查看此图：{error}")

    def circles(self):
        return self.current_edit().get("circles", self.frame["circles"])

    def draw_overlay(self):
        for item in self.overlay:
            self.canvas.scene().removeItem(item)
        self.overlay.clear()
        circles = list(self.circles())
        if self.draft is not None:
            if self.active is None:
                circles.append(self.draft)
            else:
                circles[self.active] = self.draft
        for i, (x, y, r) in enumerate(circles):
            selected = i == self.active or (self.active is None and self.draft is not None and i == len(circles) - 1)
            excluded = i in self.current_edit().get("excluded_roi", [])
            pen = QPen(QColor("#AA9E95" if excluded else "#FFBF4F" if selected else "#56E3D3"))
            if excluded:
                pen.setStyle(Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            pen.setWidth(3 if selected else 2)
            self.overlay.append(self.canvas.scene().addEllipse(x-r, y-r, 2*r, 2*r, pen))
        self.update_actions()

    def pick(self, point, button=Qt.MouseButton.LeftButton, *_):
        hit = self.hit(point)
        if hit is None:
            self.active = None
            self.draw_overlay()
        elif button == Qt.MouseButton.RightButton:
            self.active = hit[0]
            excluded = set(self.current_edit().get("excluded_roi", []))
            excluded.symmetric_difference_update({self.active})
            self.store({"circles": self.circles(), "excluded_roi": sorted(excluded)})

    def hit(self, point):
        tolerance = 8 / max(self.canvas.transform().m11(), 0.001)
        for i, (x, y, r) in enumerate(self.circles()):
            distance = hypot(point.x() - x, point.y() - y)
            if distance <= r + tolerance:
                return i, abs(distance - r) <= tolerance
        return None

    def begin_edit(self, point):
        if self.canvas.sceneRect().isEmpty() or not self.canvas.sceneRect().contains(point):
            return False
        if self.add_roi.isChecked():
            self.active = None
            start = [point.x(), point.y(), 0.0]
            self.drag = point, start, True
            return True
        hit = self.hit(point)
        if hit is None:
            return False
        self.active, resize = hit
        self.drag = point, self.circles()[self.active], resize
        self.draw_overlay()
        return True

    def drag_circle(self, point):
        origin, (x, y, r), resize = self.drag
        if resize:
            self.draft = [x, y, hypot(point.x()-x, point.y()-y)]
        else:
            delta = point-origin
            self.draft = [x+delta.x(), y+delta.y(), r]
        self.draw_overlay()

    def finish_edit(self, point):
        origin, _, _ = self.drag
        if (point - origin).manhattanLength() * self.canvas.transform().m11() >= 3:
            self.drag_circle(point)
        circle = self.draft
        self.drag = self.draft = None
        self.add_roi.setChecked(False)
        if circle is not None:
            self.commit_circle(circle)
        else:
            self.draw_overlay()

    def commit_fields(self):
        if self.active is not None:
            circle = [field.value() for field in self.fields]
            if any(abs(a-b) > 0.006 for a, b in zip(circle, self.circles()[self.active])):
                self.commit_circle(circle)

    def commit_circle(self, circle):
        x, y, r = circle
        area = self.canvas.sceneRect()
        margin = self.frame["margin"]
        updated = list(self.circles())
        if (not all(map(isfinite, circle)) or r < 1 or
                min(x-r, y-r, area.width()-1-x-r, area.height()-1-y-r) < margin):
            self.draw_overlay()
            self.summary.setText(f"圆形 ROI 须完整位于原图内，并离边缘至少 {margin} px。")
            return
        if any(i != self.active and hypot(x-a, y-b) < r+c for i, (a,b,c) in enumerate(updated)):
            self.draw_overlay()
            self.summary.setText("圆形 ROI 与另一个区域重叠，请调整位置或半径。")
            return
        if self.active is None:
            self.active = len(updated)
            updated.append(circle)
        else:
            updated[self.active] = circle
        self.store({"circles": updated, "excluded_roi": self.current_edit().get("excluded_roi", [])})

    def remove_circle(self):
        updated = list(self.circles())
        if self.active is not None:
            excluded = [i - (i > self.active) for i in self.current_edit().get("excluded_roi", []) if i != self.active]
            updated.pop(self.active)
            self.active = None
            self.store({"circles": updated, "excluded_roi": excluded})

    def reset_current(self):
        self.active = None
        self.store({})

    def store(self, edit):
        if "circles" in edit and (edit["circles"] != self.frame["circles"] or edit.get("excluded_roi")):
            if not edit.get("excluded_roi"):
                edit.pop("excluded_roi", None)
            self.edits[self.key] = edit
        else:
            self.edits.pop(self.key, None)
        self.draw_overlay()

    def update_actions(self):
        circles = self.circles()
        readable = not self.canvas.sceneRect().isEmpty()
        active = readable and self.active is not None and self.active < len(circles)
        for i, field in enumerate(self.fields):
            field.setEnabled(active)
            with QSignalBlocker(field):
                field.setValue((self.draft or circles[self.active])[i] if active else 0)
        self.add_roi.setEnabled(readable)
        self.remove.setEnabled(active)
        self.apply.setEnabled(self.edits != self.index["edits"] and all(
            edit.get("circles") for edit in self.edits.values()))
        excluded = len(self.current_edit().get("excluded_roi", []))
        self.summary.setText(f"当前 {len(circles)} 个 ROI，排除 {excluded} 个" if circles else "请圈定至少一个完整的圆形 ROI。")
