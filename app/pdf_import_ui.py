"""Batch PDF selection and background task support."""
from pathlib import Path
import json
import threading
from PySide6.QtCore import QObject, QRunnable, Signal, Qt
from PySide6.QtWidgets import (QApplication,QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QListWidget,QListWidgetItem,QComboBox,QLineEdit,QCheckBox,QDialogButtonBox,QMessageBox,
    QTableWidget,QTableWidgetItem,QAbstractItemView,QHeaderView)
from path_dialog import QFileDialog
from pdf_import import import_pdfs


class PDFDropList(QListWidget):
    def __init__(self):
        super().__init__();self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setMinimumHeight(180)
    def add_paths(self,paths):
        existing={self.item(i).data(Qt.ItemDataRole.UserRole).casefold() for i in range(self.count())}
        invalid=[]
        for value in paths:
            path=Path(value)
            if path.suffix.lower()!='.pdf' or not path.is_file():invalid.append(str(path));continue
            name=str(path.resolve())
            if name.casefold() in existing:continue
            item=QListWidgetItem(path.name+'  —  '+str(path.parent));item.setData(Qt.ItemDataRole.UserRole,name);item.setToolTip(name)
            self.addItem(item);existing.add(name.casefold())
        if invalid:QMessageBox.warning(self,'未添加的文件','仅支持已存在的本地 PDF 文件：\n'+'\n'.join(invalid[:10]))
    def dragEnterEvent(self,event):
        if event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):event.acceptProposedAction()
        else:event.ignore()
    def dragMoveEvent(self,event):self.dragEnterEvent(event)
    def dropEvent(self,event):
        self.add_paths([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]);event.acceptProposedAction()
    def paths(self):return [self.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.count())]


class PDFImportDialog(QDialog):
    def __init__(self,database,parent=None):
        # No native owner: minimizing the main window must not hide this drop target.
        super().__init__(None,Qt.WindowType.Window|Qt.WindowType.WindowMinMaxButtonsHint|Qt.WindowType.WindowCloseButtonHint)
        self.setWindowModality(Qt.WindowModality.NonModal)
        if parent is not None:self.setFont(parent.font())
        self.database=database;self.selection=None
        self.setWindowTitle('批量收藏 PDF');self.resize(850,690)
        layout=QVBoxLayout(self)
        instructions=QLabel('添加或拖入一个或多个 PDF。读取首页完整标题，通过 PubMed 搜索并收藏，可选择是否复制并链接 PDF。\n'
            '不限制年份与 JIF；PubMed 检索范围跟随设置。必须选择期刊目录和目标分组。')
        instructions.setWordWrap(True);layout.addWidget(instructions)
        row=QHBoxLayout();add=QPushButton('选择 PDF 文件…');remove=QPushButton('移除选中项')
        row.addWidget(add);row.addWidget(remove);row.addStretch();layout.addLayout(row)
        self.files=PDFDropList();layout.addWidget(QLabel('将 PDF 拖放到下面的选择框内：'));layout.addWidget(self.files,2)
        add.clicked.connect(self.choose_files)
        remove.clicked.connect(lambda:[self.files.takeItem(self.files.row(item)) for item in self.files.selectedItems()])
        layout.addWidget(QLabel('期刊目录（必选，可多选，使用并集）：'))
        self.scopes=QListWidget();self.scopes.setMaximumHeight(145)
        for scope in database.list_scopes():
            item=QListWidgetItem(scope['name']+f"（{len(json.loads(scope['issns_json']))} 本）")
            item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable);item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole,scope['id']);self.scopes.addItem(item)
        layout.addWidget(self.scopes,1)
        layout.addWidget(QLabel('收藏到分组（必选）：'))
        self.group=QComboBox();self.group.addItem('请选择已有分组…',None)
        for group in database.list_custom_groups():self.group.addItem(group['name'],group['id'])
        layout.addWidget(self.group)
        self.new_group=QCheckBox('新建自定义分组');self.name=QLineEdit();self.name.setPlaceholderText('请输入新分组名称（不可重复）');self.name.setEnabled(False)
        self.new_group.toggled.connect(lambda value:(self.name.setEnabled(value),self.group.setEnabled(not value)))
        layout.addWidget(self.new_group);layout.addWidget(self.name)
        self.link_pdf=QComboBox();self.link_pdf.addItem('链接 PDF（默认）',True);self.link_pdf.addItem('不链接 PDF，仅收藏文章信息',False)
        layout.addWidget(QLabel('是否链接 PDF（必选）：'));layout.addWidget(self.link_pdf)
        note=QLabel('不链接时不复制本次选择的 PDF。文章若已经有关联原文，原链接保留。');note.setWordWrap(True);layout.addWidget(note)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('开始识别与收藏')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.validate);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
    def choose_files(self):
        paths,_=QFileDialog.getOpenFileNames(self,'选择本地 PDF 文件','','PDF 文件 (*.pdf)')
        self.files.add_paths(paths)
    def validate(self):
        try:
            paths=self.files.paths()
            if not paths:raise ValueError('请先添加至少一个 PDF 文件。')
            scopes=[self.scopes.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.scopes.count()) if self.scopes.item(i).checkState()==Qt.CheckState.Checked]
            if not scopes:raise ValueError('必须勾选至少一个期刊目录。')
            issns=self.database.scope_union(scopes)
            if not issns:raise ValueError('所选期刊目录没有杂志，无法开始检索。')
            if self.new_group.isChecked():
                name=self.name.text().strip()
                if not name or name=='未分组':raise ValueError('请输入有效的新分组名称。')
                if any(g['name'].casefold()==name.casefold() for g in self.database.list_custom_groups()):raise ValueError('该分组名称已存在，请选择已有分组或更换名称。')
                group_id,name=self.database.create_favorite_group(name,'PDF 批量收藏')
            else:
                group_id=self.group.currentData();name=self.group.currentText()
                if group_id is None:raise ValueError('必须选择一个已有分组，或创建新分组。')
            selected=set(issns)
            aliases={alias for journal in self.database.list_journals() if journal['issn'] in selected
                for alias in {journal['issn'],*(journal.get('aliases') or '').split(';')} if alias}
            self.selection=dict(files=paths,group_id=group_id,group_name=name,issns=sorted(selected|aliases),
                scope=self.database.get_settings().get('pubmed_search_scope','default'),link_pdf=bool(self.link_pdf.currentData()))
        except Exception as error:
            QMessageBox.warning(self,'无法开始',str(error));return
        self.accept()


