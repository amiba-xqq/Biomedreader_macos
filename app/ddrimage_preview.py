"""Compare the original image and intermediate outputs of a DDRimage test run."""

from contextlib import closing
from pathlib import Path
from typing import NamedTuple

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QTransform
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QSpinBox,
    QVBoxLayout, QWidget,
)

from ddrimage_review_ui import ReviewCanvas, pixmap


class ImageType(NamedTuple):
    name: str
    path: Path
    maximum: float | None = None
    component: int | None = None
    colony_original: bool = False


def load_image(path, page, maximum=None, component=None, colony_original=False):
    if colony_original:
        return colony_pixmap(path)
    if path.suffix.lower() == ".pdf":
        import pypdfium2

        with pypdfium2.PdfDocument(path) as document, closing(document[page]) as sheet:
            width, height = sheet.get_size()
            scale = min(5.0, 1600.0 / max(width, height))
            with closing(sheet.render(scale=scale)) as bitmap:
                return pixmap(np.asarray(bitmap.to_pil().convert("RGB")))
    import tifffile

    with tifffile.TiffFile(path) as tif:
        layer = tif.pages[page]
        pixels = layer.asarray()
        if layer.axes == "SYX":
            pixels = np.moveaxis(pixels, 0, -1)
    if pixels.dtype == np.bool_:
        pixels = pixels.astype(np.uint8) * 255
    elif maximum is not None or pixels.dtype != np.uint8:
        limit = maximum or (np.iinfo(pixels.dtype).max if pixels.dtype.kind in "ui" else 1.0)
        pixels = np.rint(np.clip(pixels * (255.0 / limit), 0, 255)).astype(np.uint8)
    if pixels.ndim == 2:
        pixels = np.repeat(pixels[..., None], 3, axis=-1)
    if component is not None:
        channel = np.zeros_like(pixels)
        channel[..., component] = pixels[..., component]
        pixels = channel
    return pixmap(pixels)


def colony_pixmap(path):
    from PIL import Image, ImageOps

    with Image.open(path) as raw:
        frame = ImageOps.exif_transpose(raw)
        if frame.mode in ("RGBA", "LA") or "transparency" in frame.info:
            base = Image.new("RGBA", frame.size, "white")
            frame = Image.alpha_composite(base, frame.convert("RGBA"))
        if frame.mode in ("I", "F") or frame.mode.startswith("I;16"):
            array = np.asarray(frame, dtype=np.float32)
            low, high = np.percentile(array, (0.1, 99.9))
            if high <= low:
                low, high = array.min(), array.max()
            scaled = (np.clip((array - low) / max(float(high - low), 1e-9), 0, 1) * 255).astype(np.uint8)
            return pixmap(np.repeat(scaled[..., None], 3, axis=-1))
        return pixmap(np.asarray(frame.convert("RGB")))


class ComparisonCanvas(ReviewCanvas):
    def __init__(self):
        super().__init__()
        self.fitted = False
        self.image_rect = QRectF()
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAccessibleName("同步图像预览")
        self.center = QPointF(0.5, 0.5)
        self.zoom = 1.0

    def display(self, image=None):
        self.scene().clear()
        self.image_rect = QRectF()
        if image is not None:
            self.image_rect = self.scene().addPixmap(image).boundingRect()
        w, h = self.image_rect.width(), self.image_rect.height()
        self.setSceneRect(self.image_rect.adjusted(-w, -h, w, h))
        self.restore(self.zoom, self.center)

    def fit_scale(self):
        if self.image_rect.isEmpty():
            return 1.0
        return max(0.001, min((self.viewport().width() - 8) / self.image_rect.width(),
                              (self.viewport().height() - 8) / self.image_rect.height()))

    def restore(self, zoom, center):
        self.zoom, self.center = zoom, center
        if self.image_rect.isEmpty():
            return
        scale = zoom * self.fit_scale()
        self.setTransform(QTransform.fromScale(scale, scale))
        self.centerOn(center.x() * self.image_rect.width(), center.y() * self.image_rect.height())

    def viewport_state(self):
        center = self.mapToScene(self.viewport().rect().center())
        x = min(1.0, max(0.0, center.x() / self.image_rect.width()))
        y = min(1.0, max(0.0, center.y() / self.image_rect.height()))
        return self.transform().m11() / self.fit_scale(), QPointF(x, y)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.restore(self.zoom, self.center)

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120
        zoom = min(60.0, max(1.0, self.zoom * 1.22 ** steps))
        if not self.image_rect.isEmpty() and abs(zoom - self.zoom) > 1e-8:
            self.scale(zoom / self.zoom, zoom / self.zoom)
            self.view_changed.emit()
        event.accept()


class ComparisonImage(QFrame):
    def __init__(self, preview, position):
        super().__init__()
        self.preview = preview
        self.position = position
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(4)
        bar = QHBoxLayout()
        self.selector = QComboBox()
        self.selector.setAccessibleName("图像类型")
        self.selector.addItems([image.name for image in preview.images])
        self.selector.setCurrentIndex(position)
        self.selector.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.selector.currentIndexChanged.connect(self.select)
        previous = QPushButton("‹")
        following = QPushButton("›")
        self.remove = QPushButton("×")
        for button, label in ((previous, "上一种图像"), (following, "下一种图像"), (self.remove, "移除此图")):
            button.setProperty("secondary", True)
            button.setAccessibleName(label)
            button.setToolTip(label)
            button.setFixedWidth(36)
        previous.clicked.connect(lambda: self.navigate(-1))
        following.clicked.connect(lambda: self.navigate(1))
        self.remove.clicked.connect(lambda: preview.remove_image(self))
        bar.addWidget(previous)
        bar.addWidget(self.selector, 1)
        bar.addWidget(following)
        bar.addWidget(self.remove)
        layout.addLayout(bar)
        self.canvas = ComparisonCanvas()
        self.canvas.setMinimumSize(145, 125)
        self.canvas.view_changed.connect(lambda: preview.synchronize(self.canvas))
        layout.addWidget(self.canvas, 1)

    def navigate(self, step):
        self.selector.setCurrentIndex((self.position + step) % len(self.preview.images))

    def select(self, position):
        self.position = position
        self.preview.update_available_types()
        self.refresh()

    def refresh(self):
        image = self.preview.images[self.position]
        self.selector.setToolTip(image.name)
        try:
            self.canvas.display(load_image(image.path, self.preview.page.value() - 1,
                                           image.maximum, image.component, image.colony_original))
        except (OSError, ValueError, RuntimeError, IndexError) as error:
            self.canvas.display()
            self.selector.setToolTip(f"无法预览 · {image.name}\n{error}")


