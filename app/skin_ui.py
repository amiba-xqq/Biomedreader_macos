"""Appearance gallery and an explicit preview-and-save AI skin workflow."""
from pathlib import Path
import json

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QComboBox, QSizePolicy, QVBoxLayout, QWidget)

from ai_store import PROVIDERS
from ai_ui import confirm_ai_close, plain_label
from path_dialog import QFileDialog
from skin_ai import IMAGE_LABELS, generate_pack, private_config
from skin_theme import apply_theme, shade
from skin_artwork import connect_artwork_scroll, paint_cover, pixmap_for
from interface_scale import scaled
from skins import blend

IMAGE_FILTER = '图片 (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)'
IMAGE_SUFFIXES = {'.png','.jpg','.jpeg','.webp','.bmp','.tif','.tiff'}


def secondary(text):
    button = QPushButton(text)
    button.setProperty('secondary',True)
    return button


def swatch(skin):
    preview = QPixmap(480,264)
    preview.fill(QColor(skin.surface))
    with QPainter(preview) as paint:
        paint.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint.fillRect(0,0,118,264,QColor(skin.sidebar))
        paint.setPen(Qt.PenStyle.NoPen)
        for y in (63,110,151):
            paint.setBrush(QColor(skin.selected if y==63 else blend(skin.sidebar,skin.sidebar_text,.12)))
            paint.drawRoundedRect(15,y,87,24,6,6)
        paint.fillRect(145,43,145,10,QColor(skin.accent))
        paint.fillRect(145,66,204,4,QColor(blend(skin.accent,'#FFFFFF',.68)))
        for x in (144,304):
            paint.setBrush(QColor(blend(skin.accent,'#FFFFFF',.89)))
            paint.drawRoundedRect(x,102,142,113,11,11)
            paint.fillRect(x+16,120,68,6,QColor(skin.accent))
            paint.fillRect(x+16,188,92,4,QColor(blend(skin.accent,'#FFFFFF',.52)))
    return preview


class SkinTile(QPushButton):
    def __init__(self, skin):
        super().__init__(skin.name)
        self.skin = skin
        self.sample = None if skin.wallpaper else swatch(skin)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        self.setCheckable(True)
        self.setAccessibleName(skin.name)
        self.setAccessibleDescription(skin.note)
        self.setToolTip(skin.name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self):
        return QSize(scaled(190),scaled(210))

    def minimumSizeHint(self):
        return QSize(scaled(140),self.sizeHint().height())

    def paintEvent(self,event):
        from PySide6.QtWidgets import QApplication
        theme = getattr(QApplication.instance(),'biomed_skin',self.skin)
        accent = theme.accent
        bounds = QRectF(self.rect()).adjusted(2,2,-2,-2)
        border = accent if self.isChecked() else shade(theme,.57 if self.underMouse() else .83)
        radius = scaled(13)
        with QPainter(self) as paint:
            paint.setRenderHint(QPainter.RenderHint.Antialiasing)
            paint.setBrush(QColor(shade(theme,.92) if self.isChecked() else '#FFFFFF'))
            paint.setPen(QPen(QColor(border),scaled(2) if self.isChecked() else 1))
            paint.drawRoundedRect(bounds,radius,radius)
            cover = bounds.adjusted(scaled(6),scaled(6),-scaled(6),-scaled(65))
            picture = self.sample if self.sample is not None else pixmap_for(self.skin.wallpaper)
            clip = QPainterPath()
            clip.addRoundedRect(cover,scaled(9),scaled(9))
            paint.save()
            paint.setClipPath(clip)
            if self.sample is None:
                paint_cover(paint,cover,picture)
            else:
                paint.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                paint.drawPixmap(cover,picture,QRectF(picture.rect()))
            paint.restore()

            name = cover.adjusted(scaled(4),cover.height()+scaled(5),-scaled(4),scaled(28))
            heading = self.font()
            heading.setBold(True)
            paint.setFont(heading)
            paint.setPen(QColor('#292321'))
            paint.drawText(name,Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,
                           paint.fontMetrics().elidedText(self.skin.name,Qt.TextElideMode.ElideRight,int(name.width())))
            note = QRectF(name.left(),name.bottom()+scaled(2),name.width(),scaled(23))
            paint.setFont(self.font())
            paint.setPen(QColor('#655b55'))
            paint.drawText(note,Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,self.skin.note)

            if self.isChecked():
                size = scaled(28)
                center = QPointF(cover.right()-scaled(10)-size/2,cover.top()+scaled(10)+size/2)
                paint.setBrush(QColor(accent))
                paint.setPen(QPen(QColor('#FFFFFF'),scaled(2)))
                paint.drawEllipse(center,size/2,size/2)
                tick = QPainterPath(QPointF(center.x()-scaled(5),center.y()))
                tick.lineTo(center.x()-scaled(1),center.y()+scaled(4))
                tick.lineTo(center.x()+scaled(6),center.y()-scaled(4))
                paint.drawPath(tick)
            if self.hasFocus():
                focus = QPen(QColor(accent),scaled(2),Qt.PenStyle.DashLine)
                paint.setPen(focus)
                paint.setBrush(Qt.BrushStyle.NoBrush)
                paint.drawRoundedRect(bounds.adjusted(3,3,-3,-3),radius,radius)


