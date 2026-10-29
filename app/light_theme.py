"""An application-owned light palette, independent of Windows personalization."""
import os
import ctypes
import weakref
from PySide6.QtCore import QObject,QEvent,QTimer,Qt
from PySide6.QtGui import QPalette,QColor
from PySide6.QtWidgets import QApplication,QWidget,QDialog,QMainWindow


def light_palette():
    palette=QPalette()
    values=dict(Window='#F7F8FA',WindowText='#30251D',Base='#FFFFFF',AlternateBase='#FFF3E6',
        ToolTipBase='#FFFFDC',ToolTipText='#30251D',Text='#30251D',Button='#EFEFEF',
        ButtonText='#30251D',BrightText='#FFFFFF',Light='#FFFFFF',Midlight='#F7F7F7',
        Dark='#9F9F9F',Mid='#B8B8B8',Shadow='#767676',Highlight='#E88225',
        HighlightedText='#FFFFFF',Link='#C96812',LinkVisited='#9B6B4B',PlaceholderText='#8B7768',Accent='#F28C28')
    for group in (QPalette.ColorGroup.Active,QPalette.ColorGroup.Inactive,QPalette.ColorGroup.Disabled):
        for name,color in values.items():palette.setColor(group,getattr(QPalette.ColorRole,name),QColor(color))
    for name in ('Text','WindowText','ButtonText'):
        palette.setColor(QPalette.ColorGroup.Disabled,getattr(QPalette.ColorRole,name),QColor('#9F9389'))
    return palette


class LightTheme(QObject):
    def __init__(self,application):
        super().__init__(application)
        self.application=application;self.busy=False;self.native_busy=False;self.pending=False
        # Do not install a Python application-wide event filter: Chromium can
        # dispatch events for native objects while they are being destroyed.
        # Palette/color-scheme signals cover theme changes on every platform.
        self.watched_windows=weakref.WeakSet()
        if os.name=='nt':
            self.window_scan=QTimer(self);self.window_scan.setInterval(400)
            self.window_scan.timeout.connect(self.watch_windows);self.window_scan.start()
        application.paletteChanged.connect(self.enforce)
        application.styleHints().colorSchemeChanged.connect(self.enforce)
        self.enforce()

    def watch_windows(self):
        for widget in self.application.topLevelWidgets():
            if isinstance(widget,(QDialog,QMainWindow)) and widget not in self.watched_windows:
                widget.installEventFilter(self);self.watched_windows.add(widget);self.title_bar(widget)

    def enforce(self,*_):
        if self.busy:return
        self.busy=True
        try:
            hints=self.application.styleHints()
            if hints.colorScheme()!=Qt.ColorScheme.Light:hints.setColorScheme(Qt.ColorScheme.Light)
            desired=light_palette()
            if self.application.palette()!=desired:self.application.setPalette(desired)
        finally:self.busy=False

    def title_bar(self,widget):
        if os.name!='nt' or self.native_busy or self.application.platformName()=='offscreen':return
        self.native_busy=True
        try:
            handle=int(widget.winId());disabled=ctypes.c_int(0)
            for attribute in (20,19):
                result=ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(handle),attribute,
                    ctypes.byref(disabled),ctypes.sizeof(disabled))
                if result==0:break
        except (OSError,AttributeError,RuntimeError):pass
        finally:self.native_busy=False

    def eventFilter(self,watched,event):
        if event.type() in (QEvent.Type.ThemeChange,QEvent.Type.ApplicationPaletteChange):
            if not self.pending and not self.busy:
                self.pending=True
                QTimer.singleShot(0,self.refresh_windows)
        if event.type() in (QEvent.Type.Show,QEvent.Type.WinIdChange) and isinstance(watched,QWidget) and watched.isWindow():
            self.title_bar(watched)
        return False

    def refresh_windows(self):
        self.pending=False;self.enforce()
        for widget in self.application.topLevelWidgets():self.title_bar(widget)


def configure_light_theme(application):
    # Qt file/font/color dialogs use this palette instead of Windows dark dialogs.
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs,True)
    application.setStyle('Fusion')
    if not hasattr(application,'_light_theme_controller'):
        application._light_theme_controller=LightTheme(application)
    else:application._light_theme_controller.enforce()
    return application._light_theme_controller