class TestImagePreview(QWidget):
    def __init__(self, result, data):
        super().__init__()
        plot = bool(data.get("plot"))
        self.limit = 2 if plot else 4
        source = None if plot else Path(data["source"])
        maximum = data.get("maximum")
        colony = bool(data.get("colony"))
        self.images = [] if plot else [ImageType("原始图像" if colony else "原图 · 合并 (Merge)",
                                                source, maximum, colony_original=colony)]
        self.images.extend(ImageType(name, Path(path)) for name, path in data.get("originals", []))
        if maximum is not None:
            self.images.extend(ImageType(f"原图 · {name}通道", source, maximum, index)
                               for index, name in enumerate(("红色", "绿色")))
        originals = len(self.images)
        for name, relative in data["images"]:
            file = (result / relative).resolve()
            if file.is_relative_to(result):
                self.images.append(ImageType(name, file))
        self.panels = []
        self.zoom, self.center = 1.0, QPointF(0.5, 0.5)
        layout = QVBoxLayout(self)
        heading = QLabel("统计图预览" if plot else "测试图像对比")
        heading.setObjectName("PageTitle")
        layout.addWidget(heading)
        label = QLabel(source.name if source is not None else f"{len(self.images)} 张统计图")
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        source_row = QHBoxLayout()
        source_row.addWidget(label, 1)
        layout.addLayout(source_row)
        top = QHBoxLayout()
        self.add = QPushButton("＋ 添加对照图")
        self.add_menu = QMenu(self.add)
        self.add.setMenu(self.add_menu)
        self.type_actions = []
        if not plot:
            self.add_menu.addSection("原始图像")
        for position, image in enumerate(self.images):
            if not plot and position == originals:
                self.add_menu.addSection("中间产物")
            action = self.add_menu.addAction(image.name)
            action.triggered.connect(lambda checked=False, index=position: self.add_image(index))
            self.type_actions.append(action)
        fit = QPushButton("适合窗口")
        fit.setProperty("secondary", True)
        fit.clicked.connect(self.fit_images)
        self.full_width = QPushButton("铺满窗口")
        self.full_width.setProperty("secondary", True)
        top.addWidget(self.add)
        top.addWidget(fit)
        top.addWidget(self.full_width)
        top.addStretch()
        self.page = QSpinBox()
        self.page.setRange(1, max(1, data["pages"]))
        self.page.setAccessibleName("TIFF 页码")
        self.page.setPrefix("第 ")
        self.page.setSuffix(f" / {self.page.maximum()} 页")
        self.page.setVisible(self.page.maximum() > 1)
        self.page.valueChanged.connect(self.switch_page)
        source_row.addWidget(self.page)
        layout.addLayout(top)
        hint = QLabel("选择图像类型，或用左右箭头切换。滚轮缩放及拖动会同步至所有预览。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.grid = QGridLayout()
        self.grid.setSpacing(8)
        layout.addLayout(self.grid, 1)
        self.add_image(0)
        if plot and len(self.images) > 1:
            self.add_image(1)
        elif not plot and len(self.images) > originals:
            self.add_image(originals)

    def add_image(self, position):
        if len(self.panels) >= self.limit or any(panel.position == position for panel in self.panels):
            return
        panel = ComparisonImage(self, position)
        self.panels.append(panel)
        self.reflow()
        panel.refresh()
        panel.canvas.restore(self.zoom, self.center)

    def remove_image(self, panel):
        if len(self.panels) > 1:
            self.panels.remove(panel)
            self.grid.removeWidget(panel)
            panel.hide()
            panel.deleteLater()
            self.reflow()

    def reflow(self):
        for i, panel in enumerate(self.panels):
            self.grid.addWidget(panel, i // 2, i % 2)
            panel.remove.setEnabled(len(self.panels) > 1)
        for row in range(2):
            self.grid.setRowStretch(row, int(row == 0 or len(self.panels) > 2))
        self.update_available_types()

    def update_available_types(self):
        shown = {panel.position for panel in self.panels}
        for index, action in enumerate(self.type_actions):
            action.setEnabled(index not in shown)
        self.add.setEnabled(len(self.panels) < self.limit and len(shown) < len(self.images))

    def switch_page(self):
        for panel in self.panels:
            panel.refresh()

    def fit_images(self):
        self.zoom, self.center = 1.0, QPointF(0.5, 0.5)
        for panel in self.panels:
            panel.canvas.restore(self.zoom, self.center)

    def synchronize(self, canvas):
        if canvas.image_rect.isEmpty():
            return
        zoom, self.center = canvas.viewport_state()
        self.zoom = min(60.0, max(1.0, zoom))
        for panel in self.panels:
            panel.canvas.restore(self.zoom, self.center)
