"""Wheel input scrolls the settings page, never silently changes a parameter."""
from PySide6.QtCore import QObject,QEvent
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication,QComboBox,QAbstractSpinBox

class ClickOnlyComboGuard(QObject):
    def __init__(self,combo):
        super().__init__(combo);combo.installEventFilter(self)
        if combo.isEditable():combo.lineEdit().installEventFilter(self)
    def eventFilter(self,watched,event):
        if event.type()==QEvent.Type.Wheel:event.ignore();return True
        return False

class SettingsWheelGuard(QObject):
    def __init__(self,page,scroll):
        super().__init__(page);self.page=page;self.scroll=scroll
        for control in page.findChildren(QComboBox)+page.findChildren(QAbstractSpinBox):
            control.installEventFilter(self)
            editor=control.lineEdit() if hasattr(control,'lineEdit') else None
            if editor is not None:editor.installEventFilter(self)

    def eventFilter(self,watched,event):
        if event.type()!=QEvent.Type.Wheel or not self.page.isAncestorOf(watched):return False
        # A copy preserves high-resolution trackpad deltas and scroll phases.
        # The scroll viewport consumes it; the original never reaches the editor.
        forwarded=QWheelEvent(event);forwarded.ignore()
        QApplication.sendEvent(self.scroll.viewport(),forwarded)
        event.accept();return True
