"""Paint skin artwork only on navigation surfaces and their feature cards."""
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPixmap, QPixmapCache, QRadialGradient
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QScrollArea, QWidget

PLAIN_CARDS = {'Card','ArticleCard','AIModelCard','LabRoleCard','AIRecommendation','FinderArticle','EditorialPanel'}


def pixmap_for(path):
    key = str(path)
    result = QPixmapCache.find(key)
    if result is None:
        result = QPixmap(key)
        if not result.isNull():
            QPixmapCache.insert(key,result)
    return result


def paint_cover(painter, target, picture):
    if picture.isNull() or target.isEmpty():
        return
    size = QRectF(picture.rect())
    ratio = max(target.width()/size.width(),target.height()/size.height())
    width,height = target.width()/ratio,target.height()/ratio
    source = QRectF((size.width()-width)/2,(size.height()-height)/2,width,height)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawPixmap(target,picture,source)


def connect_artwork_scroll(scroll):
    scroll.viewport().setProperty('skinTransparent',True)
    scroll.widget().setProperty('skinTransparent',True)


def label_boxes(surface, container, *, skip_cards):
    rects = []
    for label in container.findChildren(QLabel):
        if label.window() is not surface.window() or not label.isVisibleTo(surface) or not label.text().strip():
            continue
        if skip_cards:
            parent = label.parentWidget()
            while parent is not surface and parent is not None:
                if ((isinstance(parent,QFrame) and parent.objectName() in PLAIN_CARDS)
                    or (isinstance(parent,QScrollArea) and not parent.viewport().property('skinTransparent'))):
                    break
                parent = parent.parentWidget()
            if parent is not surface:
                continue
        space = label.contentsRect().adjusted(label.margin(),label.margin(),-label.margin(),-label.margin())
        flags = label.alignment().value
        if label.wordWrap():
            flags |= Qt.TextFlag.TextWordWrap.value
        bounds = label.fontMetrics().boundingRect(space,flags,label.text())
        visible = label.visibleRegion().boundingRect()
        bounds = bounds.intersected(visible)
        if not bounds.isEmpty():
            rects.append(QRectF(label.mapTo(surface,bounds.topLeft()),bounds.size()))
    return rects


def paint_text_veil(painter, boxes, tint, opacity=225):
    core = QColor(tint)
    core.setAlpha(opacity)
    halfway = QColor(tint)
    halfway.setAlpha(round(opacity*.46))
    clear = QColor(tint)
    clear.setAlpha(0)
    feather = 13
    for bounds in boxes:
        body = bounds.adjusted(-4,-3,4,3)
        x,y,w,h = body.x(),body.y(),body.width(),body.height()
        right,bottom = x+w,y+h
        painter.fillRect(body,core)
        for edge,start,end in (
            (QRectF(x-feather,y,feather,h),QPointF(x,y),QPointF(x-feather,y)),
            (QRectF(right,y,feather,h),QPointF(right,y),QPointF(right+feather,y)),
            (QRectF(x,y-feather,w,feather),QPointF(x,y),QPointF(x,y-feather)),
            (QRectF(x,bottom,w,feather),QPointF(x,bottom),QPointF(x,bottom+feather)),
        ):
            gradient = QLinearGradient(start,end)
            gradient.setStops([(0,core),(.5,halfway),(1,clear)])
            painter.fillRect(edge,gradient)
        for center,cx,cy in (
            (body.topLeft(),x-feather,y-feather),(body.topRight(),right,y-feather),
            (body.bottomLeft(),x-feather,bottom),(body.bottomRight(),right,bottom),
        ):
            gradient = QRadialGradient(center,feather)
            gradient.setStops([(0,core),(.5,halfway),(1,clear)])
            painter.fillRect(QRectF(cx,cy,feather,feather),gradient)


class SkinRoot(QWidget):
    def dragEnterEvent(self,event):
        page = self.pages.currentWidget()
        if hasattr(page,'dropped_pack'):
            page.dragEnterEvent(event)
        else:
            event.ignore()

    def dropEvent(self,event):
        page = self.pages.currentWidget()
        if hasattr(page,'dropped_pack'):
            page.dropEvent(event)
        else:
            event.ignore()

    def paintEvent(self,event):
        super().paintEvent(event)
        skin = getattr(QApplication.instance(),'biomed_skin',None)
        if not skin or not skin.wallpaper:
            return
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            paint_cover(painter,QRectF(self.rect()),pixmap_for(skin.wallpaper))
            page = self.pages.currentWidget() if hasattr(self,"pages") else None
            if page is not None:
                paint_text_veil(painter,label_boxes(self,page,skip_cards=True),skin.surface)

            sidebar = getattr(self,'sidebar',None)
            if sidebar is not None:
                from interface_scale import scaled
                boxes = label_boxes(self,sidebar,skip_cards=False)
                for button in sidebar.findChildren(QPushButton):
                    if button.isVisibleTo(self) and not button.isChecked():
                        fm = button.fontMetrics()
                        start = button.mapTo(self,QPoint(scaled(13),(button.height()-fm.height())//2))
                        boxes.append(QRectF(start.x(),start.y(),
                                           fm.horizontalAdvance(button.text())+scaled(25),fm.height()))
                paint_text_veil(painter,boxes,'#FFFFFF',174)


def card_image(card,skin):
    if not skin or not skin.cards or card.isWindow():
        return None
    index = card.property('skinArtworkIndex')
    if index is None:
        board = getattr(card,'board',None)
        if board is None:
            return None
        offset = board.property('skinArtworkOffset')
        if offset is None or card not in board.cards:
            return None
        index = offset + board.cards.index(card)
    return skin.cards[index % len(skin.cards)]


def paint_card_artwork(card,painter):
    skin = getattr(QApplication.instance(),'biomed_skin',None)
    path = card_image(card,skin)
    if path is None:
        return
    picture = pixmap_for(path)
    if picture.isNull():
        return
    area = QRectF(card.rect()).adjusted(1,1,-1,-2)
    clip = QPainterPath()
    from interface_scale import scaled
    radius = scaled(13)
    clip.addRoundedRect(area,radius,radius)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setClipPath(clip)
    paint_cover(painter,area,picture)
    painter.fillRect(area,QColor(255,255,255,74))
    paint_text_veil(painter,label_boxes(card,card,skip_cards=False),'#FFFFFF',218)
    painter.restore()
