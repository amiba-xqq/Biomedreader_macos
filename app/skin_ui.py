"""Appearance gallery and an explicit preview-and-save AI skin workflow."""
from pathlib import Path
import json

from PySide6.QtCore import QEvent, QMimeData, QPointF, QRectF, Signal, QSize, Qt
from PySide6.QtGui import QColor, QDrag, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QComboBox, QSizePolicy, QVBoxLayout, QWidget)

from ai_store import PROVIDERS
from ai_ui import plain_label
from path_dialog import QFileDialog
from skin_ai import IMAGE_LABELS, generate_pack, private_config, reference_summary
from skin_theme import apply_theme, shade
from skin_artwork import connect_artwork_scroll, paint_cover, pixmap_for
from interface_scale import scaled
from skins import MAX_CARDS, blend, decode_image, jpeg, pack_metadata, read_image_file

IMAGE_FILTER = '图片 (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)'
IMAGE_SUFFIXES = {'.png','.jpg','.jpeg','.webp','.bmp','.tif','.tiff'}
PACK_MIME = 'application/x-biomed-skin-id'
WINDOW_CONTROLS = (Qt.WindowType.WindowSystemMenuHint | Qt.WindowType.WindowMinimizeButtonHint
                   | Qt.WindowType.WindowMaximizeButtonHint | Qt.WindowType.WindowCloseButtonHint)


def skin_button(text, *, quiet=False):
    button = QPushButton(text)
    if quiet:
        button.setProperty('secondary',True)
    button.setMinimumWidth(button.fontMetrics().horizontalAdvance(text)+scaled(42))
    return button


def secondary(text):
    return skin_button(text,quiet=True)


class SkinDialog(QDialog):
    def __init__(self, parent, title, size):
        super().__init__(parent, Qt.WindowType.Window | WINDOW_CONTROLS)
        self.setWindowTitle(title)
        self.resize(*size)


def choose_files(parent, title, filters, mode=QFileDialog.FileMode.ExistingFile, save_as=''):
    dialog = QFileDialog(parent)
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setWindowFlags(Qt.WindowType.Window | WINDOW_CONTROLS)
    dialog.setWindowTitle(title)
    dialog.setNameFilter(filters)
    dialog.setFileMode(mode)
    if save_as:
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setDefaultSuffix('zip')
        dialog.selectFile(save_as)
    result = dialog.selectedFiles() if dialog.exec() else []
    dialog.deleteLater()
    return result


def skin_message(parent, title, text, action=None):
    dialog = SkinDialog(parent,title,(480,190))
    layout = QVBoxLayout(dialog)
    layout.addWidget(plain_label(text),1)
    buttons = QHBoxLayout()
    buttons.addStretch()
    close = secondary('取消' if action else '关闭')
    close.setDefault(True)
    close.clicked.connect(dialog.reject)
    buttons.addWidget(close)
    if action:
        confirm = skin_button(action)
        confirm.clicked.connect(dialog.accept)
        buttons.addWidget(confirm)
    layout.addLayout(buttons)
    accepted = bool(dialog.exec())
    dialog.deleteLater()
    return accepted


def preview_icon(data):
    image = QPixmap()
    image.loadFromData(data)
    return QIcon(image)


def add_preview(listing, label, icon):
    item = QListWidgetItem(icon,label)
    item.setToolTip(label)
    listing.addItem(item)
    return item


def ordered_images(name):
    listing = QListWidget()
    listing.setAccessibleName(name)
    listing.setIconSize(QSize(104,64))
    listing.setMinimumHeight(115)
    listing.setDragDropMode(QListWidget.DragDropMode.InternalMove)
    return listing


