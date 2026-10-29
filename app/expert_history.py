from PySide6.QtWidgets import QDialog,QVBoxLayout
from PySide6.QtCore import QTimer
from ai_workflow_ui import HistoryPane,window_flags,show_window
def open_history(manager,kind,dialog_class,parent):
    dialog=QDialog(parent);window_flags(dialog);dialog.setWindowTitle('AI reviewer · 历史记录');dialog.resize(1050,700);layout=QVBoxLayout(dialog)
    def show(record):
        view=dialog_class(manager,parent);view.restore_record(record);dialog.accept();QTimer.singleShot(0,lambda:show_window(view))
    dialog.history_pane=HistoryPane(manager,kind,show,dialog);layout.addWidget(dialog.history_pane);return show_window(dialog)
