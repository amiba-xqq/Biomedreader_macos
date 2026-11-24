from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from functools import partial
import codecs
from shiboken6 import isValid

from matplotlib import colormaps as MPL_COLORMAPS
from PySide6.QtCore import QProcess, QSignalBlocker, QSize, Qt, QUrl, QSettings, QTimer
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLayout, QLineEdit, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QTextEdit, QVBoxLayout, QWidget,
)

from card_board import CardBoard
from ddrimage_oir_ui import OirGroupField
from ddrimage_plot_ui import PlotComparisonField, PlotGroupField
from ddrimage_review_data import REVIEW_INDEX, REVIEW_WORKFLOWS
from ddrimage_output import clear_test_outputs, clear_window_backups, metadata_root, RESULT_MARKER
from ddrimage_worker import PAYLOAD, WORKFLOWS, legacy_statistics_destination, plot_output_root, previous_statistics_destination, statistics_lock

APP_DIR = Path(__file__).resolve().parent
NATURE_COLORS = ["#E64B35", "#4DBBD5", "#00A087", "#3C5488", "#F39B7F",
                 "#8491B4", "#91D1C2", "#DC0000", "#7E6148", "#B09C85"]
PRISM_COLORS = ["#1F77B4", "#FF7F0E", "#2CA02C", "#D62728", "#9467BD",
                "#8C564B", "#E377C2", "#7F7F7F", "#BCBD22", "#17BECF",
                "#E64B35", "#4DBBD5", "#00A087", "#3C5488", "#F39B7F", "#91D1C2"]
HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
_COLORBAR_ICONS = None


def field(key, label, default, kind="text", help_text="", targets=(), choices=(), group="参数"):
    return dict(key=key, label=label, default=default, kind=kind, help=help_text,
                targets=list(targets), choices=list(choices), group=group)


class NoWheelComboBox(QComboBox):
    """A combo box that only changes selection by click/keyboard, never wheel."""

    def wheelEvent(self, event):
        event.ignore()  # Let the enclosing page/log scroll area consume the wheel.


def dropped_folder(event):
    urls = event.mimeData().urls()
    if len(urls) == 1 and urls[0].isLocalFile():
        candidate = Path(urls[0].toLocalFile())
        if candidate.is_dir():
            return str(candidate)
    return None


