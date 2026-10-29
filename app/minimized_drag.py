"""Give minimizable Qt windows normal independent Windows taskbar entries."""
import sys,weakref,logging
from PySide6.QtCore import QObject,QEvent,QTimer,Qt
from PySide6.QtWidgets import QWidget

def drag_position(origin,initial,current):
    # Compatibility for old diagnostic fixtures; compact captions are gone.
    return origin[0]+current[0]-initial[0],origin[1]+current[1]-initial[1]

class TaskbarWindowFilter(QObject):
    def __init__(self,application):
        super().__init__(application);self.applying=False
        import ctypes
        from ctypes import wintypes
        self.ctypes=ctypes;self.user=ctypes.WinDLL('user32',use_last_error=True)
        self.get=self.user.GetWindowLongPtrW;self.get.argtypes=[wintypes.HWND,ctypes.c_int];self.get.restype=ctypes.c_ssize_t
        self.set=self.user.SetWindowLongPtrW;self.set.argtypes=[wintypes.HWND,ctypes.c_int,ctypes.c_ssize_t];self.set.restype=ctypes.c_ssize_t
        self.user.SetWindowPos.argtypes=[wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT];self.user.SetWindowPos.restype=wintypes.BOOL
        self.user.IsWindow.argtypes=[wintypes.HWND];self.user.IsWindow.restype=wintypes.BOOL
    def apply(self,widget):
        import shiboken6
        if self.applying or widget is None or not shiboken6.isValid(widget) or not widget.isWindow():return
        if not widget.windowFlags()&Qt.WindowType.WindowMinimizeButtonHint:return
        self.applying=True
        try:
            # winId() would CREATE a native window while a dialog is closing.
            # Only use an already-existing handle; never resurrect a tiny window.
            hwnd=int(widget.effectiveWinId() or 0)
            if not hwnd or not self.user.IsWindow(hwnd):return
            handle=widget.windowHandle()
            if handle is not None and handle.transientParent() is not None:handle.setTransientParent(None)
            hwnd=int(widget.effectiveWinId() or 0)
            if not hwnd or not self.user.IsWindow(hwnd):return
            style=self.get(hwnd,-20);wanted=(style|0x40000)&~0x80;changed=False
            if style!=wanted:self._set(hwnd,-20,wanted);changed=True
            # Keep QObject parent/lifetime, remove only native window ownership.
            if self.get(hwnd,-8):self._set(hwnd,-8,0);changed=True
            if changed:self.user.SetWindowPos(hwnd,None,0,0,0,0,0x0037)
        except OSError:logging.getLogger(__name__).warning('Unable to apply taskbar window style',exc_info=True)
        finally:self.applying=False
    def _set(self,hwnd,index,value):
        self.ctypes.set_last_error(0);result=self.set(hwnd,index,value)
        if not result and self.ctypes.get_last_error():raise self.ctypes.WinError(self.ctypes.get_last_error())
    def eventFilter(self,watched,event):
        if not self.applying and isinstance(watched,QWidget) and watched.isWindow() and event.type() in (QEvent.Type.Show,QEvent.Type.WinIdChange,QEvent.Type.WindowStateChange):
            self.apply(watched)
            # Qt can finish native owner assignment after its Show event.
            reference=weakref.ref(watched);QTimer.singleShot(0,lambda:self.apply(reference()))
        return False

def install_minimized_drag(application):
    # Compatibility import; only normal taskbar behavior is installed now.
    if sys.platform=='win32':
        application.taskbar_window_filter=TaskbarWindowFilter(application)
        application.installEventFilter(application.taskbar_window_filter)
