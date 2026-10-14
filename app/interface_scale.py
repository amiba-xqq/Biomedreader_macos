"""Application zoom in logical pixels, layered on top of Qt's native DPI support."""
import re
import weakref
from PySide6.QtCore import QObject,QEvent,QTimer,Signal
from PySide6.QtWidgets import QApplication,QWidget,QAbstractItemView,QDialog


def factor():
    app=QApplication.instance()
    return float(getattr(app,'biomed_scale',1.0)) if app else 1.0


def scaled(value):
    return round(value*factor())


def scale_style(style,zoom):
    return re.sub(r'(?<![\w#])([0-9]+(?:\.[0-9]+)?)(pt|px)',
        lambda match:f'{float(match[1])*zoom:g}{match[2]}',style)


class InterfaceScale(QObject):
    changed=Signal(float)

    def __init__(self,window,database,style):
        super().__init__(window)
        self.window=window;self.database=database;self.style=style;self.screen=None;self.busy=False
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.apply)
        # A Python application-wide filter also receives Chromium's transient
        # native QObjects. PySide can crash while wrapping a dying native object
        # before eventFilter() is entered. Observe stable application windows
        # only; discover new dialogs without filtering Chromium internals.
        self.watched_windows=weakref.WeakSet()
        self.window.installEventFilter(self);self.watched_windows.add(self.window)
        self.window_scan=QTimer(self);self.window_scan.setInterval(400)
        self.window_scan.timeout.connect(self.watch_dialogs);self.window_scan.start()
        self.schedule()

    def watch_dialogs(self):
        if getattr(self,'stopped',False):return
        for top in QApplication.topLevelWidgets():
            if isinstance(top,QDialog) and top not in self.watched_windows:
                top.installEventFilter(self);self.watched_windows.add(top);self.schedule()

    def eventFilter(self,obj,event):
        if event.type() in (QEvent.Type.Show,QEvent.Type.ScreenChangeInternal) and isinstance(obj,QWidget):
            if not self.busy:self.schedule()
        return False

    def schedule(self):
        if not getattr(self,'stopped',False):self.timer.start(80)

    def stop(self):
        self.stopped=True
        self.timer.stop();self.window_scan.stop()
        for top in list(self.watched_windows):
            try:top.removeEventFilter(self)
            except RuntimeError:pass
        self.watched_windows.clear()
        if self.screen:
            try:self.screen.availableGeometryChanged.disconnect(self.schedule)
            except (RuntimeError,TypeError):pass

    def requested_factor(self):
        settings=self.database.get_settings()
        screen=self.window.screen()
        # Width is logical (already DPI adjusted); avoid multiplying devicePixelRatio twice.
        automatic=max(1.0,min(1.6,screen.availableGeometry().width()/1600)) if screen else 1.0
        base=automatic if settings.get('interface_auto_scale','1')=='1' else 1.0
        return max(.8,min(2.0,base*int(settings.get('interface_scale_percent','100'))/100))

    def apply(self):
        if self.busy:return
        self.busy=True
        try:
            handle=self.window.windowHandle()
            if handle and not getattr(self,'connected_handle',None):
                handle.screenChanged.connect(lambda screen:self.schedule());self.connected_handle=handle
            screen=self.window.screen()
            if screen is not self.screen:
                if self.screen:
                    try:self.screen.availableGeometryChanged.disconnect(self.schedule)
                    except (RuntimeError,TypeError):pass
                self.screen=screen
                if screen:screen.availableGeometryChanged.connect(self.schedule)
            app=QApplication.instance();zoom=self.requested_factor();previous=factor()
            app.biomed_scale=zoom
            if previous!=zoom and screen and not self.window.isMaximized():
                available=screen.availableGeometry()
                self.window.resize(min(available.width(),round(self.window.width()*zoom/previous)),
                    min(available.height(),round(self.window.height()*zoom/previous)))
            stylesheet=scale_style(self.style,zoom)
            if app.styleSheet()!=stylesheet:app.setStyleSheet(stylesheet)
            for top in app.topLevelWidgets():
                if isinstance(top,QDialog) and screen:
                    current=(top.width(),top.height())
                    if not hasattr(top,'_zoom_base_size'):top._zoom_base_size=current
                    elif current!=getattr(top,'_zoom_last_size',current):
                        top._zoom_base_size=tuple(n/previous for n in current)
                    available=screen.availableGeometry()
                    top.resize(min(available.width(),round(top._zoom_base_size[0]*zoom)),
                        min(available.height(),round(top._zoom_base_size[1]*zoom)))
                    top._zoom_last_size=(top.width(),top.height())
                for widget in [top,*top.findChildren(QWidget)]:
                    current=widget.styleSheet()
                    if current!=getattr(widget,'_zoom_last_style',None):widget._zoom_base_style=current
                    updated=scale_style(getattr(widget,'_zoom_base_style',current),zoom)
                    if current!=updated:widget.setStyleSheet(updated)
                    widget._zoom_last_style=updated
                    layout=widget.layout()
                    if layout:
                        if not hasattr(layout,'_zoom_margins'):
                            m=layout.contentsMargins();layout._zoom_margins=(m.left(),m.top(),m.right(),m.bottom())
                            layout._zoom_spacing=layout.spacing()
                        layout.setContentsMargins(*(round(n*zoom) for n in layout._zoom_margins))
                        if layout._zoom_spacing>=0:layout.setSpacing(round(layout._zoom_spacing*zoom))
                    if isinstance(widget,QAbstractItemView) and hasattr(widget,'verticalHeader'):
                        header=widget.verticalHeader()
                        if not hasattr(header,'_zoom_section'):header._zoom_section=header.defaultSectionSize()
                        header.setDefaultSectionSize(round(header._zoom_section*zoom))
                    if isinstance(widget,QAbstractItemView) and hasattr(widget,'horizontalHeader'):
                        header=widget.horizontalHeader()
                        widths=[header.sectionSize(n) for n in range(header.count())]
                        if not hasattr(header,'_zoom_widths') or len(header._zoom_widths)!=len(widths):
                            header._zoom_widths=widths;header._zoom_last_widths=widths
                        for n,width in enumerate(widths):
                            if header.isSectionHidden(n):continue
                            if width!=header._zoom_last_widths[n]:header._zoom_widths[n]=width/previous
                            header.resizeSection(n,round(header._zoom_widths[n]*zoom))
                        header._zoom_last_widths=[header.sectionSize(n) for n in range(header.count())]
            self.window.sidebar.setFixedWidth(scaled(220))
            from keyword_cards import KeywordBoard
            for board in self.window.findChildren(KeywordBoard):board.reflow()
            for page in (self.window.results,self.window.favorites):
                for card in page.findChildren(QWidget):
                    if hasattr(card,'_base_card_height'):card.setFixedHeight(scaled(card._base_card_height))
                    if card.objectName()=='ArticleCard':
                        card.setMinimumWidth(scaled(240 if card._base_card_height==280 else 300))
                if hasattr(page,'reflow_cards'):page.reflow_cards()
                if hasattr(page,'reflow_favorite_cards'):page.reflow_favorite_cards()
            if previous!=zoom:self.changed.emit(zoom)
        finally:self.busy=False