class PDFImportSignals(QObject):
    progress=Signal(object)
    finished=Signal(object)


class PDFImportTask(QRunnable):
    def __init__(self,database,selection):
        super().__init__();self.database=database;self.selection=selection
        self.signals=PDFImportSignals();self.stop=threading.Event()
    def run(self):
        try:
            options={key:self.selection[key] for key in ('files','group_id','issns','scope','link_pdf')}
            result=import_pdfs(self.database,**options,progress=self.signals.progress.emit,cancelled=self.stop.is_set)
            if result['cancelled']:
                completed={row['file'] for kind in ('successes','failures') for row in result[kind]}
                result['failures'].extend({'file':path,'reason':'已取消，尚未完成处理。'} for path in options['files'] if path not in completed)
        except Exception as error:
            result=dict(total=len(self.selection['files']),completed=0,successes=[],cancelled=False,
                failures=[{'file':path,'reason':str(error)} for path in self.selection['files']])
        self.signals.finished.emit(result)


class PDFFailureDialog(QDialog):
    def __init__(self,report,parent=None):
        super().__init__(parent);self.report=report;self.setWindowTitle('PDF 收藏结果 · 未成功文件');self.resize(900,460)
        layout=QVBoxLayout(self)
        label=QLabel(f"共 {report['total']} 个 PDF，成功 {len(report['successes'])} 个，未成功 {len(report['failures'])} 个。\n成功结果已保存，可离线查看；本窗口需手动关闭。")
        layout.addWidget(label)
        table=QTableWidget(len(report['failures']),2);table.setHorizontalHeaderLabels(['PDF 文件','未成功原因'])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch);table.setColumnWidth(0,380)
        for row,failure in enumerate(report['failures']):
            item=QTableWidgetItem(Path(failure['file']).name);item.setToolTip(failure['file']);table.setItem(row,0,item);table.setItem(row,1,QTableWidgetItem(failure['reason']))
        layout.addWidget(table,1)
        buttons=QHBoxLayout();copy=QPushButton('复制未成功的 PDF 文件名');export=QPushButton('导出文件名单…');close=QPushButton('关闭')
        copy.clicked.connect(self.copy_names);export.clicked.connect(self.export_names);close.clicked.connect(self.accept)
        buttons.addWidget(copy);buttons.addWidget(export);buttons.addStretch();buttons.addWidget(close);layout.addLayout(buttons)
        self.status=QLabel();layout.addWidget(self.status)
    def names(self):return '\n'.join(Path(row['file']).name for row in self.report['failures'])
    def copy_names(self):
        QApplication.clipboard().setText(self.names());self.status.setText('文件名称已复制，可以粘贴。窗口将保持打开。')
    def export_names(self):
        path,_=QFileDialog.getSaveFileName(self,'导出未成功文件名单','未成功收藏的PDF.txt','文本文件 (*.txt)')
        if path:
            try:Path(path).write_text(self.names()+'\n',encoding='utf-8-sig');self.status.setText('文件名单已导出。')
            except OSError as error:QMessageBox.warning(self,'导出失败',str(error))