class FolderEdit(QLineEdit):
    def mouseDoubleClickEvent(self, event):
        raw = self.text().strip()
        if event.button() == Qt.MouseButton.LeftButton and raw:
            path = Path(raw).expanduser()
            if path.is_dir():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def dragEnterEvent(self, event):
        if dropped_folder(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        path = dropped_folder(event)
        if path:
            self.setText(path)
            event.acceptProposedAction()
        else:
            event.ignore()


def _colorbar_icon(colors):
    pixmap = QPixmap(170, 18)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    try:
        width = pixmap.width()
        for index, color in enumerate(colors):
            left = round(index * width / len(colors))
            right = round((index + 1) * width / len(colors))
            painter.fillRect(left, 0, max(1, right - left), pixmap.height(), QColor(color))
    finally:
        painter.end()
    return QIcon(pixmap)


def colorbar_icons():
    global _COLORBAR_ICONS
    if _COLORBAR_ICONS is None:
        icons = {"nature": _colorbar_icon(NATURE_COLORS)}
        for name in sorted(MPL_COLORMAPS):
            cmap = MPL_COLORMAPS[name]
            samples = []
            for index in range(32):
                red, green, blue, _ = cmap(index / 31)
                samples.append(QColor.fromRgbF(float(red), float(green), float(blue)).name())
            icons[name] = _colorbar_icon(samples)
        _COLORBAR_ICONS = icons
    return _COLORBAR_ICONS


PAGES = [
    dict(
        id="lif", title="Leica / Olympus 导出 TIFF",
        description="批量转换 Leica LIF 或 Olympus OIR，生成合并图和独立通道 TIFF。",
        fields=[
            field("source_format", "原图类型", "leica", "choice", "只扫描所选类型的文件。",
                  [("lif2tif.py", "source_format")], [("leica", "Leica（LIF）"), ("olympus", "Olympus（OIR）")], "输入"),
            field("input_dir", "原图文件夹", "", "folder", "选择存放 LIF 或 OIR 文件的文件夹。",
                  [("lif2tif.py", "input_dir")], group="输入"),
            field("lif_mode", "导出内容", "merge_and_channels", "choice", "后续核内分析请选择合并图与独立通道。",
                  [("lif2tif.py", "lif2tif_mode")], [("merge_and_channels", "合并图与独立通道"), ("only_merge", "仅合并图")]),
            field("recursive", "扫描子文件夹", False, "check", "同时查找各级子目录中的所选格式文件。",
                  [("lif2tif.py", "recursive")]),
            field("oir_groups", "OIR 导出分组", {}, "oir_groups", "将文件拖入命名分组。待分配的文件会跳过。",
                  [("lif2tif.py", "oir_groups")]),
        ], stages=[("lif2tif.py", "导出 TIFF")],
    ),
    dict(
        id="foci", title="细胞核内 Foci", icon="02", warning=True,
        description="读取 LIF 导出的 TIFF，统计每个细胞核内的蛋白 foci，并生成分组散点图与统计结果。",
        fields=[
            field("input_dir", "TIFF 母文件夹", "", "folder", "选择导出工具生成的 *_tif 文件夹，内含各原图文件对应的子文件夹。", [("nucleus_foci.py", "input_dir"), ("foci_plot.py", "input_dir")], group="输入"),
            field("run_step", "本次运行步骤", "analysis", "choice", "二选一：图像统计生成 CSV/标注图；统计作图只读取已有 CSV，不重新分析图片。", choices=[("analysis", "仅运行图像统计"), ("plot", "仅生成统计图")], group="步骤"),
            field("foci_mode", "处理范围", "test", "choice", "可选范围：test 或 all；test 只处理首个子文件夹的首张合并图，all 处理全部。", [("nucleus_foci.py", "foci_mode")], [("test", "测试一张"), ("all", "全部处理")], "统计参数"),
            field("nucleus_area_px", "细胞核面积 (px²)", [1000, 20000], "pair_int", "整数范围：1 ≤ 下限 ≤ 上限；仅保留面积处于闭区间内的细胞核。", [("nucleus_foci.py", "nucleus_area_px")], group="统计参数"),
            field("foci_area_px", "Foci 面积 (px²)", [2, 25], "pair_int", "整数范围：1 ≤ 下限 ≤ 上限；仅统计面积处于闭区间内的 foci。", [("nucleus_foci.py", "foci_area_px")], group="统计参数"),
            field("dapi_adjust_windows", "DAPI 拉伸窗口", "full", "window", "可填 full、none，或整数 low,high；数值范围：0 ≤ low < high ≤ 255。", [("nucleus_foci.py", "dapi_adjust_windows")], group="DAPI 调整"),
            field("dapi_adjust_contrast", "DAPI 对比度", 1.0, "float", "数值范围：> 0 的有限数；1 保持不变，常用建议 0.1–10。", [("nucleus_foci.py", "dapi_adjust_contrast")], group="DAPI 调整"),
            field("dapi_adjust_brightness", "DAPI 亮度", 0.0, "float", "数值范围：任意有限数；0 保持不变，8-bit 图像常用建议 −255–255。", [("nucleus_foci.py", "dapi_adjust_brightness")], group="DAPI 调整"),
            *sum(([field(f"channel{i}_adjust_window", f"通道 {i} 窗口", [0, 255], "pair_int", "整数范围：0 ≤ 下限 < 上限 ≤ 255。", [("nucleus_foci.py", f"channel{i}_adjust_window")], group="蛋白通道调整"),
                   field(f"channel{i}_adjust_contrast", f"通道 {i} 对比度", 10.0, "float", "数值范围：> 0 的有限数；1 保持不变，常用建议 0.1–10。", [("nucleus_foci.py", f"channel{i}_adjust_contrast")], group="蛋白通道调整"),
                   field(f"channel{i}_adjust_brightness", f"通道 {i} 亮度", 0.0, "float", "数值范围：任意有限数；0 保持不变，8-bit 图像常用建议 −255–255。", [("nucleus_foci.py", f"channel{i}_adjust_brightness")], group="蛋白通道调整")]
                  for i in range(1, 6)), []),
            field("plot_order", "作图分组与顺序", {}, "plot_groups", "默认按汇总 CSV 的列顺序绘制全部组。可拖动卡片排序或排除。", targets=[("foci_plot.py", "plot_order")], group="作图参数"),
            field("group_colors", "配色", "nature", kind="colors", help_text="三种方式：直接填写 nature、Matplotlib 渐变色或 N 个十六进制颜色；从带色带的预设列表单击选择；或用颜色板按第 1、2…组依次添加颜色。", targets=[("foci_plot.py", "group_colors")], group="作图参数"),
            field("wilcox_pairs", "Wilcoxon 比较", "all", "plot_pairs", "可选择全部组两两比较、不进行统计检验，或按组名手动选择。", targets=[("foci_plot.py", "wilcox_pairs")], group="作图参数"),
        ], stages=[("nucleus_foci.py", "图像统计", "run_step", "analysis"), ("foci_plot.py", "统计作图", "run_step", "plot")],
    ),
    dict(
        id="intensity", title="核内相对荧光强度", icon="03", warning=True,
        description="分割细胞核，计算各蛋白通道的核内相对荧光强度，并生成分组图与统计结果。",
        fields=[
            field("input_dir", "TIFF 母文件夹", "", "folder", "选择导出工具生成的 *_tif 文件夹，内含各原图文件对应的子文件夹。", [("nuclear_intensity.py", "input_dir"), ("intensity_plot.py", "input_dir")], group="输入"),
            field("run_step", "本次运行步骤", "analysis", "choice", "二选一：图像统计生成 CSV/标注图；统计作图只读取已有 CSV，不重新分析图片。", choices=[("analysis", "仅运行图像统计"), ("plot", "仅生成统计图")], group="步骤"),
            field("foci_mode", "处理范围", "all", "choice", "可选范围：test 或 all；test 只处理首个子文件夹的首张合并图，all 处理全部。", [("nuclear_intensity.py", "foci_mode")], [("test", "测试一张"), ("all", "全部处理")], "统计参数"),
            field("nucleus_area_px", "细胞核面积 (px²)", [500, 20000], "pair_int", "整数范围：1 ≤ 下限 ≤ 上限；仅保留面积处于闭区间内的细胞核。", [("nuclear_intensity.py", "nucleus_area_px")], group="统计参数"),
            field("dapi_adjust_windows", "DAPI 拉伸窗口", "full", "window", "可填 full、none，或整数 low,high；数值范围：0 ≤ low < high ≤ 255。", [("nuclear_intensity.py", "dapi_adjust_windows")], group="DAPI 调整"),
            field("dapi_adjust_contrast", "DAPI 对比度", 1.0, "float", "数值范围：> 0 的有限数；1 保持不变，常用建议 0.1–10。", [("nuclear_intensity.py", "dapi_adjust_contrast")], group="DAPI 调整"),
            field("dapi_adjust_brightness", "DAPI 亮度", 0.0, "float", "数值范围：任意有限数；0 保持不变，8-bit 图像常用建议 −255–255。", [("nuclear_intensity.py", "dapi_adjust_brightness")], group="DAPI 调整"),
            *[field(f"channel{i}_threshold", f"通道 {i} 阈值", default, "pair_int", "整数范围：0 ≤ 下限 ≤ 上限 ≤ 255；仅累计此闭区间内的调整后灰度。", [("nuclear_intensity.py", f"channel{i}_threshold")], group="蛋白通道阈值") for i, default in enumerate(([125, 255], [60, 255], [0, 255], [0, 255], [0, 255]), 1)],
            field("plot_order", "作图分组与顺序", {}, "plot_groups", "默认按汇总 CSV 的列顺序绘制全部组。可拖动卡片排序或排除。", targets=[("intensity_plot.py", "plot_order")], group="作图参数"),
            field("group_colors", "配色", "nature", kind="colors", help_text="三种方式：直接填写 nature、Matplotlib 渐变色或 N 个十六进制颜色；从带色带的预设列表单击选择；或用颜色板按第 1、2…组依次添加颜色。", targets=[("intensity_plot.py", "group_colors")], group="作图参数"),
            field("wilcox_pairs", "Wilcoxon 比较", "all", "plot_pairs", "可选择全部组两两比较、不进行统计检验，或按组名手动选择。", targets=[("intensity_plot.py", "wilcox_pairs")], group="作图参数"),
        ], stages=[("nuclear_intensity.py", "图像统计", "run_step", "analysis"), ("intensity_plot.py", "统计作图", "run_step", "plot")],
    ),
    dict(
        id="fiber", title="DNA Fiber", icon="04", warning=True,
        description="识别 DNA fiber 的红/绿片段，输出逐条纤维结果，并绘制复制叉速度和长度比。",
        fields=[
            field("input_dir", "图片母文件夹", "", "folder", "选择图片母文件夹；待处理的单张 RGB TIFF 应放在其各个分组子文件夹中。", [("DNA_fiber.py", "parent_folder"), ("fiber_plot.py", "input_dir")], group="输入"),
            field("run_step", "本次运行步骤", "analysis", "choice", "二选一：图像统计生成逐条纤维 CSV/标注图；统计作图只读取已有 CSV。", choices=[("analysis", "仅运行图像统计"), ("plot", "仅生成统计图")], group="步骤"),
            field("fiber_mode", "处理范围", "test", "choice", "可选范围：test 或 all；test 只处理首个子文件夹的首张图，all 处理全部。", [("DNA_fiber.py", "fiber_mode")], [("test", "测试一张"), ("all", "全部处理")], "统计参数"),
            field("green_contrast", "绿色对比度", 1.0, "float", "数值范围：≥ 0 的有限数；1 保持不变，常用建议 0–10。", [("DNA_fiber.py", "green_contrast")], group="统计参数"),
            field("green_brightness", "绿色亮度", 50.0, "float", "数值范围：任意有限数；8-bit 图像常用建议 −255–255。", [("DNA_fiber.py", "green_brightness")], group="统计参数"),
            field("green_threshold", "绿色阈值", 80.0, "float", "数值范围：0–255（含端点）；数值越大，保留的绿色信号越强。", [("DNA_fiber.py", "green_threshold")], group="统计参数"),
            field("red_contrast", "红色对比度", 1.0, "float", "数值范围：≥ 0 的有限数；1 保持不变，常用建议 0–10。", [("DNA_fiber.py", "red_contrast")], group="统计参数"),
            field("red_brightness", "红色亮度", 20.0, "float", "数值范围：任意有限数；8-bit 图像常用建议 −255–255。", [("DNA_fiber.py", "red_brightness")], group="统计参数"),
            field("red_threshold", "红色阈值", 80.0, "float", "数值范围：0–255（含端点）；数值越大，保留的红色信号越强。", [("DNA_fiber.py", "red_threshold")], group="统计参数"),
            field("analysis_mode", "作图内容", 3, "choice", "可选值：1 仅复制叉速度、2 仅长度比、3 两者都生成。", [("fiber_plot.py", "analysis_mode")], [(1, "复制叉速度"), (2, "长度比"), (3, "两者")], "作图参数"),
            field("IF_order", "标记顺序", "red_green", "choice", "可选 red_green 或 green_red；必须与两次核苷类似物的实际掺入先后顺序一致。", [("fiber_plot.py", "IF_order")], [("red_green", "红 → 绿"), ("green_red", "绿 → 红")], "作图参数"),
            field("scale", "像素/μm", 1.0, "float", "数值范围：> 0 的有限数；填写显微图像标定的每微米像素数。", [("fiber_plot.py", "scale")], group="作图参数"),
            field("time", "后标记时间 (min)", 15.0, "float", "数值范围：> 0 的有限数；单位为分钟。", [("fiber_plot.py", "time")], group="作图参数"),
            field("plot_order", "作图分组与顺序", {}, "plot_groups", "默认按汇总 CSV 的列顺序绘制全部组。可拖动卡片排序或排除。", targets=[("fiber_plot.py", "plot_order")], group="作图参数"),
            field("group_colors", "配色", "nature", kind="colors", help_text="三种方式：直接填写 nature、Matplotlib 渐变色或 N 个十六进制颜色；从带色带的预设列表单击选择；或用颜色板按第 1、2…组依次添加颜色。", targets=[("fiber_plot.py", "group_colors")], group="作图参数"),
            field("wilcox_pairs", "Wilcoxon 比较", "all", "plot_pairs", "可选择全部组两两比较、不进行统计检验，或按组名手动选择。", targets=[("fiber_plot.py", "wilcox_pairs")], group="作图参数"),
        ], stages=[("DNA_fiber.py", "图像统计", "run_step", "analysis"), ("fiber_plot.py", "统计作图", "run_step", "plot")],
    ),
    dict(
        id="colony", title="单克隆形成", icon="05", warning=True,
        description="自动识别培养皿或六孔板 ROI，分割并统计克隆，输出标注图和 CSV。",
        fields=[
            field("input_dir", "图片母文件夹", "", "folder", "选择图片母文件夹；待统计图片应放在其分组子文件夹中，支持 JPG、PNG 和单页 TIFF。", [("colony_formation.py", "parent_dir")], group="输入"),
            field("mode", "处理范围", "test", "choice", "可选范围：test 或 all；test 只处理首个子文件夹的首张图，all 处理全部。", [("colony_formation.py", "mode")], [("test", "测试一张"), ("all", "全部处理")], "统计参数"),
            field("area_range", "克隆面积 (px²)", [10, 12000], "pair_int", "整数范围：1 ≤ 下限 ≤ 上限；按基准 ROI 像素面积筛选克隆。", [("colony_formation.py", "area_range")], group="统计参数"),
            field("hough_votes", "圆检测票数", 110, "int", "正整数范围：≥ 1；越大越严格，漏孔时可尝试 70–100，误检时增大。", [("colony_formation.py", "hough_votes")], group="ROI 参数"),
            field("threshold_factor", "Otsu 阈值倍数", 1.0, "float", "数值范围：> 0 的有限数；小于 1 更易保留浅染色，大于 1 更严格。", [("colony_formation.py", "threshold_factor")], group="分割参数"),
            field("min_contrast", "最低灰度差", 8.0, "float", "数值范围：≥ 0 的有限数，8-bit 图像有效建议 0–255；用于抑制空白孔误计数。", [("colony_formation.py", "min_contrast")], group="分割参数"),
            field("peak_prominence", "距离峰突出度", 3.0, "float", "数值范围：> 0 的有限数；增大可减少相邻克隆的过度分割。", [("colony_formation.py", "peak_prominence")], group="分割参数"),
        ], stages=[("colony_formation.py", "克隆统计")],
    ),
]

DDRIMAGE_CARD_PALETTES = {
    "lif": ("#FAEDDB", "#E8D9C5", "#AD7747"),
    "foci": ("#EDE5F8", "#DDD1EB", "#8263A2"),
    "intensity": ("#DFF1EE", "#CDE4E0", "#4D8278"),
    "fiber": ("#E7EEF9", "#D3DEED", "#567BA0"),
    "colony": ("#F8E4E3", "#EBD1D1", "#AB6567"),
}


class PageWidget(QWidget):
    def __init__(self, spec, saved):
        super().__init__()
        self.spec = spec
        self.widgets = {}
        self.groups = {}
        self.mode = "analysis"
        self.paths = {"analysis": saved.get("input_dir", ""), "plot": saved.get("plot_input_dir", "")}
        if saved.get("run_step") == "plot" and "plot_input_dir" not in saved:
            self.paths = {"analysis": "", "plot": saved.get("input_dir", "")}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 8)
        title = QLabel(spec["title"])
        title.setObjectName("PageTitle")
        desc = QLabel(spec["description"])
        desc.setObjectName("description")
        desc.setWordWrap(True)
        outer.addWidget(title)
        outer.addWidget(desc)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 6, 14, 8)
        body_layout.setSpacing(8)
        for item in spec["fields"]:
            group_name = item["group"]
            if group_name not in self.groups:
                card = QFrame()
                card.setObjectName("Card")
                card.setMaximumWidth(730)
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(12, 8, 12, 8)
                card_layout.setSpacing(4)
                if group_name not in {"输入", "步骤"}:
                    heading = QLabel(group_name)
                    heading.setStyleSheet("font-weight:700; background:transparent;")
                    card_layout.addWidget(heading)
                form = QFormLayout()
                form.setLabelAlignment(Qt.AlignLeft)
                form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
                form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
                form.setHorizontalSpacing(14)
                form.setVerticalSpacing(6)
                card_layout.addLayout(form)
                body_layout.addWidget(card)
                self.groups[group_name] = (card, form)
            key = item["key"]
            value = saved.get(key, item["default"])
            if key == "run_step":
                value = "analysis"
            elif key == "input_dir":
                value = self.paths["analysis"]
            widget = self._create_widget(item, value)
            self.widgets[item["key"]] = widget
            label = QLabel(item["label"])
            label.setToolTip(item["help"])
            target = getattr(widget, "value_widget", widget)
            label.setBuddy(target)
            target.setAccessibleName(item["label"])
            target.setToolTip(item["help"])
            self.groups[group_name][1].addRow(label, widget)
            if item["kind"] == "folder":
                self.input_label = label
                self.source_label = item["label"]
        body_layout.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        if "run_step" in self.widgets:
            self.widgets["run_step"].currentIndexChanged.connect(self.change_mode)
            self.update_mode()
        if "plot_order" in self.widgets:
            self.widgets["input_dir"].value_widget.textChanged.connect(self.update_plot_groups)
        if "oir_groups" in self.widgets:
            button = self.widgets["oir_groups"]
            self.widgets["input_dir"].value_widget.textChanged.connect(button.refresh)
            self.widgets["source_format"].currentIndexChanged.connect(self.update_oir_grouping)
            self.update_oir_grouping()

    @property
    def stage(self):
        return next(stage for stage in self.spec["stages"] if len(stage) == 2 or stage[3] == self.mode)

    def change_mode(self):
        selector = self.widgets["run_step"]
        requested = selector.currentData()
        if requested == self.mode:
            return
        if requested == "plot":
            title = "切换到仅生成统计图"
            message = "作图将读取已有的统计 CSV。请选择图像统计的结果目录。是否切换？"
        else:
            title = "切换到仅运行图像统计"
            message = "重新统计成功后，同一输入目录上次生成的统计结果会被替换。需要保留时，请提前备份。是否切换？"
        answer = QMessageBox.question(self, title, message,
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            with QSignalBlocker(selector):
                selector.setCurrentIndex(selector.findData(self.mode))
            return
        path_field = self.widgets["input_dir"].value_widget
        self.mode = requested
        path_field.setText(self.paths[self.mode])
        self.update_mode()

    def update_mode(self):
        plot_mode = self.mode == "plot"
        for name, (card, _) in self.groups.items():
            card.setVisible(name in {"输入", "步骤"} or (name == "作图参数") == plot_mode)
        label = "统计结果目录" if plot_mode else self.source_label
        self.input_label.setText(label)
        self.widgets["input_dir"].value_widget.setAccessibleName(label)
        self.update_plot_groups()

    def update_plot_groups(self):
        if self.mode == "plot" and "plot_order" in self.widgets:
            self.widgets["plot_order"].refresh()

    def update_oir_grouping(self):
        self.groups["参数"][1].setRowVisible(
            self.widgets["oir_groups"], self.widgets["source_format"].currentData() == "olympus")

    def _create_widget(self, item, value):
        kind = item["kind"]
        if kind == "check":
            w = QCheckBox()
            w.setChecked(bool(value))
        elif kind == "choice":
            w = NoWheelComboBox()
            for actual, label in item["choices"]:
                w.addItem(label, actual)
            idx = w.findData(value)
            w.setCurrentIndex(max(0, idx))
            w.setMaximumWidth(245)
        elif kind == "colors":
            w = self._create_color_widget(item, value)
        elif kind == "plot_groups":
            w = PlotGroupField(self.spec["id"], self.widgets["input_dir"].value_widget.text, value)
        elif kind == "plot_pairs":
            w = PlotComparisonField(self.widgets["plot_order"], value)
        elif kind == "oir_groups":
            w = OirGroupField(self.widgets["input_dir"].value_widget.text, self.widgets["recursive"].isChecked, value)
        elif kind == "folder":
            container = QWidget()
            layout = QHBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            edit = FolderEdit(str(value))
            edit.setAcceptDrops(True)
            edit.setPlaceholderText("拖入文件夹，或点击浏览")
            edit.setAccessibleName(item["label"])
            button = QPushButton("浏览…")
            button.clicked.connect(lambda: self._browse(edit))
            layout.addWidget(edit, 1)
            layout.addWidget(button)
            container.setMaximumWidth(510)
            container.value_widget = edit
            w = container
        elif kind in ("pair_int", "pair_float"):
            container = QWidget()
            layout = QHBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            values = value if isinstance(value, (list, tuple)) and len(value) == 2 else ["", ""]
            low, high = QLineEdit(str(values[0])), QLineEdit(str(values[1]))
            low.setPlaceholderText("下限")
            high.setPlaceholderText("上限")
            low.setAccessibleName(item["label"] + "下限")
            high.setAccessibleName(item["label"] + "上限")
            layout.addWidget(low)
            layout.addWidget(QLabel("—"))
            layout.addWidget(high)
            container.setMaximumWidth(190)
            container.value_widgets = (low, high)
            w = container
        else:
            w = QLineEdit(str(value))
            w.setMaximumWidth(122 if kind in {"int", "float"} else 265)
        w.setToolTip(item["help"])
        return w

    def _browse(self, edit):
        start = edit.text().strip() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "选择文件夹", start)
        if selected:
            edit.setText(selected)

    def _create_color_widget(self, item, value):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        direct_row = QHBoxLayout()
        direct_label = QLabel("直接填写")
        direct_label.setObjectName("parameterOption")
        edit = QLineEdit(str(value))
        edit.setPlaceholderText("nature、渐变色名，或 #RRGGBB,#RRGGBB…")
        direct_row.addWidget(direct_label)
        direct_row.addWidget(edit, 1)
        layout.addLayout(direct_row)

        select_row = QHBoxLayout()
        select_label = QLabel("预设色带")
        select_label.setObjectName("parameterOption")
        preset = NoWheelComboBox()
        preset.setIconSize(QSize(88, 14))
        preset.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        preset.setMinimumContentsLength(11)
        preset.addItem("选择色带…", None)
        for name, icon in colorbar_icons().items():
            preset.addItem(icon, name, name)
        palette_button = QPushButton()
        select_row.addWidget(select_label)
        select_row.addWidget(preset, 1)
        select_row.addWidget(palette_button)
        layout.addLayout(select_row)

        container.value_widget = edit
        container.preset_widget = preset
        container.palette_button = palette_button
        container.help_text = item["help"]
        container.setMaximumWidth(480)
        preset.activated.connect(lambda index: self._use_color_preset(container, index))
        palette_button.clicked.connect(lambda: self._choose_palette_color(container))
        edit.textChanged.connect(lambda: self._update_color_widget(container))
        self._update_color_widget(container)
        return container

    @staticmethod
    def _hex_colors(text):
        values = [part.strip().upper() for part in text.split(",") if part.strip()]
        return values if values and all(HEX_COLOR.fullmatch(value) for value in values) else []

    def _use_color_preset(self, container, index):
        value = container.preset_widget.itemData(index)
        if value:
            container.value_widget.setText(str(value))

    def _update_color_widget(self, container):
        text = container.value_widget.text().strip()
        colors = self._hex_colors(text)
        edit = container.value_widget
        if colors:
            if len(colors) == 1:
                background = colors[0]
            else:
                stops = ", ".join(
                    f"stop:{index / (len(colors) - 1):.3f} {color}"
                    for index, color in enumerate(colors))
                background = f"qlineargradient(x1:0, y1:0, x2:1, y2:0, {stops})"
            average = sum(QColor(color).lightness() for color in colors) / len(colors)
            foreground = "#FFFFFF" if average < 128 else "#172522"
            edit.setStyleSheet(
                f"QLineEdit {{ background: {background}; color: {foreground}; "
                "border: 1px solid #82979b; border-radius: 6px; padding: 7px; }}")
        else:
            edit.setStyleSheet("")

        preset_index = container.preset_widget.findData(text)
        container.preset_widget.blockSignals(True)
        container.preset_widget.setCurrentIndex(max(0, preset_index))
        container.preset_widget.blockSignals(False)
        container.palette_button.setText(f"颜色板：选择第 {len(colors) + 1} 组…")

    def _choose_palette_color(self, container):
        current = getattr(container, "palette_dialog", None)
        if current is not None:
            if current.isMinimized():
                current.showNormal()
            current.raise_()
            current.activateWindow()
            return
        existing = self._hex_colors(container.value_widget.text().strip())
        dialog = QColorDialog()
        container.palette_dialog = dialog
        container.destroyed.connect(dialog.close)
        dialog.setWindowTitle(f"选择第 {len(existing) + 1} 组颜色")
        dialog.setOption(QColorDialog.DontUseNativeDialog, True)
        dialog.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowMinMaxButtonsHint
                              | Qt.WindowType.WindowCloseButtonHint)
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.layout().setSizeConstraint(QLayout.SizeConstraint.SetDefaultConstraint)
        dialog.setMaximumSize(16777215, 16777215)
        dialog.setSizeGripEnabled(True)
        for index, value in enumerate(PRISM_COLORS):
            dialog.setCustomColor(index, QColor(value))
        dialog.setCurrentColor(QColor(existing[-1] if existing else PRISM_COLORS[len(existing) % len(PRISM_COLORS)]))

        def add_color(color):
            existing = self._hex_colors(container.value_widget.text().strip())
            existing.append(color.name(QColor.HexRgb).upper())
            container.value_widget.setText(",".join(existing))

        dialog.colorSelected.connect(add_color)
        dialog.finished.connect(lambda _: setattr(container, "palette_dialog", None))
        dialog.show()

    def raw_value(self, item):
        w = self.widgets[item["key"]]
        if item["kind"] == "check":
            return w.isChecked()
        if item["kind"] == "choice":
            return w.currentData()
        if item["kind"] == "plot_groups":
            return w.saved
        if item["kind"] == "plot_pairs":
            return w.settings()
        if item["kind"] == "oir_groups":
            return w.saved
        if item["kind"] in {"folder", "colors"}:
            return w.value_widget.text().strip()
        if item["kind"] in ("pair_int", "pair_float"):
            return [x.text().strip() for x in w.value_widgets]
        return w.text().strip()

    def parsed_values(self):
        values = {}
        script = self.stage[0]
        for item in self.spec["fields"]:
            if not any(owner == script for owner, _ in item["targets"]):
                continue
            raw = self.raw_value(item)
            kind = item["kind"]
            try:
                if kind == "folder":
                    if not raw:
                        raise ValueError("尚未选择文件夹")
                    path = Path(raw).expanduser().resolve()
                    if not path.is_dir():
                        raise ValueError("文件夹不存在")
                    value = {"__path__": str(path)}
                elif kind == "int":
                    value = int(raw)
                elif kind in {"plot_groups", "plot_pairs"}:
                    value = self.widgets[item["key"]].value()
                elif kind == "oir_groups":
                    value = self.widgets[item["key"]].value() if self.widgets["source_format"].currentData() == "olympus" else None
                elif kind == "float":
                    value = float(raw)
                    if not math.isfinite(value):
                        raise ValueError("必须是有限数值")
                elif kind == "pair_int":
                    value = {"__tuple__": [int(raw[0]), int(raw[1])]}
                elif kind == "pair_float":
                    value = {"__tuple__": [float(raw[0]), float(raw[1])]}
                elif kind == "window":
                    text = raw.strip().lower()
                    if text == "full":
                        value = "full"
                    elif text in ("none", "无", ""):
                        value = None
                    else:
                        parts = [int(x.strip()) for x in text.split(",")]
                        if len(parts) != 2:
                            raise ValueError("请填写 full、none 或 low,high")
                        if not 0 <= parts[0] < parts[1] <= 255:
                            raise ValueError("数值窗口必须满足 0 ≤ low < high ≤ 255")
                        value = {"__tuple__": parts}
                else:
                    value = raw
                self._validate_range(item, value)
            except ValueError as exc:
                raise ValueError(f"{item['label']}：{exc}") from exc
            values[item["key"]] = value
        return values

    @staticmethod
    def _validate_range(item, value):
        key = item["key"]
        if item["kind"] in {"pair_int", "pair_float"}:
            low, high = value["__tuple__"]
            if key in {"nucleus_area_px", "foci_area_px", "area_range"} and not 1 <= low <= high:
                raise ValueError("必须满足 1 ≤ 下限 ≤ 上限")
            if key.endswith("_adjust_window") and not 0 <= low < high <= 255:
                raise ValueError("必须满足 0 ≤ 下限 < 上限 ≤ 255")
            if key.endswith("_threshold") and not 0 <= low <= high <= 255:
                raise ValueError("必须满足 0 ≤ 下限 ≤ 上限 ≤ 255")
        if key == "hough_votes" and value < 1:
            raise ValueError("必须是 ≥ 1 的正整数")
        if key in {"dapi_adjust_contrast", "scale", "time", "threshold_factor", "peak_prominence"} and value <= 0:
            raise ValueError("必须是 > 0 的有限数")
        if key.endswith("_adjust_contrast") and value <= 0:
            raise ValueError("必须是 > 0 的有限数")
        if key in {"green_contrast", "red_contrast", "min_contrast"} and value < 0:
            raise ValueError("必须是 ≥ 0 的有限数")
        if key in {"green_threshold", "red_threshold"} and not 0 <= value <= 255:
            raise ValueError("必须在 0–255 范围内")

    def serializable_values(self):
        saved = {item["key"]: self.raw_value(item) for item in self.spec["fields"] if item["key"] != "run_step"}
        saved["input_dir"] = self.paths["analysis"]
        saved["plot_input_dir"] = self.paths["plot"]
        return saved



