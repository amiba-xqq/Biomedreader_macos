"""Per-file sample and target aliases for qPCR projects."""
from PySide6.QtCore import QSignalBlocker,Qt
from PySide6.QtGui import QBrush,QColor
from PySide6.QtWidgets import (QDialog,QDialogButtonBox,QFormLayout,QHeaderView,QLabel,QLineEdit,QMessageBox,
                               QStyledItemDelegate,QTabWidget,QTableWidget,QTableWidgetItem,QVBoxLayout,QWidget)

from figure_data import qpcr_keep,qpcr_mapping_entry,qpcr_mapping_issues,qpcr_name,qpcr_required

KINDS = ('samples','genes')
REQUIRED = ('control','reference')


class MappingNameDelegate(QStyledItemDelegate):
    def createEditor(self,parent,option,index):
        editor = QLineEdit(parent)
        editor.setFont(option.font)
        editor.setMaxLength(120)
        editor.setStyleSheet('QLineEdit {padding:1px 7px;min-height:0px;}')
        return editor

    def updateEditorGeometry(self,editor,option,index):
        editor.setGeometry(option.rect.adjusted(4,3,-4,-3))


class MappingDialog(QDialog):
    def __init__(self,parent,files,options):
        super().__init__(parent)
        self.setWindowTitle('qPCR 样本与基因映射')
        self.resize(790,580)
        self.files,self.result_settings = files,None
        self.tables,self.required = {},{}
        outer = QVBoxLayout(self)
        instructions = QLabel('每个素材单独设置。双击映射名称可编辑，留空保留原名。各素材保留的最终名称须一致。')
        instructions.setWordWrap(True)
        outer.addWidget(instructions)
        form = QFormLayout()
        for kind,key,title in zip(KINDS,REQUIRED,('对照组最终名称','内参基因最终名称')):
            entry = QLineEdit(qpcr_required(options,kind))
            self.required[key] = entry
            form.addRow(title,entry)
        outer.addLayout(form)
        self.files_tabs = QTabWidget()
        outer.addWidget(self.files_tabs,1)
        self.tab_by_file = {}
        for file_id,filename,dimensions in files:
            tabs = QTabWidget()
            index = self.files_tabs.addTab(tabs,filename)
            self.tab_by_file[file_id] = index,filename,tabs
            for kind,caption,names in zip(KINDS,('样本','基因'),dimensions):
                table = QTableWidget(len(names),3)
                table.setHorizontalHeaderLabels(['保留','原始名称','映射名称'])
                table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents)
                table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
                table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch)
                table.verticalHeader().setDefaultSectionSize(max(38,table.fontMetrics().lineSpacing()+22))
                table.verticalHeader().hide()
                table.setItemDelegateForColumn(2,MappingNameDelegate(table))
                for row,name in enumerate(names):
                    config = qpcr_mapping_entry(options,kind,name,file_id)
                    include = QTableWidgetItem()
                    include.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsUserCheckable)
                    include.setCheckState(Qt.CheckState.Checked if config.get('keep',True) else Qt.CheckState.Unchecked)
                    original = QTableWidgetItem(name)
                    original.setFlags(original.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    table.setItem(row,0,include)
                    table.setItem(row,1,original)
                    table.setItem(row,2,QTableWidgetItem(config.get('name','')))
                self.tables[file_id,kind] = table
                tabs.addTab(table,caption)
                table.itemChanged.connect(self.validate)
        self.message = QLabel()
        self.message.setWordWrap(True)
        outer.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.finish)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        for entry in self.required.values():
            entry.textChanged.connect(self.validate)
        self.validate()

    def settings(self):
        mapping = {file_id:{} for file_id,_,_ in self.files}
        result = {key:entry.text().strip() for key,entry in self.required.items()}
        for (file_id,kind),table in self.tables.items():
            mapping[file_id][kind] = {
                table.item(row,1).text():dict(name=table.item(row,2).text().strip(),
                    keep=table.item(row,0).checkState()==Qt.CheckState.Checked)
                for row in range(table.rowCount())
            }
        return dict(**result,file_mappings=mapping,name_mapping={})

    def validate(self,*_):
        settings = self.settings()
        issues = qpcr_mapping_issues(self.files,settings)
        notes = []
        for file_id,(index,filename,tabs) in self.tab_by_file.items():
            file_notes = []
            for tab,kind in enumerate(KINDS):
                table = self.tables[file_id,kind]
                flagged,missing,duplicates = issues.get((file_id,kind),(set(),set(),set()))
                caption = ('样本','基因')[tab]
                tabs.setTabText(tab,caption+(' · 有差异' if flagged or missing else ''))
                if missing or duplicates:
                    details = []
                    if missing:
                        details.append('缺少 '+'、'.join(sorted(missing)))
                    if duplicates:
                        details.append('重复映射 '+'、'.join(sorted(duplicates)))
                    file_notes.append(caption+' '+ '；'.join(details))
                with QSignalBlocker(table):
                    for row in range(table.rowCount()):
                        original = table.item(row,1).text()
                        required = qpcr_name(settings,kind,original,file_id)==qpcr_required(settings,kind)
                        check = table.item(row,0)
                        if required:
                            check.setCheckState(Qt.CheckState.Checked)
                        check.setFlags(Qt.ItemFlag.ItemIsEnabled if required else
                                       Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsUserCheckable)
                        for column in (1,2):
                            item = table.item(row,column)
                            item.setForeground(QBrush(QColor('#B42318')) if original in flagged else QBrush())
                            item.setToolTip('此最终名称在其它素材中缺失，或在同一素材中重复。' if original in flagged else '')
            self.files_tabs.setTabText(index,filename+(' · 有差异' if any((file_id,k) in issues for k in KINDS) else ''))
            self.files_tabs.setTabToolTip(index,'；'.join(file_notes))
            if file_notes:
                notes.append(filename+'　'+'；'.join(file_notes))
        self.message.setText('\n'.join(notes[:4]) if issues else '各素材的样本和基因名称一致。')
        self.message.setToolTip('\n'.join(notes))
        return issues

    def warn_mismatch(self):
        if self.validate():
            QMessageBox.warning(self,'qPCR 映射不一致','各素材保留的样本或基因名称不一致，或缺少对照组、内参。请查看标红的行和各素材的缺失提示。')

    def finish(self):
        result = self.settings()
        if not all(result[key] for key in REQUIRED):
            self.message.setText('请填写对照组和内参基因的最终名称。')
            return
        if self.validate():
            self.warn_mismatch()
            return
        self.result_settings = self.settings()
        self.accept()


def remap_pairs(pairs,files,old,new):
    aliases = {}
    for file_id,_,(names,_) in files:
        for name in names:
            if qpcr_keep(new,'samples',name,file_id):
                before = qpcr_name(old,'samples',name,file_id)
                after = qpcr_name(new,'samples',name,file_id)
                aliases.setdefault(before,set()).add(after)
    result = []
    for first,second in pairs:
        a,b = aliases.get(first,()),aliases.get(second,())
        if len(a)==len(b)==1:
            pair = [next(iter(a)),next(iter(b))]
            if pair[0]!=pair[1] and pair not in result and pair[::-1] not in result:
                result.append(pair)
    return result
