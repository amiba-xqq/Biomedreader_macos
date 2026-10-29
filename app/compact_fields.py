"""Font-aware field widths, without changing editing or wheel behavior."""
from PySide6.QtCore import QObject,QEvent
from PySide6.QtWidgets import QComboBox,QAbstractSpinBox,QFormLayout,QSizePolicy

class CompactFields(QObject):
    def __init__(self,parent,controls):
        super().__init__(parent);self.controls=controls
        for control in controls:
            if isinstance(control,QComboBox):
                control.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                control.setMinimumContentsLength(16)
            control.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Fixed)
            control.installEventFilter(self);self.adjust(control)
    def adjust(self,control):
        characters=14 if isinstance(control,QAbstractSpinBox) else 28
        control.setMaximumWidth(max(180,int(control.fontMetrics().horizontalAdvance('M')*characters+40)))
    def eventFilter(self,control,event):
        if event.type()==QEvent.Type.FontChange:self.adjust(control)
        return False

def compact_settings(page,body):
    body.setObjectName('CompactSettingsBody')
    body.setStyleSheet('QWidget#CompactSettingsBody QComboBox, QWidget#CompactSettingsBody QAbstractSpinBox {padding:5px 8px;}')
    controls=body.findChildren(QComboBox)+body.findChildren(QAbstractSpinBox)
    page.compact_fields=CompactFields(page,controls)
    for form in body.findChildren(QFormLayout):form.setVerticalSpacing(8);form.setHorizontalSpacing(14)