class DDRimagePage(QWidget):
    def __init__(self):
        super().__init__()
        self.workflows = {}
        layout = QVBoxLayout(self)
        title = QLabel("DDRimage")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        intro = QLabel("显微图像导出与定量分析 · 在本机运行")
        layout.addWidget(intro)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.board = CardBoard(columns=4)
        self.board.scroll_area = scroll
        for spec in PAGES:
            identifier = spec["id"]
            self.board.add_card(identifier, spec["title"], spec["description"],
                                partial(self.open_workflow, spec),
                                palette=DDRIMAGE_CARD_PALETTES[identifier])
        scroll.setWidget(self.board)
        layout.addWidget(scroll, 1)

    def open_workflow(self, spec, _identifier=None):
        key = spec["id"]
        dialog = self.workflows.get(key)
        if dialog is None:
            dialog = WorkflowDialog(spec)
            self.workflows[key] = dialog
            dialog.finished.connect(lambda _: self.workflows.pop(key, None))
        if dialog.isMinimized():
            dialog.showNormal()
        else:
            dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return dialog

    def close_workflows(self):
        return all(dialog.close() for dialog in tuple(self.workflows.values()))


class WorkflowDialog(QDialog):
    def __init__(self, spec):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowMinMaxButtonsHint
                            | Qt.WindowType.WindowCloseButtonHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"{spec['title']} · DDRimage")
        self.resize(865, 720)
        self.setAcceptDrops(True)
        self.settings = QSettings()
        self.settings_key = "ddrimage/" + spec["id"]
        try:
            saved = json.loads(self.settings.value(self.settings_key, "{}"))
            if not isinstance(saved, dict):
                saved = {}
        except (ValueError, TypeError):
            saved = {}
        self.page = PageWidget(spec, saved)
        self.input = self.page.widgets["input_dir"].value_widget
        self.input.setToolTip("拖入一个本地文件夹。双击已有路径可打开文件夹。")
        self.process = None
        self.session = None
        self.review_dialog = None
        self.backup_id = uuid4().hex
        self.backup_roots = set()
        self.result = None
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.read_output)
        self.status = QLabel("准备就绪")
        self.run_button = QPushButton("开始运行")
        self.run_button.clicked.connect(self.start_run)
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_run)
        self.open_button = QPushButton("打开结果文件夹")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_output)
        self.review_button = QPushButton("矫正图像统计…")
        self.review_button.setVisible(spec["id"] in REVIEW_WORKFLOWS)
        self.review_button.setToolTip("全部处理成功后，可核对和矫正已识别的细胞核或配对。")
        self.review_button.clicked.connect(self.review_images)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("在这里查看运行进度、结果和错误信息。")
        self.log.document().setMaximumBlockCount(3000)
        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.addWidget(self.status)
        controls = QGridLayout()
        controls.addWidget(self.stop_button, 0, 0)
        controls.addWidget(self.run_button, 0, 1)
        controls.addWidget(self.review_button, 1, 0)
        controls.addWidget(self.open_button, 1, 1)
        bottom_layout.addLayout(controls)
        bottom_layout.addWidget(self.log, 1)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self.page)
        splitter.addWidget(bottom)
        splitter.setSizes([520, 180])
        self.content = QSplitter(Qt.Orientation.Horizontal)
        self.content.setChildrenCollapsible(False)
        self.content.setHandleWidth(8)
        self.content.addWidget(splitter)
        self.content.splitterMoved.connect(self.update_preview_width)
        self.controls_width = 400
        self.preview = None
        layout = QVBoxLayout(self)
        layout.addWidget(self.content)
        self.input.textChanged.connect(self.remember_input_path)
        self.input.textChanged.connect(self.update_review)
        if "run_step" in self.page.widgets:
            self.page.widgets["run_step"].currentIndexChanged.connect(self.update_review)
        if spec["id"] == "colony":
            self.page.widgets["mode"].currentIndexChanged.connect(self.update_review)
        self.update_review()

    def remember_input_path(self, text):
        path = text.strip()
        try:
            valid = path and Path(path).expanduser().is_dir()
        except (OSError, ValueError):
            return
        if valid and self.page.paths[self.page.mode] != path:
            self.page.paths[self.page.mode] = path
            self.save_settings()
            self.settings.sync()

    def review_target(self):
        raw = self.input.text().strip()
        workflow = self.page.spec["id"]
        if (self.process is not None or self.review_dialog is not None or self.page.mode != "analysis"
                or workflow not in REVIEW_WORKFLOWS or not raw):
            return None
        try:
            source = Path(raw).expanduser().resolve()
            targets = [metadata_root(source, workflow), previous_statistics_destination(source, workflow),
                       legacy_statistics_destination(source, workflow)]
            if workflow == "colony":
                targets = [metadata_root(source, workflow, test=self.page.widgets["mode"].currentData() == "test"),
                           metadata_root(source, workflow, test=self.page.widgets["mode"].currentData() != "test")]
            return next((result for result in targets if (result / REVIEW_INDEX).is_file()), None)
        except (OSError, ValueError):
            return None

    def update_review(self):
        self.review_button.setEnabled(self.review_target() is not None)

    def review_images(self):
        result = self.review_target()
        if result is None:
            return
        self.enable_image_dependencies()
        try:
            if self.page.spec["id"] == "colony":
                from ddrimage_colony_ui import ColonyReviewDialog
                dialog = ColonyReviewDialog(result, self)
            else:
                from ddrimage_review_ui import ReviewDialog
                dialog = ReviewDialog(result, self)
        except (OSError, ValueError, TypeError, KeyError) as error:
            QMessageBox.warning(self, "无法打开矫正窗口", str(error))
            return
        self.review_dialog = dialog
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.finished.connect(self.review_finished)
        self.update_review()
        dialog.open()

    def review_finished(self, result):
        dialog, self.review_dialog = self.review_dialog, None
        if result == QDialog.DialogCode.Accepted and dialog is not None and isValid(dialog):
            self.start_job({"correction": dialog.request()})
        else:
            self.update_review()

    @staticmethod
    def enable_image_dependencies():
        packages = str(PAYLOAD / "packages")
        if getattr(sys, "frozen", False) and packages not in sys.path:
            sys.dont_write_bytecode = True
            sys.path.append(packages)

    def dragEnterEvent(self, event):
        if self.process is None and dropped_folder(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        path = dropped_folder(event)
        if self.process is None and path:
            self.input.setText(path)
            event.acceptProposedAction()
        else:
            event.ignore()

    def start_run(self):
        if self.process is not None:
            return
        try:
            values = self.page.parsed_values()
            script, label, *_ = self.page.stage
            overrides = {
                target: values[item["key"]]
                for item in self.page.spec["fields"]
                for owner, target in item["targets"]
                if owner == script
            }
            input_key, workflow = WORKFLOWS[script]
            source = Path(overrides[input_key]["__path__"])
            test = overrides.get(REVIEW_WORKFLOWS.get(workflow, "mode")) == "test"
            if workflow not in {"", "tif"} and (metadata_root(source, workflow, test=test) / RESULT_MARKER).exists():
                answer = QMessageBox.question(
                    self, "重新运行图像统计",
                    "本次成功后将替换上次生成的统计结果。需要保留时，请先备份结果目录。是否继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            self.save_settings()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "无法开始", str(error))
            return
        job = {"script": script, "label": label, "overrides": overrides}
        self.start_job({"jobs": [job]})

    def start_job(self, payload):
        try:
            if self.session:
                self.session.cleanup()
            self.session = TemporaryDirectory(prefix="biomed-ddrimage-")
            self.config = Path(self.session.name) / "job.json"
            raw = self.input.text().strip()
            if raw:
                source = Path(raw).expanduser().resolve()
                self.backup_roots.add(plot_output_root(source, self.page.spec["id"]) if self.page.mode == "plot" else source)
            payload["backup_id"] = self.backup_id
            self.config.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "无法开始", str(error))
            return
        self.offset = 0
        self.pending = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.stopped = False
        self.result = None
        if self.preview is not None:
            self.content.setCollapsible(0, False)
            if self.content.sizes()[0] == 0:
                self.toggle_preview_width()
            self.preview.hide()
            self.preview.setParent(None)
            self.preview.deleteLater()
            self.preview = None
        self.open_button.setEnabled(False)
        self.page.setEnabled(False)
        self.log.clear()
        self.status.setText("正在运行…")
        self.run_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        process = self.process = QProcess(self)
        self.update_review()
        arguments = ["--ddrimage-worker", str(self.config)]
        if not getattr(sys, "frozen", False):
            arguments.insert(0, str(APP_DIR / "main.py"))
        process.setProgram(sys.executable)
        process.setArguments(arguments)
        process.finished.connect(self.worker_finished)
        process.errorOccurred.connect(self.process_error)
        self.timer.start()
        process.start()

    def read_output(self, final=False):
        path = self.config.with_suffix(".log")
        if not path.is_file():
            return
        try:
            with path.open("rb") as stream:
                stream.seek(self.offset)
                data = stream.read()
                self.offset = stream.tell()
        except OSError:
            return
        lines = (self.pending + self.decoder.decode(data, final=final)).split("\n")
        self.pending = lines.pop()
        if final and self.pending:
            lines.append(self.pending)
            self.pending = ""
        for line in lines:
            self.append_log(line.rstrip("\r"))

    def append_log(self, line):
        cursor = self.log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(line)
        cursor.insertBlock()
        self.log.setTextCursor(cursor)
        self.log.ensureCursorVisible()

    def worker_finished(self, exit_code, exit_status):
        self.timer.stop()
        self.read_output(final=True)
        status_file = self.config.with_suffix(".result.json")
        try:
            folder = Path(json.loads(status_file.read_text(encoding="utf-8")))
            if folder.is_dir():
                self.result = folder
        except (OSError, ValueError, TypeError):
            pass
        ok = exit_status == QProcess.ExitStatus.NormalExit and exit_code == 0
        if ok and self.result is not None:
            preview_file = self.config.with_suffix(".preview.json")
            if preview_file.is_file():
                self.show_test_preview(preview_file)
            if self.page.mode == "analysis" and "run_step" in self.page.widgets:
                source = Path(self.input.text()).expanduser().resolve()
                legacy = legacy_statistics_destination(source, self.page.spec["id"])
                previous = self.page.paths["plot"]
                old_results = [str(previous_statistics_destination(source, self.page.spec["id"], test=test))
                               for test in (False, True)]
                if not previous or previous in [str(legacy), *old_results]:
                    self.page.paths["plot"] = str(self.result)
                    self.save_settings()
        self.status.setText("已停止" if self.stopped else ("运行完成" if ok else "运行失败，请查看日志"))
        self.reset_controls()

    def show_test_preview(self, path):
        self.enable_image_dependencies()
        from ddrimage_preview import TestImagePreview

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.preview = TestImagePreview(self.result, data)
        except (ImportError, OSError, ValueError, KeyError, TypeError) as error:
            self.append_log(f"无法打开测试图像预览：{error}")
            return
        self.content.addWidget(self.preview)
        self.content.handle(1).setToolTip("拖动分隔线可调整参数区与预览区的宽度")
        self.content.setCollapsible(0, True)
        self.content.setStretchFactor(1, 1)
        self.preview.full_width.clicked.connect(self.toggle_preview_width)
        if not self.isMaximized() and not self.isMinimized() and self.width() < 1350:
            available = self.screen().availableGeometry().width()
            self.resize(min(1350, available - 32), self.height())
        self.content.setSizes([self.controls_width, max(1, self.content.width() - self.controls_width)])

    def update_preview_width(self):
        if self.preview is None:
            return
        controls, _ = self.content.sizes()
        if controls:
            self.controls_width = controls
        self.preview.full_width.setText("铺满窗口" if controls else "显示参数")

    def toggle_preview_width(self):
        controls, preview = self.content.sizes()
        total = controls + preview
        width = 0 if controls else self.controls_width
        self.content.setSizes([width, max(1, total - width)])
        self.update_preview_width()

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart and self.process is not None:
            self.timer.stop()
            self.append_log(self.process.errorString())
            self.status.setText("无法启动分析")
            self.reset_controls()

    def reset_controls(self):
        self.process.deleteLater()
        self.process = None
        self.page.setEnabled(True)
        self.run_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.open_button.setEnabled(self.result is not None)
        self.update_review()

    def stop_run(self):
        if self.process is not None:
            self.stopped = True
            self.process.kill()

    def open_output(self):
        if self.result and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.result))):
            QMessageBox.warning(self, "无法打开", str(self.result))

    def save_settings(self):
        options = json.dumps(self.page.serializable_values(), ensure_ascii=False)
        self.settings.setValue(self.settings_key, options)

    def done(self, result):
        if self.review_dialog is not None and isValid(self.review_dialog) and not self.review_dialog.close():
            return
        if self.process is not None:
            answer = QMessageBox.question(self, "停止分析", "分析仍在运行。停止并关闭窗口？",
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                          QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            worker = self.process
            self.stop_run()
            if not worker.waitForFinished(2000):
                return
        self.save_settings()
        if self.session:
            self.session.cleanup()
            self.session = None
        errors = []
        workflow = self.page.spec["id"]
        raw = self.page.paths["analysis"]
        if workflow in REVIEW_WORKFLOWS and raw:
            self.backup_roots.add(Path(raw).expanduser().resolve())
        for source in sorted(self.backup_roots):
            try:
                if workflow in REVIEW_WORKFLOWS and (metadata_root(source, workflow, test=True) / RESULT_MARKER).is_file():
                    with statistics_lock(source):
                        clear_test_outputs(source, workflow)
                clear_window_backups(source, self.backup_id)
            except (OSError, ValueError) as error:
                errors.append(str(error))
        if errors:
            QMessageBox.warning(self, "DDRimage 临时文件清理未完成", "\n".join(errors))
        super().done(result)
