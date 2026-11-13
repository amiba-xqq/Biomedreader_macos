"""Select and review a band ROI for each labeled WB/gel lane."""
from figure_status import FigureStatus
from PySide6.QtCore import QPointF,QRectF,QSignalBlocker,Qt,Signal
from PySide6.QtGui import QColor,QPainter,QPen,QPolygonF
from PySide6.QtWidgets import (QCheckBox,QDialog,QDialogButtonBox,QFormLayout,QHBoxLayout,QLabel,
                               QPushButton,QVBoxLayout)

from figure_ui import CropArea,QComboBox,viewport_tools
from wb_density import automatic_bands,background_regions,control_lane,image_signature,panel_density,roi_bounds


class BandArea(CropArea):
    traced = Signal(object)
    def __init__(self):
        super().__init__()
        self.selection_editable = False
        self.allow_resize = False
        self.boxes = []
        self.size_px = (1,1)
        self.active = 0
        self.manual = False
        self.trace = []

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and not self.hand_tool:
            if self.manual and self.area().contains(event.position()):
                self.trace = [self.pixel(event.position())]
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if self.trace:
            self.trace.append(self.pixel(event.position()))
            self.update()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        if self.trace and event.button()==Qt.MouseButton.LeftButton:
            trace,self.trace = self.trace,[]
            self.traced.emit(trace)
            self.update()
        else:
            super().mouseReleaseEvent(event)

    def paintEvent(self,event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setClipRect(self.rect())
        w,h = self.size_px
        for index,box in enumerate(self.boxes):
            if box is None:
                continue
            color = '#EF791A' if index==self.active else '#FFD35A'
            if isinstance(box,dict) and box.get('points'):
                painter.setPen(QPen(QColor(color),2))
                painter.drawPolygon(QPolygonF([self.point(QPointF(x/w,y/h)) for x,y in box['points']]))
                regions = []
            elif isinstance(box,dict) and box.get('outline'):
                painter.setPen(QPen(QColor(color),2))
                for x,y,xx,yy in box['outline']:
                    painter.drawLine(self.point(QPointF(x/w,y/h)),self.point(QPointF(xx/w,yy/h)))
                regions = []
            else:
                regions = [(roi_bounds(box),color)]
            if not (isinstance(box,dict) and box.get('empty')):
                regions.extend((region,'#339DB8') for region in background_regions(box,h))
            for (x,y,width,height),color in regions:
                painter.setPen(QPen(QColor(color),2))
                painter.drawRect(QRectF(self.point(QPointF(x/w,y/h)),self.point(QPointF((x+width)/w,(y+height)/h))))
        if self.trace:
            painter.setPen(QPen(QColor('#EF791A'),2))
            painter.drawPolyline(QPolygonF([self.point(point) for point in self.trace]))
        painter.end()


class DensityDialog(QDialog):
    def __init__(self,parent,panels,images,labels,previous):
        super().__init__(parent)
        self.setWindowTitle('WB／gel · 条带相对灰度')
        self.resize(880,640)
        self.panels,self.images,self.labels = panels,images,labels
        self.first_panel = panels[0]['uid']
        self.default_control = control_lane(self.first_panel,labels,previous)
        self.controls = {panel['uid']:previous['controls'][panel['uid']] for panel in panels[1:]
                         if type(previous.get('controls',{}).get(panel['uid'])) is int
                         and 0<=previous['controls'][panel['uid']]<len(labels)}
        self.lane_names = [f'{index+1} · {label}' for index,label in enumerate(labels)]
        self.result_settings = None
        self.signatures = [image_signature(image) for image in images]
        self.rectangles = []
        for panel,signature in zip(panels,self.signatures):
            old = previous.get('panels',{}).get(panel['uid'],{})
            if old.get('signature')==signature and old.get('labels')==labels:
                self.rectangles.append(list(old['rectangles']))
            else:
                self.rectangles.append([None]*len(labels))
        self.manual_rectangles = self.rectangles if previous.get('selection','manual')=='manual' else [
            [None]*len(labels) for _ in panels]
        self.automatic_rectangles = None
        self.automatic_polarity = None
        layout = QVBoxLayout(self)
        self.enable = QCheckBox('计算并展示条带相对灰度')
        self.enable.setChecked(previous.get('enabled',True))
        layout.addWidget(self.enable)
        explanation = QLabel('同名泳道按从左到右的编号区分。每条可独立选对照，默认沿用首条的设置。'
                             '手绘从自动选区开始，蓝色矩形为局部背景。自动选不到条带时，小矩形代表零信号。')
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        form = QFormLayout()
        self.panel = QComboBox()
        self.lane = QComboBox()
        self.control = QComboBox()
        for item in panels:
            self.panel.addItem(item['label'])
        for label in self.lane_names:
            self.lane.addItem(label)
        self.populate_controls()
        self.polarity = QComboBox()
        self.polarity.addItem('浅背景上的深色条带','dark')
        self.polarity.addItem('深背景上的亮色条带','light')
        self.polarity.setCurrentIndex(max(0,self.polarity.findData(previous.get('polarity','dark'))))
        self.display_mode = QComboBox()
        self.display_mode.addItem('在各泳道下方显示数值','below')
        self.display_mode.addItem('图右侧显示柱状图与圈选示意','side')
        self.display_mode.setCurrentIndex(max(0,self.display_mode.findData(previous.get('display','below'))))
        self.selection_mode = QComboBox()
        self.selection_mode.addItem('自动阈值选区','auto')
        self.selection_mode.addItem('手绘目标区域','manual')
        default = 'manual' if previous.get('panels') else 'auto'
        self.selection_mode.setCurrentIndex(max(0,self.selection_mode.findData(previous.get('selection',default))))
        form.addRow('选区方式',self.selection_mode)
        for caption,control in (('蛋白／核酸',self.panel),('对照泳道',self.control),('条带类型',self.polarity),
                                ('展示方式',self.display_mode),('圈选泳道',self.lane)):
            form.addRow(caption,control)
        layout.addLayout(form)
        self.area = BandArea()
        self.area.setMinimumHeight(160)
        self.area.traced.connect(self.record_trace)
        layout.addWidget(self.area,1)
        tools,_ = viewport_tools(self.area)
        clear = QPushButton('清除当前泳道框')
        clear.clicked.connect(self.clear_roi)
        tools.addWidget(clear)
        layout.addLayout(tools)
        self.status = FigureStatus()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.finish)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.panel.currentIndexChanged.connect(self.change_panel)
        self.lane.currentIndexChanged.connect(self.refresh_overlay)
        self.control.currentIndexChanged.connect(self.change_control)
        self.polarity.currentIndexChanged.connect(self.update_selection_mode)
        self.selection_mode.currentIndexChanged.connect(self.update_selection_mode)
        self.update_selection_mode()
        self.change_panel()

    def update_selection_mode(self,*_):
        self.area.manual = self.selection_mode.currentData()=='manual'
        self.lane.setEnabled(self.area.manual)
        polarity = self.polarity.currentData()
        if self.automatic_polarity!=polarity:
            self.automatic_rectangles = [automatic_bands(image,len(self.labels),polarity) for image in self.images]
            self.automatic_polarity = polarity
        if self.area.manual:
            for manual,automatic in zip(self.manual_rectangles,self.automatic_rectangles):
                for lane,current in enumerate(manual):
                    if current is None or isinstance(current,dict) and ('threshold' in current or current.get('empty')):
                        manual[lane] = automatic[lane]
            self.rectangles = self.manual_rectangles
        else:
            self.rectangles = self.automatic_rectangles
        self.refresh_overlay()

    def change_panel(self,*_):
        image = self.images[self.panel.currentIndex()]
        self.area.size_px = image.size
        self.area.set_image(image)
        self.area.fit_image()
        self.populate_controls()
        self.refresh_overlay()

    def populate_controls(self):
        index = self.panel.currentIndex()
        uid = self.panels[index]['uid']
        with QSignalBlocker(self.control):
            self.control.clear()
            default = self.lane_names[self.default_control] if self.default_control is not None else '未设置'
            self.control.addItem('请选择对照泳道' if index==0 else f'沿用首条（{default}）',None)
            for lane,name in enumerate(self.lane_names):
                self.control.addItem(name,lane)
            selected = self.default_control if index==0 else self.controls.get(uid)
            self.control.setCurrentIndex(max(0,self.control.findData(selected)))

    def change_control(self,*_):
        index = self.panel.currentIndex()
        uid = self.panels[index]['uid']
        selected = self.control.currentData()
        if index==0:
            self.default_control = selected
        elif selected is None:
            self.controls.pop(uid,None)
        else:
            self.controls[uid] = selected
        self.refresh_overlay()

    def settings(self):
        panels = {panel['uid']:dict(signature=signature,labels=self.labels,rectangles=rectangles)
                  for panel,signature,rectangles in zip(self.panels,self.signatures,self.rectangles)}
        return dict(enabled=self.enable.isChecked(),default_control=self.default_control,controls=dict(self.controls),
                    polarity=self.polarity.currentData(),display=self.display_mode.currentData(),
                    selection=self.selection_mode.currentData(),panels=panels)

    def refresh_overlay(self,*_):
        index = self.panel.currentIndex()
        self.area.boxes = self.rectangles[index]
        self.area.active = self.lane.currentIndex()
        self.area.update()
        if not all(self.area.boxes):
            self.status.setText(f'已圈选 {sum(bool(box) for box in self.area.boxes)}／{len(self.labels)} 个泳道。')
            return
        try:
            ratios,_,_ = panel_density(self.panels[index],self.images[index],self.labels,self.settings())
        except ValueError as error:
            self.status.error(str(error))
            return
        self.status.setText('　'.join(f'{name}  {value:.2f}' for name,value in zip(self.lane_names,ratios)))

    def record_trace(self,trace):
        w,h = self.area.size_px
        points = list(dict.fromkeys((min(w-1,round(point.x()*w)),min(h-1,round(point.y()*h))) for point in trace))
        if len(points)>=3:
            xs,ys = zip(*points)
            x,y = min(xs),min(ys)
            self.rectangles[self.panel.currentIndex()][self.lane.currentIndex()] = dict(
                rect=[x,y,max(xs)-x+1,max(ys)-y+1],points=points)
        self.refresh_overlay()

    def clear_roi(self):
        if not self.area.manual:
            return
        self.rectangles[self.panel.currentIndex()][self.lane.currentIndex()] = None
        self.refresh_overlay()

    def finish(self):
        settings = self.settings()
        if settings['enabled']:
            if self.default_control is None:
                self.panel.setCurrentIndex(0)
                self.status.error('请先为首条蛋白／核酸选择对照泳道。')
                return
            try:
                for index,(panel,image) in enumerate(zip(self.panels,self.images)):
                    panel_density(panel,image,self.labels,settings)
            except ValueError as error:
                self.panel.setCurrentIndex(index)
                self.status.error(str(error))
                return
        self.result_settings = settings
        self.accept()
