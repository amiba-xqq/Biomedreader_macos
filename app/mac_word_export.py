"""Mac document interoperability; does not install the Windows COM add-in."""
import json,os,tempfile
from pathlib import Path
from PySide6.QtCore import Qt,Slot,QThreadPool
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QListWidget,QListWidgetItem,QComboBox,QCompleter,QPushButton,QMessageBox
from path_dialog import QFileDialog
from citations import ROOT
from bibliography import enrich_articles
from word_document import document_bytes

class MacWordExportDialog(QDialog):
    def __init__(self,database,articles,selected=(),parent=None):
        super().__init__(parent);self.database=database;self.articles=articles;self.task=None
        self.setWindowTitle('导出 Word / EndNote 引文文档');self.resize(820,590)
        layout=QVBoxLayout(self)
        note=QLabel('选择文章，导出包含 EndNote 引文域、嵌入文献记录和文末参考文献的 .docx 文件。\n'
            '用 Mac Word 打开后，使用已安装的 EndNote CWYW 更新引用；可复制引文域到稿件。此功能不安装原生 Mac Word 工具栏。')
        note.setWordWrap(True);layout.addWidget(note)
        self.list=QListWidget()
        for index,article in enumerate(articles):
            item=QListWidgetItem(article.get('title') or '无标题');item.setData(Qt.ItemDataRole.UserRole,index)
            item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if not selected or article['article_key'] in selected else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        layout.addWidget(self.list,1)
        row=QHBoxLayout();all_button=QPushButton('全选');none=QPushButton('全不选')
        all_button.clicked.connect(lambda:self.check_all(True));none.clicked.connect(lambda:self.check_all(False))
        row.addWidget(all_button);row.addWidget(none);row.addStretch();layout.addLayout(row)
        layout.addWidget(QLabel('参考文献样式（NC 扩选 ∩ 已核实 EndNote 样式）：'))
        self.style=QComboBox();self.style.setEditable(True);self.style.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for title in json.loads((ROOT/'word_catalogue.json').read_text(encoding='utf8')):self.style.addItem(title)
        self.style.completer().setFilterMode(Qt.MatchFlag.MatchContains);self.style.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.style.setCurrentIndex(max(0,self.style.findText(database.get_settings().get('citation_journal','Cell'))));layout.addWidget(self.style)
        self.status=QLabel('字体为 Arial、常规、10.5 磅（五号）；上标格式跟随所选样式。');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.export=QPushButton('导出 .docx…');self.export.clicked.connect(self.start);layout.addWidget(self.export)
        close=QPushButton('关闭');close.clicked.connect(self.reject);layout.addWidget(close)
    def check_all(self,value):
        for index in range(self.list.count()):self.list.item(index).setCheckState(Qt.CheckState.Checked if value else Qt.CheckState.Unchecked)
    def start(self):
        if self.task:return
        rows=[self.articles[self.list.item(i).data(Qt.ItemDataRole.UserRole)] for i in range(self.list.count()) if self.list.item(i).checkState()==Qt.CheckState.Checked]
        if not rows:QMessageBox.warning(self,'未选择文章','请至少选择一篇文章。');return
        journal=self.style.currentText()
        if journal not in json.loads((ROOT/'word_catalogue.json').read_text(encoding='utf8')):
            QMessageBox.warning(self,'请选择期刊','请从匹配的下拉列表中选择一个期刊。');return
        destination,_=QFileDialog.getSaveFileName(self,'导出 Word / EndNote 文档','BiomedReader_EndNote.docx','Word 文档 (*.docx)')
        if not destination:return
        target=Path(destination)
        if target.suffix.lower()!='.docx':target=target.with_suffix('.docx')
        from ui import BackgroundOperation
        def build(progress):
            enriched,warnings=enrich_articles(self.database,rows)
            payload=document_bytes(enriched,journal)
            with tempfile.NamedTemporaryFile(dir=target.parent,suffix='.tmp',delete=False) as stream:
                temporary=Path(stream.name);stream.write(payload)
            try:os.replace(temporary,target)
            finally:temporary.unlink(missing_ok=True)
            return str(target),warnings
        self.export.setEnabled(False);self.status.setText('正在补齐引用信息并生成 Word 引文文档……')
        self.task=BackgroundOperation(build);self.task.signals.finished.connect(self.finished_export);self.task.signals.failed.connect(self.failed_export)
        QThreadPool.globalInstance().start(self.task)
    @Slot(object)
    def finished_export(self,result):
        path,warnings=result;self.task=None;self.export.setEnabled(True)
        self.status.setText('已导出：'+path+('。部分卷页仍缺失，可稍后在文章详情重新补全。' if warnings else ''))
        QMessageBox.information(self,'Word 文档已导出','已保存引文域、文献记录及文末参考文献。\n在 Mac Word 中打开后，通过 EndNote → Update Citations and Bibliography 接管格式化。')
    @Slot(str)
    def failed_export(self,message):
        self.task=None;self.export.setEnabled(True);self.status.setText('导出失败：'+message)
    def reject(self):
        if self.task:self.status.setText('正在生成文档，请等待完成后关闭。');return
        super().reject()

def show_word_export(database,articles,selected,parent):
    if not articles:QMessageBox.information(parent,'没有可导出的文章','请先收藏文章。');return
    MacWordExportDialog(database,articles,selected,parent).exec()