def order_tools(listing):
    row = QHBoxLayout()

    def move(offset):
        index = listing.currentRow()
        target = index + offset
        if index >= 0 and 0 <= target < listing.count():
            listing.insertItem(target,listing.takeItem(index))
            listing.setCurrentRow(target)

    for title,offset in (('前移',-1),('后移',1)):
        button = secondary(title)
        button.clicked.connect(lambda _,delta=offset:move(delta))
        row.addWidget(button)
    row.addStretch()
    return row


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
    def __init__(self, skin, move=None):
        super().__init__(skin.name)
        self.skin = skin
        self.move_skin = move
        self.pressed_at = None
        self.setAcceptDrops(move is not None)
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

    def mousePressEvent(self,event):
        self.pressed_at = event.position().toPoint() if event.button()==Qt.MouseButton.LeftButton else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if (self.move_skin and self.pressed_at is not None and event.buttons() & Qt.MouseButton.LeftButton
                and (event.position().toPoint()-self.pressed_at).manhattanLength() >= QApplication.startDragDistance()):
            self.pressed_at = None
            self.setDown(False)
            drag = QDrag(self)
            data = QMimeData()
            data.setData(PACK_MIME,self.skin.id.encode('ascii'))
            drag.setMimeData(data)
            drag.setPixmap(self.grab().scaledToWidth(scaled(140),Qt.TransformationMode.SmoothTransformation))
            drag.exec(Qt.DropAction.MoveAction)
        else:
            super().mouseMoveEvent(event)

    def dragEnterEvent(self,event):
        if event.source() is not self and event.mimeData().hasFormat(PACK_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    dragMoveEvent = dragEnterEvent

    def dropEvent(self,event):
        if event.source() is not self and event.mimeData().hasFormat(PACK_MIME):
            event.acceptProposedAction()
            self.move_skin(bytes(event.mimeData().data(PACK_MIME)).decode('ascii','ignore'),self.skin.id)
        else:
            event.ignore()

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


class DIYDialog(SkinDialog):
    image_progress = Signal(int,int)

    def __init__(self, page):
        super().__init__(page.window,'AI DIY皮肤包',(840,790))
        self.page = page
        self.manager = page.manager
        self.task = None
        self.draft = None
        root = QVBoxLayout(self)
        heading = QLabel('AI DIY皮肤包')
        heading.setObjectName('PageTitle')
        root.addWidget(heading)
        root.addWidget(plain_label('生成 1 张桌面背景和 8 张独立卡片背景。生成后可预览并拖动调整卡片背景顺序。'))
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
        root.addWidget(QLabel('参考图片 · 拖入图片作为创作素材'))
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
        self.preview = QLabel('桌面背景预览')
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setObjectName('Muted')
        self.card_previews = ordered_images('卡片背景预览')
        preview = QHBoxLayout()
        preview.addWidget(self.preview,3)
        card_section = QVBoxLayout()
        card_section.addWidget(QLabel('卡片背景预览'))
        card_section.addWidget(self.card_previews,1)
        card_section.addLayout(order_tools(self.card_previews))
        preview.addLayout(card_section,2)
        root.addLayout(preview,1)
        self.reference_info = plain_label('',True)
        root.addWidget(self.reference_info)
        self.status = plain_label('参考图仅保存路径。生成会向支持图像输入的所选模型提交参考图，可能产生费用。',True)
        root.addWidget(self.status)
        self.progress = QProgressBar()
        self.image_progress.connect(self.update_progress)
        self.progress.hide()
        root.addWidget(self.progress)
        buttons = QHBoxLayout()
        self.generate = skin_button('生成皮肤预览')
        self.generate.clicked.connect(self.start)
        self.cancel = secondary('取消 AI 任务')
        self.cancel.clicked.connect(self.cancel_task)
        self.cancel.setEnabled(False)
        self.save = skin_button('保存并应用')
        self.save.setEnabled(False)
        self.save.clicked.connect(self.save_draft)
        close = secondary('关闭')
        close.clicked.connect(self.reject)
        for button in (self.generate,self.cancel,self.save,close):
            buttons.addWidget(button)
        root.addLayout(buttons)
        self.load_models()
        self.image.currentIndexChanged.connect(self.update_reference_info)
        self.update_reference_info()

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
        self.update_reference_info()

    def update_reference_info(self):
        from image_routes import ROUTES,config
        provider = self.image.currentData()
        store = self.manager.store
        cfg = config(store.database,provider,'personal') if provider in ROUTES else store.config(provider,'personal')
        self.reference_info.setText(reference_summary(provider,cfg.get('model') or '',self.refs.paths))
        self.reference_info.setToolTip('\n'.join(Path(path).name for path in self.refs.paths))

    def choose_images(self):
        paths = choose_files(self,'添加参考图片',IMAGE_FILTER,QFileDialog.FileMode.ExistingFiles)
        self.refs.add_paths(paths)

    def set_running(self, running):
        for widget in (self.generate,self.text,self.image,self.prompt,self.name,self.refs,self.add,self.remove,self.card_previews):
            widget.setEnabled(not running)
        self.cancel.setEnabled(running)
        self.progress.setVisible(running)
        self.save.setEnabled(not running and self.draft is not None)

    def start(self):
        if self.task:
            return
        name, request = self.name.text().strip(), self.prompt.toPlainText().strip()
        if not name or not request:
            self.status.setText('请输入皮肤名称和作图要求。')
            return
        if len(request)>4000:
            self.status.setText('请将作图要求精简到 4000 字以内。')
            return
        image_provider = self.image.currentData()
        store = self.manager.store
        try:
            cfg = private_config(store,image_provider)
        except ValueError as error:
            self.status.setText(str(error))
            return
        text_provider, route = self.text.currentData()
        paths = tuple(self.refs.paths)
        self.reference_info.setText(reference_summary(image_provider,cfg.get('model') or '',paths))
        self.progress.setRange(0,0)
        self.set_running(True)
        self.status.setText('正在编写绘图提示…')
        notify = self.image_progress.emit
        self.task = self.manager.submit(self,lambda:generate_pack(store,text_provider,route,image_provider,name,request,paths,notify),
                                        'generated','failed')

    def update_progress(self,done,total):
        if self.task is None:
            return
        self.progress.setRange(0,total)
        self.progress.setValue(done)
        if self.cancel.isEnabled():
            self.status.setText('正在生成桌面背景…' if done==0 else f'正在生成卡片背景，已完成 {done-1} / {total-1} 张。')

    def cancel_task(self):
        if self.task:
            self.manager.cancel(self.task)
            self.cancel.setEnabled(False)
            self.status.setText('正在取消本次生成…')

    def generated(self, draft):
        self.task = None
        self.draft = draft
        pic = QPixmap()
        pic.loadFromData(draft[1]['wallpaper.jpg'])
        self.preview.setPixmap(pic.scaled(470,190,Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation))
        self.card_previews.clear()
        for index,member in enumerate(draft[0]['cards'],1):
            item = add_preview(self.card_previews,f'卡片背景 {index}',preview_icon(draft[1][member]))
            item.setData(Qt.ItemDataRole.UserRole,member)
        self.status.setText(f'「{draft[0]["name"]}」已生成，共 {self.card_previews.count()} 张卡片背景。')
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
            ordered = [self.card_previews.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.card_previews.count())]
            skin = self.page.store.receive(dict(metadata,cards=ordered),files.__getitem__)
            self.page.refresh()
            self.page.select(skin)
        except (OSError,ValueError) as error:
            self.status.setText(str(error))
            return
        self.accept()

    def reject(self):
        if self.task and not skin_message(self,'中断 AI 任务','关闭后将取消当前任务并停止后续生图请求。','中断并关闭'):
            return
        self.manager.abandon_owner(self)
        super().reject()

    def closeEvent(self,event):
        event.ignore()
        self.reject()


