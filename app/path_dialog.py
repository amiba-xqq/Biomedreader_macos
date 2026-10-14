"""Light Qt file dialogs with an editable Look in directory selector."""
import os
from pathlib import Path
from PySide6.QtCore import QEvent,Qt
from PySide6.QtWidgets import QFileDialog as QtFileDialog,QComboBox,QLabel


class QFileDialog(QtFileDialog):
    def __init__(self,parent=None,caption='',directory='',filter=''):
        super().__init__(parent,caption,directory,filter)
        self.setOption(self.Option.DontUseNativeDialog,True)
        self.path_hint=QLabel('位置栏支持输入或粘贴文件夹路径，按 Enter 进入。',self)
        self.path_hint.setWordWrap(True)
        self.layout().addWidget(self.path_hint,self.layout().rowCount(),0,1,self.layout().columnCount())
        self.enable_path_entry()

    def enable_path_entry(self):
        self.location_box=self.findChild(QComboBox,'lookInCombo')
        if self.location_box:
            self.location_box.setEditable(True)
            self.location_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            self.location_box.lineEdit().setPlaceholderText('输入或粘贴文件夹路径，然后按 Enter')
            self.location_box.lineEdit().installEventFilter(self)

    def showEvent(self,event):
        super().showEvent(event)
        self.enable_path_entry()

    def eventFilter(self,obj,event):
        if getattr(self,'location_box',None) and obj is self.location_box.lineEdit() and event.type()==QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter):
                self.enter_location(obj.text());return True
        return super().eventFilter(obj,event)

    def enter_location(self,text):
        path=Path(os.path.expandvars(os.path.expanduser(text.strip().strip('"'))))
        if not path.is_absolute():path=Path(self.directory().absolutePath())/path
        if not path.is_dir():
            self.path_hint.setText('路径不存在或不是文件夹，请检查后重新输入。');return False
        self.setDirectory(str(path.resolve()))
        self.path_hint.setText('已进入：'+str(path.resolve()))
        return True

    @classmethod
    def getOpenFileName(cls,parent=None,caption='',dir='',filter='',selectedFilter='',options=QtFileDialog.Option(0)):
        return cls._choose(parent,caption,dir,filter,False,False,options,selectedFilter)

    @classmethod
    def getSaveFileName(cls,parent=None,caption='',dir='',filter='',selectedFilter='',options=QtFileDialog.Option(0)):
        return cls._choose(parent,caption,dir,filter,True,False,options,selectedFilter)

    @classmethod
    def getExistingDirectory(cls,parent=None,caption='',dir='',options=QtFileDialog.Option.ShowDirsOnly):
        return cls._choose(parent,caption,dir,'',False,True,options,'')[0]

    @classmethod
    def _choose(cls,parent,caption,directory,filter,saving,folders,options,selected_filter):
        dialog=cls(parent,caption)
        dialog.setOptions(options|cls.Option.DontUseNativeDialog)
        dialog.setAcceptMode(cls.AcceptMode.AcceptSave if saving else cls.AcceptMode.AcceptOpen)
        dialog.setFileMode(cls.FileMode.Directory if folders else cls.FileMode.AnyFile if saving else cls.FileMode.ExistingFile)
        if folders:dialog.setOption(cls.Option.ShowDirsOnly,True)
        if filter:dialog.setNameFilter(filter)
        if selected_filter:dialog.selectNameFilter(selected_filter)
        if directory:
            path=Path(directory)
            if path.is_dir():dialog.setDirectory(str(path))
            else:
                if path.parent.is_dir():dialog.setDirectory(str(path.parent.resolve()))
                dialog.selectFile(path.name)
        dialog.enable_path_entry()
        accepted=dialog.exec()==cls.DialogCode.Accepted
        files=dialog.selectedFiles() if accepted else []
        result=(files[0] if files else '',dialog.selectedNameFilter() if accepted else '')
        dialog.deleteLater()
        return result