class References(QListWidget):
    def __init__(self, paths, changed):
        super().__init__()
        self.paths = list(paths)
        self.changed = changed
        self.setAcceptDrops(True)
        self.setMaximumHeight(126)
        self.setSelectionMode(self.SelectionMode.ExtendedSelection)
        self.display()

    def display(self):
        self.clear()
        for path in self.paths:
            self.addItem(path)
        self.setToolTip('把本地图片拖到这里，最多 6 张。')

    def add_paths(self, paths):
        for item in paths:
            path = Path(item)
            if len(self.paths)>=6:
                break
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES and str(path) not in self.paths:
                self.paths.append(str(path))
        self.display()
        self.changed(self.paths)

    def remove_selection(self):
        chosen = {self.row(item) for item in self.selectedItems()}
        self.paths = [path for index,path in enumerate(self.paths) if index not in chosen]
        self.display()
        self.changed(self.paths)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, event):
        self.add_paths([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
        event.acceptProposedAction()


class DIYDialog(QDialog):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.manager = page.manager
        self.task = None
        self.draft = None
        self.setWindowTitle('AI DIY皮肤包')
        self.resize(740,760)
        root = QVBoxLayout(self)
        heading = QLabel('AI DIY皮肤包')
        heading.setObjectName('PageTitle')
        root.addWidget(heading)
        root.addWidget(plain_label('文字模型编写绘图提示，生图模型生成壁纸。卡片背景取自同一张图的不同区域，并自动淡化。'))
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText('输入皮肤名称')
        self.name.setMaxLength(48)
        form.addRow('皮肤名称',self.name)
        self.text = QComboBox()
        self.image = QComboBox()
        form.addRow('文字模型',self.text)
        form.addRow('生图模型',self.image)
        root.addLayout(form)
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText('描述配色、主题和构图，例如：米白和浅棕、右侧一只线稿小狗、中央保持留白。')
        self.prompt.setMaximumHeight(100)
        root.addWidget(self.prompt)
        root.addWidget(QLabel('参考图片 · 拖入图片提取配色'))
        try:
            paths = json.loads(self.page.store.database.get_settings().get('skin_reference_paths','[]'))
            paths = [path for path in paths if isinstance(path,str)][:6] if isinstance(paths,list) else []
        except ValueError:
            paths = []
        self.refs = References(paths,self.save_paths)
        root.addWidget(self.refs)
        tools = QHBoxLayout()
        self.add = secondary('添加参考图')
        self.remove = secondary('移除所选')
        self.add.clicked.connect(self.choose_images)
        self.remove.clicked.connect(self.refs.remove_selection)
        tools.addWidget(self.add)
        tools.addWidget(self.remove)
        tools.addStretch()
        root.addLayout(tools)
        self.preview = QLabel('生成后在这里预览壁纸')
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(150)
        self.preview.setObjectName('Muted')
        root.addWidget(self.preview,1)
        self.status = plain_label('原图仅在本机用于提取配色，软件只保存文件路径。生成可能产生费用。',True)
        root.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0,0)
        self.progress.hide()
        root.addWidget(self.progress)
        buttons = QHBoxLayout()
        self.generate = QPushButton('生成皮肤预览')
        self.generate.clicked.connect(self.start)
        self.cancel = secondary('取消 AI 任务')
        self.cancel.clicked.connect(self.cancel_task)
        self.cancel.setEnabled(False)
        self.save = QPushButton('保存并应用')
        self.save.setEnabled(False)
        self.save.clicked.connect(self.save_draft)
        close = secondary('关闭')
        close.clicked.connect(self.reject)
        for button in (self.generate,self.cancel,self.save,close):
            buttons.addWidget(button)
        root.addLayout(buttons)
        self.load_models()

    def load_models(self):
        store = self.manager.store
        for p,label in PROVIDERS.items():
            cfg = store.config(p)
            model = cfg['model'] or '待配置'
            channel = '本机' if p=='codex' else '按部署优先级'
            self.text.addItem(f'{label} · {model} · {channel}',(p,None))
            if p!='codex':
                for kind,title in [('personal','个人 API'),('lab','课题组授权')]:
                    cfg = store.config(p,kind)
                    if store.channel_ready(cfg):
                        self.text.addItem(f'{label} · {cfg["model"]} · {title}',(p,kind))
        from image_routes import ROUTES,config
        for p,label in IMAGE_LABELS.items():
            cfg = config(store.database,p,'personal') if p in ROUTES else store.config(p,'personal')
            model = cfg['model'] or '待配置'
            self.image.addItem(f'{label} · {model}'+(' · 个人直连' if p!='codex' else ''),p)
        self.text.setCurrentIndex(0)  # DeepSeek is the first configured provider.
        self.image.setCurrentIndex(self.image.findData('ark'))

    def save_paths(self, paths):
        self.page.store.database.save_settings({'skin_reference_paths':json.dumps(paths,ensure_ascii=False)})

    def choose_images(self):
        paths,_ = QFileDialog.getOpenFileNames(self,'添加参考图片','',IMAGE_FILTER)
        self.refs.add_paths(paths)

    def set_running(self, running):
        for widget in (self.generate,self.text,self.image,self.prompt,self.name,self.refs,self.add,self.remove):
            widget.setEnabled(not running)
        self.cancel.setEnabled(running)
        self.progress.setVisible(running)
        self.save.setEnabled(not running and self.draft is not None)

    def start(self):
        if self.task:
            return
        name, request = self.name.text().strip(), self.prompt.toPlainText().strip()
        if not name or not request:
            QMessageBox.information(self,'填写皮肤要求','请输入皮肤名称和作图要求。')
            return
        if len(request)>4000:
            QMessageBox.information(self,'作图要求过长','请将作图要求精简到 4000 字以内。')
            return
        image_provider = self.image.currentData()
        store = self.manager.store
        try:
            private_config(store,image_provider)
        except ValueError as error:
            QMessageBox.warning(self,'生图通道不可用',str(error))
            return
        text_provider, route = self.text.currentData()
        paths = tuple(self.refs.paths)
        self.set_running(True)
        self.status.setText('正在编写提示词并生成背景…')
        self.task = self.manager.submit(self,lambda:generate_pack(store,text_provider,route,image_provider,name,request,paths),
                                        'generated','failed')

    def cancel_task(self):
        if self.task:
            self.manager.cancel(self.task)
            self.cancel.setEnabled(False)

    def generated(self, draft):
        self.task = None
        self.draft = draft
        pic = QPixmap()
        pic.loadFromData(draft[1]['wallpaper.jpg'])
        self.preview.setPixmap(pic.scaled(550,168,Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation))
        self.status.setText(f'「{draft[0]["name"]}」已生成。确认后保存皮肤包并切换。')
        self.set_running(False)

    def failed(self, message):
        self.task = None
        self.set_running(False)
        self.status.setText(message)

    def save_draft(self):
        if not self.draft:
            return
        metadata, files = self.draft
        try:
            skin = self.page.store.receive(metadata,files.__getitem__)
            self.page.refresh()
            self.page.select(skin)
        except (OSError,ValueError) as error:
            QMessageBox.warning(self,'保存失败',str(error))
            return
        self.accept()

    def reject(self):
        if self.task and not confirm_ai_close(self):
            return
        self.manager.abandon_owner(self)
        super().reject()

    def closeEvent(self,event):
        event.ignore()
        self.reject()


class SkinPage(QWidget):
    def __init__(self, window, store):
        super().__init__(window)
        self.window = window
        self.store = store
        self.setAcceptDrops(True)
        self.manager = window.ai_manager
        root = QVBoxLayout(self)
        title = QLabel('皮肤')
        title.setObjectName('PageTitle')
        root.addWidget(title)
        root.addWidget(plain_label('点击卡片即可切换皮肤。可将 ZIP 皮肤包拖到此页导入。',True))
        row = QHBoxLayout()
        diy = QPushButton('AI DIY皮肤包')
        diy.clicked.connect(lambda: DIYDialog(self).exec())
        load = secondary('导入皮肤包')
        load.clicked.connect(self.import_pack)
        self.export = secondary('导出皮肤包')
        self.export.clicked.connect(self.export_pack)
        self.delete = secondary('删除皮肤包')
        self.delete.clicked.connect(self.delete_pack)
        for button in (diy,load,self.export,self.delete):
            row.addWidget(button)
        row.addStretch()
        root.addLayout(row)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.scroll.setWidget(self.content)
        connect_artwork_scroll(self.scroll)
        self.scroll.viewport().installEventFilter(self)
        gallery = QVBoxLayout(self.content)
        gallery.setContentsMargins(4,4,4,8)
        gallery.setSpacing(14)
        self.grid = QGridLayout()
        gallery.addLayout(self.grid)
        self.custom_title = QLabel('DIY 皮肤包')
        self.custom_title.setObjectName('SectionTitle')

        gallery.addStretch()
        root.addWidget(self.scroll,1)
        self.columns = 0
        self.tiles = []
        self.refresh()

    def refresh(self):
        for _,tile in self.tiles:
            self.grid.removeWidget(tile)
            tile.hide()
            tile.deleteLater()
        self.tiles = []
        for skin in self.store.all():
            tile = SkinTile(skin)
            tile.clicked.connect(lambda _,value=skin:self.select(value))
            self.tiles.append((skin,tile))
        self.custom_title.setVisible(any(skin.directory for skin,_ in self.tiles))
        self.reflow()
        self.update_selection(self.store.active())

    def update_selection(self,active):
        self.export.setEnabled(active.directory is not None)
        self.delete.setEnabled(active.directory is not None)
        for skin,tile in self.tiles:
            tile.setChecked(skin.id==active.id)
            tile.update()

    def reflow(self):
        columns = max(1,min(5,self.scroll.viewport().width()//scaled(172)))
        self.grid.removeWidget(self.custom_title)
        for _,tile in self.tiles:
            self.grid.removeWidget(tile)
        for column in range(5):
            self.grid.setColumnStretch(column,1 if column<columns else 0)
        builtins = [tile for skin,tile in self.tiles if not skin.directory]
        custom = [tile for skin,tile in self.tiles if skin.directory]
        for index,tile in enumerate(builtins):
            self.grid.addWidget(tile,index//columns,index%columns)
        if custom:
            heading_row = (len(builtins)+columns-1)//columns
            self.grid.addWidget(self.custom_title,heading_row,0,1,columns)
            for index,tile in enumerate(custom):
                self.grid.addWidget(tile,heading_row+1+index//columns,index%columns)
        self.columns = columns

    def eventFilter(self,watched,event):
        if watched is self.scroll.viewport() and event.type()==QEvent.Type.Resize and hasattr(self,'tiles'):
            self.reflow()
        return super().eventFilter(watched,event)

    def select(self,skin):
        self.store.select(skin)
        apply_theme(self.window.interface_scale,skin)
        self.reflow()
        self.update_selection(skin)

    @staticmethod
    def dropped_pack(event):
        urls = event.mimeData().urls()
        if len(urls)==1 and urls[0].isLocalFile():
            path = Path(urls[0].toLocalFile())
            if path.suffix.lower()=='.zip' and path.is_file():
                return path
        return None

    def dragEnterEvent(self,event):
        if self.dropped_pack(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self,event):
        path = self.dropped_pack(event)
        if path is None:
            event.ignore()
            return
        event.acceptProposedAction()
        self.load_pack(path)

    def import_pack(self):
        path,_ = QFileDialog.getOpenFileName(self,'导入皮肤包','','皮肤包 (*.zip)')
        if path:
            self.load_pack(path)

    def load_pack(self,path):
        try:
            skin = self.store.import_zip(path)
            self.refresh()
            self.select(skin)
        except (OSError,ValueError) as error:
            QMessageBox.warning(self,'导入失败',str(error))

    def export_pack(self):
        skin = self.store.active()
        path,_ = QFileDialog.getSaveFileName(self,'导出皮肤包','刊禾皮肤包.zip','皮肤包 (*.zip)')
        if path:
            try:
                self.store.export_zip(skin,path if path.lower().endswith('.zip') else path+'.zip')
            except (OSError,ValueError) as error:
                QMessageBox.warning(self,'导出失败',str(error))

    def delete_pack(self):
        skin = self.store.active()
        if skin.directory and QMessageBox.question(self,'删除皮肤包',
                f'删除「{skin.name}」及本机生成的背景后，将切回经典皮肤。确定删除？',
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes:
            try:
                self.store.delete(skin)
                self.refresh()
                self.select(self.store.builtins[0])
            except (OSError,ValueError) as error:
                QMessageBox.warning(self,'删除失败',str(error))