class LocalImageList(QListWidget):
    paths_dropped = Signal(list)

    def dragEnterEvent(self,event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    dragMoveEvent = dragEnterEvent

    def dropEvent(self,event):
        self.paths_dropped.emit([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
        event.acceptProposedAction()


class LocalSkinDialog(SkinDialog):
    def __init__(self,page):
        super().__init__(page,'DIY 皮肤包',(960,680))
        self.page = page
        self.wallpaper = None
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setMaxLength(48)
        form.addRow('皮肤名称',self.name)
        layout.addLayout(form)
        layout.addWidget(plain_label('将图片拖到「待选图片」，然后指定 1 张桌面背景和至少 1 张卡片背景。'))
        sides = QHBoxLayout()
        layout.addLayout(sides,1)
        left = QVBoxLayout()
        left.addWidget(QLabel('待选图片'))
        self.images = LocalImageList()
        self.images.setAccessibleName('待选图片')
        self.images.setAcceptDrops(True)
        self.images.setIconSize(QSize(104,64))
        self.images.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.images.paths_dropped.connect(self.add_images)
        left.addWidget(self.images,1)
        add = secondary('添加图片')
        add.clicked.connect(self.browse)
        left.addWidget(add)
        sides.addLayout(left,1)

        right = QVBoxLayout()
        self.desktop = QLabel('尚未选择桌面背景')
        self.desktop.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.desktop.setMinimumHeight(106)
        self.desktop.setObjectName('Muted')
        right.addWidget(QLabel('桌面背景'))
        right.addWidget(self.desktop)
        self.desktop_button = secondary('设为桌面背景')
        self.desktop_button.clicked.connect(self.assign_wallpaper)
        right.addWidget(self.desktop_button)
        right.addWidget(QLabel('卡片背景 · 可拖动排序'))
        self.cards = ordered_images('已选卡片背景')
        right.addWidget(self.cards,1)
        self.cards_button = secondary('加入卡片背景')
        self.cards_button.clicked.connect(self.assign_cards)
        tools = order_tools(self.cards)
        remove = secondary('移除卡片背景')
        remove.clicked.connect(self.remove_card)
        tools.insertWidget(0,self.cards_button)
        tools.insertWidget(1,remove)
        right.addLayout(tools)
        sides.addLayout(right,1)

        self.status = plain_label('',True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        row.addStretch()
        close = secondary('关闭')
        close.clicked.connect(self.reject)
        row.addWidget(close)
        self.save = skin_button('保存并应用')
        self.save.clicked.connect(self.save_skin)
        row.addWidget(self.save)
        layout.addLayout(row)
        self.name.textChanged.connect(self.update_controls)
        self.images.itemSelectionChanged.connect(self.update_controls)
        self.update_controls()

    def browse(self):
        self.add_images(choose_files(self,'添加本地图片',IMAGE_FILTER,QFileDialog.FileMode.ExistingFiles))

    def add_images(self,paths):
        existing = {self.images.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.images.count())}
        failed = []
        added = []
        for value in paths:
            try:
                path = str(Path(value).resolve(strict=True))
                if path in existing:
                    continue
                image = decode_image(read_image_file(path))
                icon = preview_icon(jpeg(image,(220,140),85))
            except (OSError,ValueError):
                failed.append(Path(value).name)
                continue
            item = add_preview(self.images,Path(path).name,icon)
            item.setToolTip(path)
            item.setData(Qt.ItemDataRole.UserRole,path)
            added.append(item)
            existing.add(path)
        if added:
            self.images.clearSelection()
            for item in added:
                item.setSelected(True)
        self.status.setText(f'无法读取 {len(failed)} 张图片。首个文件为「{failed[0]}」。' if failed else '')
        self.update_controls()

    def update_controls(self):
        selected = len(self.images.selectedItems())
        self.desktop_button.setEnabled(selected==1)
        self.cards_button.setEnabled(selected>0 and self.cards.count()<MAX_CARDS)
        self.save.setEnabled(bool(self.name.text().strip() and self.wallpaper and self.cards.count()))

    def assign_wallpaper(self):
        items = self.images.selectedItems()
        if len(items)==1:
            item = items[0]
            self.wallpaper = item.data(Qt.ItemDataRole.UserRole)
            self.desktop.setPixmap(item.icon().pixmap(220,140))
            self.desktop.setAccessibleName(f'桌面背景 {item.text()}')
            self.desktop.setToolTip(self.wallpaper)
            self.update_controls()

    def assign_cards(self):
        paths = {self.cards.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.cards.count())}
        for source in sorted(self.images.selectedItems(),key=self.images.row):
            path = source.data(Qt.ItemDataRole.UserRole)
            if path in paths:
                continue
            if self.cards.count()>=MAX_CARDS:
                self.status.setText(f'每个皮肤包最多包含 {MAX_CARDS} 张卡片背景。')
                break
            item = add_preview(self.cards,source.text(),source.icon())
            item.setData(Qt.ItemDataRole.UserRole,path)
            self.cards.setCurrentItem(item)
        self.update_controls()

    def remove_card(self):
        if self.cards.currentRow()>=0:
            self.cards.takeItem(self.cards.currentRow())
            self.update_controls()

    def save_skin(self):
        if not self.save.isEnabled():
            return
        cards = [f'cards/{index:02}.jpg' for index in range(self.cards.count())]
        files = {'wallpaper.jpg':self.wallpaper}
        files.update((key,self.cards.item(index).data(Qt.ItemDataRole.UserRole)) for index,key in enumerate(cards))
        try:
            metadata = pack_metadata(dict(name=self.name.text(),wallpaper='wallpaper.jpg',cards=cards))
            skin = self.page.store.receive(metadata,lambda member:read_image_file(files[member]))
        except (OSError,ValueError) as error:
            self.status.setText(str(error))
            return
        self.page.refresh()
        self.page.select(skin)
        self.accept()


class PackSelection(SkinDialog):
    def __init__(self,parent,title,rows,confirm,checked=()):
        super().__init__(parent,title,(700,555))
        root = QVBoxLayout(self)
        self.listing = QListWidget()
        self.listing.setAccessibleName(title)
        self.listing.setIconSize(QSize(132,84))
        for index,(text,icon) in enumerate(rows):
            item = add_preview(self.listing,text,icon)
            item.setSizeHint(QSize(0,94))
            item.setCheckState(Qt.CheckState.Checked if index in checked else Qt.CheckState.Unchecked)
        root.addWidget(self.listing,1)
        bar = QHBoxLayout()
        for text,state in (('全选',Qt.CheckState.Checked),('取消全选',Qt.CheckState.Unchecked)):
            button = secondary(text)
            button.clicked.connect(lambda _,value=state:self.set_all(value))
            bar.addWidget(button)
        bar.addStretch()
        close = secondary('关闭')
        close.clicked.connect(self.reject)
        bar.addWidget(close)
        self.apply = skin_button(confirm)
        self.apply.clicked.connect(self.accept)
        bar.addWidget(self.apply)
        root.addLayout(bar)
        self.listing.itemChanged.connect(self.update_action)
        self.update_action()

    def selected(self):
        return [index for index in range(self.listing.count())
                if self.listing.item(index).checkState()==Qt.CheckState.Checked]

    def set_all(self,value):
        self.listing.blockSignals(True)
        for index in range(self.listing.count()):
            self.listing.item(index).setCheckState(value)
        self.listing.blockSignals(False)
        self.update_action()

    def update_action(self):
        count = len(self.selected())
        self.apply.setEnabled(count>0)

    def choose(self):
        selected = self.selected() if self.exec() else []
        self.deleteLater()
        return selected


class SkinPage(QWidget):
    def __init__(self, window, store):
        super().__init__(window)
        self.window = window
        self.store = store
        self.setAcceptDrops(True)
        self.manager = window.ai_manager
        self.ai_editor = None
        root = QVBoxLayout(self)
        title = QLabel('皮肤')
        title.setObjectName('PageTitle')
        root.addWidget(title)
        root.addWidget(plain_label('点击卡片切换皮肤。DIY 皮肤可拖动排序，也可右键前移或后移。将 ZIP 拖到此页可预览导入。',True))
        row = QHBoxLayout()
        diy = skin_button('AI DIY皮肤包')
        diy.clicked.connect(self.open_ai_editor)
        local = secondary('DIY 皮肤包')
        local.clicked.connect(lambda: self.open_editor(LocalSkinDialog))
        load = secondary('导入皮肤包')
        load.clicked.connect(self.import_pack)
        self.export = secondary('导出皮肤包')
        self.export.clicked.connect(self.export_pack)
        self.delete = secondary('删除皮肤包')
        self.delete.clicked.connect(self.delete_pack)
        for button in (diy,local,load,self.export,self.delete):
            row.addWidget(button)
        row.addStretch()
        root.addLayout(row)
        self.status = plain_label('',True)
        self.status.hide()
        root.addWidget(self.status)
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

    def open_editor(self,editor):
        dialog = editor(self)
        dialog.exec()
        dialog.deleteLater()

    def open_ai_editor(self):
        dialog = self.ai_editor
        if dialog is None:
            dialog = self.ai_editor = DIYDialog(self)
            dialog.setWindowModality(Qt.WindowModality.NonModal)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            dialog.finished.connect(self.clear_ai_editor)
            dialog.show()
        elif dialog.isMinimized():
            dialog.setWindowState(dialog.windowState() & ~Qt.WindowState.WindowMinimized)
        dialog.raise_()
        dialog.activateWindow()

    def clear_ai_editor(self,_result):
        self.ai_editor = None

    def close_ai_editor(self):
        if self.ai_editor is not None:
            self.ai_editor.close()

    def refresh(self):
        for _,tile in self.tiles:
            self.grid.removeWidget(tile)
            tile.hide()
            tile.deleteLater()
        self.tiles = []
        for skin in self.store.all():
            tile = SkinTile(skin,self.move_pack if skin.directory else None)
            tile.clicked.connect(lambda _,value=skin:self.select(value))
            if skin.directory:
                tile.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                tile.customContextMenuRequested.connect(lambda point,widget=tile:self.move_menu(widget,point))
            self.tiles.append((skin,tile))
        self.custom_title.setVisible(any(skin.directory for skin,_ in self.tiles))
        self.reflow()
        for _,tile in self.tiles:
            tile.show()
        self.update_selection(self.store.active())

    def update_selection(self,active):
        self.export.setEnabled(any(skin.directory for skin,_ in self.tiles))
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

    def custom_ids(self):
        return [skin.id for skin,_ in self.tiles if skin.directory]

    def move_pack(self,source,target):
        order = self.custom_ids()
        if source==target or source not in order or target not in order:
            return
        destination = order.index(target)
        order.remove(source)
        order.insert(destination,source)
        try:
            self.store.reorder(order)
        except (OSError,ValueError) as error:
            self.show_status(str(error))
            return
        by_id = {skin.id:(skin,tile) for skin,tile in self.tiles if skin.directory}
        self.tiles = [pair for pair in self.tiles if not pair[0].directory] + [by_id[identifier] for identifier in order]
        self.reflow()
        by_id[source][1].setFocus()

    def move_menu(self,tile,point):
        order = self.custom_ids()
        index = order.index(tile.skin.id)
        menu = QMenu(tile)
        earlier = menu.addAction('前移')
        later = menu.addAction('后移')
        earlier.setEnabled(index>0)
        later.setEnabled(index+1<len(order))
        action = menu.exec(tile.mapToGlobal(point))
        if action is earlier:
            self.move_pack(tile.skin.id,order[index-1])
        elif action is later:
            self.move_pack(tile.skin.id,order[index+1])

    def show_status(self,text):
        self.status.setText(text)
        self.status.setVisible(bool(text))

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
        paths = choose_files(self,'选择要导入的 ZIP','皮肤包 (*.zip)')
        if paths:
            self.load_pack(paths[0])

    def load_pack(self,path):
        path = Path(path)
        try:
            before = path.stat()
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                previews = self.store.preview_zip(path)
            finally:
                QApplication.restoreOverrideCursor()
            dialog = PackSelection(self,'预览并选择要缓存的皮肤包',
                [(f'{item.name}\n{item.card_count} 张卡片背景',preview_icon(item.thumbnail)) for item in previews],
                '缓存所选',checked=(0,) if len(previews)==1 else ())
            selected = dialog.choose()
            if not selected:
                return
            after = path.stat()
            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                raise ValueError('ZIP 文件在预览后发生了变化，请重新打开。')
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                skins = self.store.import_selected(path,selected)
            finally:
                QApplication.restoreOverrideCursor()
            self.refresh()
            self.show_status(f'已缓存 {len(skins)} 款皮肤。点击卡片即可应用。')
        except (OSError,ValueError) as error:
            self.show_status(f'导入失败。{error}')

    def export_pack(self):
        skins = [skin for skin,_ in self.tiles if skin.directory]
        active_id = self.store.active().id
        dialog = PackSelection(self,'选择要导出的 DIY 皮肤包',
            [(f'{skin.name}\n{len(skin.cards)} 张卡片背景',QIcon(pixmap_for(skin.wallpaper))) for skin in skins],
            '选择保存位置',checked=[i for i,skin in enumerate(skins) if skin.id==active_id])
        indices = dialog.choose()
        if not indices:
            return
        paths = choose_files(self,'保存皮肤包','皮肤包 (*.zip)',QFileDialog.FileMode.AnyFile,'刊禾皮肤包.zip')
        if paths:
            try:
                selected = [skins[i] for i in indices]
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                try:
                    self.store.export_selected(selected,paths[0])
                finally:
                    QApplication.restoreOverrideCursor()
                self.show_status(f'已导出 {len(selected)} 款皮肤到「{Path(paths[0]).name}」。')
            except (OSError,ValueError) as error:
                self.show_status(f'导出失败。{error}')

    def delete_pack(self):
        skin = self.store.active()
        if skin.directory and skin_message(self,'删除皮肤包',
                f'删除「{skin.name}」及其本机背景后，将切回经典皮肤。','删除皮肤包'):
            try:
                self.store.delete(skin)
                self.refresh()
                self.select(self.store.builtins[0])
            except (OSError,ValueError) as error:
                self.show_status(f'删除失败。{error}')
