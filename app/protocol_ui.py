"""Protocol drafting, editing, and sharing in AI Assistant."""
import json
import re
from pathlib import Path

from PySide6.QtCore import QEvent,QSignalBlocker,QTimer,Qt,QUrl
from PySide6.QtGui import QDesktopServices,QFont,QImage,QTextBlockFormat,QTextCursor,QTextDocument
from PySide6.QtWidgets import (
    QCheckBox,QDialog,QDialogButtonBox,QHBoxLayout,QInputDialog,QLabel,QLineEdit,
    QListWidget,QListWidgetItem,QMessageBox,QPlainTextEdit,QPushButton,QScrollArea,QSplitter,QStackedWidget,
    QTextBrowser,QTextEdit,QVBoxLayout,QWidget,
)

from ai_ui import plain_label,local_time
from ai_workflow_ui import AnalysisDialog,show_window,window_flags
from card_board import CardBoard
from path_dialog import QFileDialog
from protocol_documents import (display_title,export_docx,export_pdf,flow_image,import_protocols,
                                normalize_author,preview_html,share_protocols,without_local_references,write_protocol)
from protocol_workflow import draft_protocol,image_suffixes,protocol_attachment


def ask_author(parent,database):
    default = database.get_settings().get('protocol_export_author','')
    text,accepted = QInputDialog.getText(parent,'署名','输入用户 ID 或名称，将追加到导出的方案名称。',
                                        QLineEdit.EchoMode.Normal,default)
    if not accepted:
        return None
    try:
        author = normalize_author(text)
    except ValueError as error:
        QMessageBox.warning(parent,'署名无效',str(error))
        return None
    database.save_settings({'protocol_export_author':author})
    return author


class ProtocolPreview(QTextBrowser):
    def __init__(self):
        super().__init__()
        self.setStyleSheet('QTextBrowser {background:white;border:1px solid #DCE4E8;border-radius:12px;padding:24px;}')
        self.setOpenExternalLinks(False)
        self.result = None
        self.image = QImage()
        self.last_width = None
        self.updating = False
        self.resize_pending = False

    def present(self,result,image=None,keep_position=False):
        self.result = result
        if image is not None:
            self.image = image
        if self.updating:
            return
        self.updating = True
        self.last_width = max(1,self.viewport().width())
        bar = self.verticalScrollBar()
        position = bar.value()/max(1,bar.maximum()) if keep_position else 0
        dimensions = None
        document = QTextDocument(self)
        if not self.image.isNull():
            w,h = self.image.width(),self.image.height()
            scale = min(780/w,420/h,max(120,self.last_width-36)/w)
            dimensions = (max(1,round(w*scale)),max(1,round(h*scale)))
            document.addResource(QTextDocument.ResourceType.ImageResource,QUrl('protocol-flow'),self.image)
        try:
            document.setHtml(preview_html(result,dimensions))
            self.setDocument(document)
            bar.setValue(round(position*bar.maximum()))
        finally:
            self.updating = False

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if self.result and not self.updating and not self.resize_pending:
            self.resize_pending = True
            QTimer.singleShot(0,self,self.refresh_width)

    def refresh_width(self):
        self.resize_pending = False
        if self.result and self.last_width!=self.viewport().width():
            self.present(self.result,keep_position=True)


class FavoritePicker(QDialog):
    def __init__(self,database,selected,parent):
        super().__init__(parent)
        self.setWindowTitle('选择收藏文献')
        self.resize(760,590)
        layout = QVBoxLayout(self)
        query = QLineEdit()
        query.setPlaceholderText('按标题、期刊或 DOI 筛选收藏')
        layout.addWidget(query)
        self.list = QListWidget()
        layout.addWidget(self.list,1)
        for article in database.favorite_articles():
            item = QListWidgetItem(article.get('title','未命名文献'),self.list)
            item.setData(Qt.ItemDataRole.UserRole,article)
            item.setCheckState(Qt.CheckState.Checked if article['article_key'] in selected else Qt.CheckState.Unchecked)
        query.textChanged.connect(self.filter)
        standard = QDialogButtonBox.StandardButton
        buttons = QDialogButtonBox(standard.Ok|standard.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def filter(self,text):
        for i in range(self.list.count()):
            item = self.list.item(i)
            article = item.data(Qt.ItemDataRole.UserRole)
            haystack = ' '.join(str(article.get(k,'')) for k in ('title','journal_title','doi')).casefold()
            item.setHidden(text.strip().casefold() not in haystack)

    def articles(self):
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState()==Qt.CheckState.Checked]


class ProtocolDialog(AnalysisDialog):
    def __init__(self,manager,parent,record=None):
        super().__init__(manager,'AI Protocol · 实验方法',parent)
        self.resize(1160,870)
        self.setAcceptDrops(True)
        self.identifier = record['id'] if record else None
        self.built_at = record.get('created_at') if record else None
        inputs = record.get('inputs',{}) if record else {}
        self.files = list(dict.fromkeys(path for path in inputs.get('file_paths',[]) if isinstance(path,str)))
        database = manager.store.database
        self.articles = [article for key in inputs.get('article_keys',[])
                         if (article:=database.article_record(key))]
        self.export_task = None
        self.info.setText('从论文、收藏文献、图片或自拟方法编写中文 Protocol。选定资料会交由所选 AI 处理，补充文献通过 PubMed 检索。')
        self.run_button.setText('生成 AI Protocol')
        self.save_button.setText('保存 Protocol')
        self.source_panel = QWidget()
        sources = QVBoxLayout(self.source_panel)
        sources.setContentsMargins(0,0,0,0)
        self.method = QPlainTextEdit()
        self.method.setMinimumHeight(45)
        self.method.setPlaceholderText('指定要提取的具体方法、对应章节及样品。也可描述自己设计的方法、目的和已确定的步骤。')
        self.method.setPlainText(inputs.get('method',''))
        self.method.textChanged.connect(self.mark_dirty)
        sources.addWidget(self.method)
        self.follow_up = QPlainTextEdit()
        self.follow_up.setPlaceholderText('输入本轮的修改要求。也可以先添加新资料，再让 AI 修订当前正文。')
        self.follow_up.setPlainText(inputs.get('follow_up',''))
        self.follow_up.setMinimumHeight(45)
        self.follow_up.textChanged.connect(self.mark_dirty)
        sources.addWidget(self.follow_up)
        self.follow_up.hide()
        row = QHBoxLayout()
        for label,action in [('添加本地资料',self.pick_files),('选择收藏文献',self.pick_articles),('清除选择',self.clear_sources)]:
            button = QPushButton(label)
            button.clicked.connect(action)
            row.addWidget(button)
        row.addStretch()
        self.use_diagram = QCheckBox('使用设置中的 AI 绘图通道生成流程图')
        self.use_diagram.setChecked(inputs.get('with_diagram',True))
        self.use_diagram.toggled.connect(self.mark_dirty)
        row.addWidget(self.use_diagram)
        sources.addLayout(row)
        self.sources_note = plain_label('',True)
        sources.addWidget(self.sources_note)
        self.file_list = QListWidget()
        self.file_list.setMinimumHeight(42)
        self.file_list.itemDoubleClicked.connect(self.open_source_folder)
        self.file_list.setToolTip('双击已添加的本地资料，打开其所在文件夹。')
        sources.addWidget(self.file_list)
        old_output = self.output
        output_index = self.body_layout.indexOf(old_output)
        self.upper_panel = QWidget()
        upper_layout = QVBoxLayout(self.upper_panel)
        upper_layout.setContentsMargins(0,0,0,0)
        source_header = QWidget(self.upper_panel)
        header_layout = QVBoxLayout(source_header)
        header_layout.setContentsMargins(0,0,0,0)
        for _ in range(output_index):
            item = self.body_layout.takeAt(0)
            if item.widget():
                header_layout.addWidget(item.widget(),0,item.alignment())
            elif item.layout():
                item.layout().setParent(None)
                header_layout.addLayout(item.layout())
        upper_layout.addWidget(source_header)
        upper_layout.addWidget(self.source_panel)
        self.body_layout.removeWidget(old_output)
        old_output.deleteLater()
        self.views = QStackedWidget()
        self.preview = ProtocolPreview()
        self.preview.setPlaceholderText('生成后会在此显示 Protocol')
        self.views.addWidget(self.preview)
        edit_view = QWidget()
        results = QVBoxLayout(edit_view)
        results.setContentsMargins(0,0,0,0)
        self.title = QLineEdit()
        self.title.setPlaceholderText('Protocol 标题')
        results.addWidget(self.title)
        self.editor = QTextEdit()
        self.editor.setAcceptRichText(False)
        font = QFont()
        font.setFamilies(['Arial','Microsoft YaHei'])
        font.setPointSize(12)
        self.editor.document().setDefaultFont(font)
        self.editor.setPlaceholderText('生成后可直接编辑正文')
        results.addWidget(self.editor,1)
        self.views.addWidget(edit_view)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.addWidget(self.upper_panel)
        self.splitter.addWidget(self.views)
        self.splitter.setCollapsible(0,True)
        self.splitter.setCollapsible(1,False)
        self.splitter.setHandleWidth(8)
        self.splitter.setStyleSheet('QSplitter::handle {background:#E8D9CA;margin:2px 0;}')
        self.splitter.handle(1).setToolTip('向上拖到底可收起资料区，向下拖可恢复。')
        self.splitter.setStretchFactor(0,0)
        self.splitter.setStretchFactor(1,1)
        self.splitter.setSizes([210,485])
        self.body_layout.insertWidget(0,self.splitter,1)
        for view in (self.method,self.follow_up,self.preview,self.editor,self.file_list):
            view.viewport().setAcceptDrops(True)
            view.viewport().installEventFilter(self)
        self.title.textEdited.connect(self.mark_dirty)
        self.editor.textChanged.connect(self.mark_dirty)
        self.full = QPushButton('全屏正文')
        self.full.clicked.connect(self.toggle_full)
        self.switch = QPushButton('编辑正文')
        self.switch.clicked.connect(self.switch_view)
        self.bottom_layout.insertWidget(1,self.switch)
        self.bottom_layout.insertWidget(2,self.full)
        self.exports = []
        for i,(extension,exporter) in enumerate([('docx',export_docx),('pdf',export_pdf)],3):
            button = QPushButton('导出 '+('Word' if extension=='docx' else 'PDF'))
            button.clicked.connect(lambda checked=False,suffix=extension,write=exporter:self.export(suffix,write))
            self.bottom_layout.insertWidget(i,button)
            self.exports.append(button)
        self.refresh_sources()
        self.refresh_actions()
        if record:
            self.display(record['result'],saved=True)

    def refresh_sources(self):
        papers = '、'.join(row['title'] for row in self.articles)
        self.file_list.clear()
        for path in self.files:
            source = Path(path)
            item = QListWidgetItem(source.name+('' if source.is_file() else '（原文件不可用）'),self.file_list)
            item.setData(Qt.ItemDataRole.UserRole,path)
            item.setToolTip(path)
        self.file_list.setVisible(bool(self.files))
        self.sources_note.setText('收藏：'+papers if papers else
                                  '可拖入文档或图片。图片需由支持图片输入的模型读取。')

    def add_files(self,names):
        if self.task:
            return
        paths = [str(Path(name).absolute()) for name in names if
                 Path(name).is_file() and protocol_attachment(name)]
        previous = self.files
        self.files = list({path.casefold():path for path in [*self.files,*paths]}.values())
        if self.files!=previous:
            self.mark_dirty()
        self.refresh_sources()
        if len(paths)!=len(names):
            self.status.setText('部分文件无法识别。可添加 PDF、DOCX、文本及本机可解码的图片。')

    def open_source_folder(self,item):
        folder = Path(item.data(Qt.ItemDataRole.UserRole)).parent
        if not folder.is_dir():
            self.status.setText('原资料所在文件夹不可用。')
        elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            self.status.setText('无法打开资料所在文件夹。')

    def pick_files(self):
        dialog = QFileDialog(self,'选择方法资料')
        dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
        patterns = ' '.join('*'+suffix for suffix in sorted(image_suffixes()))
        dialog.setNameFilters(['方法文档 (*.pdf *.docx *.txt *.md)','图片 ('+patterns+')','所有文件 (*.*)'])
        if dialog.exec():
            self.add_files(dialog.selectedFiles())
        dialog.deleteLater()

    def pick_articles(self):
        picker = FavoritePicker(self.manager.store.database,{row['article_key'] for row in self.articles},self)
        if picker.exec():
            self.articles = picker.articles()
            self.refresh_sources()
            self.mark_dirty()

    def clear_sources(self):
        self.files,self.articles = [],[]
        self.refresh_sources()
        self.mark_dirty()

    def eventFilter(self,watched,event):
        if event.type() in (QEvent.Type.DragEnter,QEvent.Type.DragMove,QEvent.Type.Drop) and event.mimeData().hasUrls():
            if event.type()==QEvent.Type.Drop:
                self.dropEvent(event)
            else:
                self.dragEnterEvent(event)
            return True
        return super().eventFilter(watched,event)

    def dragEnterEvent(self,event):
        if not self.task and event.mimeData().hasUrls() and all(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self,event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if not self.task and paths:
            self.add_files(paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    def run(self):
        if self.task or self.export_task:
            return
        try:
            method = self.method.toPlainText().strip()
            if not method:
                raise ValueError('请先说明要提取的具体方法。')
            engine = self.engine()
            previous = self.current_result() if self.result else None
            request = self.follow_up.toPlainText().strip() if previous else ''
            if previous and not request:
                request = '结合当前附加材料核对并修订当前草稿，保留仍适用的正文。'
        except ValueError as error:
            self.status.setText(str(error))
            return
        files,articles,diagram = tuple(self.files),tuple(self.articles),self.use_diagram.isChecked()
        database = self.manager.store.database
        progress = self.progress_callback
        self.source_panel.setEnabled(False)
        self.editor.setReadOnly(True)
        self.title.setReadOnly(True)
        self.start(lambda:draft_protocol(engine,database,method,files,articles,progress,
                                         with_diagram=diagram,previous=previous,request=request))
        self.refresh_actions()

    def display(self,result,saved=False):
        result = without_local_references(result)
        self.built_at = self.built_at or result.get('created_at')
        self.result,self.dirty = result,not saved
        self.source_panel.setEnabled(True)
        self.run_button.setText('发送给 AI 修订')
        self.follow_up.setVisible(True)
        if not saved:
            with QSignalBlocker(self.follow_up):
                self.follow_up.clear()
        self.editor.setReadOnly(False)
        self.title.setReadOnly(False)
        self.title.setText(result.get('title',''))
        self.editor.blockSignals(True)
        self.editor.setPlainText(result.get('text',''))
        block = QTextBlockFormat()
        block.setTextIndent(24)
        block.setLineHeight(150,QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        cursor = QTextCursor(self.editor.document())
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.mergeBlockFormat(block)
        self.editor.moveCursor(QTextCursor.MoveOperation.Start)
        self.editor.blockSignals(False)
        try:
            image = QImage.fromData(flow_image(result) or b'')
        except Exception:
            image = QImage()
        self.preview.present(result,image)
        self.set_editor_visible(False)
        self.reasoning_note.setText('已加载内置科研写作、科学证据评估和文献检索技能。' if result.get('scientific_skills') else '')
        self.status.setText(' · '.join(result.get('warnings',[])) or ('已打开保存的方案。' if saved else '已生成。可核对、编辑和保存方案。'))
        self.refresh_actions()

    def failed(self,message):
        super().failed(message)
        self.source_panel.setEnabled(True)
        self.editor.setReadOnly(False)
        self.title.setReadOnly(False)
        self.refresh_actions()

    def refresh_actions(self):
        ready = bool(self.result and not self.task and not self.export_task)
        self.save_button.setEnabled(ready)
        self.full.setEnabled(ready)
        self.switch.setEnabled(ready)
        for button in self.exports:
            button.setEnabled(ready)

    def mark_dirty(self,*_):
        if self.result:
            self.dirty = True

    def current_result(self):
        title,body = self.title.text().strip(),self.editor.toPlainText().strip()
        if not title or len(title)>160 or not body:
            raise ValueError('请填写标题（不超过 160 字）及正文。')
        return {**self.result,'title':title,'text':body}

    def save_result(self):
        result = self.current_result()
        inputs = dict(file_paths=self.files,article_keys=[article['article_key'] for article in self.articles],
                      method=self.method.toPlainText().strip(),with_diagram=self.use_diagram.isChecked(),
                      follow_up=self.follow_up.toPlainText().strip())
        self.identifier = self.manager.repository.save_history('protocol',display_title(result),inputs,result,self.identifier,
                                                               created_at=self.built_at)
        self.result = result
        self.preview.present(result)
        self.manager.changed.emit()

    def set_editor_visible(self,editing):
        first_edit = editing and self.views.currentIndex()==0
        if not editing and self.result:
            draft = {**self.result,'title':self.title.text(),'text':self.editor.toPlainText()}
            self.preview.present(draft)
        self.views.setCurrentIndex(1 if editing else 0)
        self.switch.setText('阅读预览' if editing else '编辑正文')
        if first_edit:
            self.editor.setFocus()
            self.editor.selectAll()

    def switch_view(self):
        self.set_editor_visible(self.views.currentIndex()==0)

    def toggle_full(self):
        full = not self.isFullScreen()
        if full:
            self.normal_split_sizes = self.splitter.sizes()
            self.was_maximized = self.isMaximized()
            self.upper_panel.hide()
            self.showFullScreen()
        else:
            self.upper_panel.show()
            self.showMaximized() if self.was_maximized else self.showNormal()
            self.splitter.setSizes(self.normal_split_sizes)
        self.full.setText('退出全屏' if full else '全屏正文')

    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape and self.isFullScreen():
            self.toggle_full()
            event.accept()
            return
        super().keyPressEvent(event)

    def export(self,extension,writer):
        try:
            result = self.current_result()
        except ValueError as error:
            self.status.setText(str(error))
            return
        author = ask_author(self,self.manager.store.database)
        if author is None:
            return
        result['author'] = author
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',display_title(result))[:140].rstrip(' .')
        path,_ = QFileDialog.getSaveFileName(self,'导出 Protocol',name+'.'+extension,
                                            extension.upper()+f' (*.{extension})')
        if not path:
            return
        if not path.lower().endswith('.'+extension):
            path += '.'+extension
        self.export_task = self.manager.submit(self,lambda:write_protocol(path,result,writer),'export_done','export_failed')
        self.status.setText('正在导出 Protocol…')
        self.refresh_actions()

    def export_done(self,path):
        self.export_task = None
        self.status.setText('Protocol 已导出：'+path)
        self.refresh_actions()

    def export_failed(self,message):
        self.export_task = None
        self.status.setText(message)
        self.refresh_actions()
        QMessageBox.warning(self,'Protocol 导出未完成',message)


class ProtocolHub(QDialog):
    def __init__(self,manager,parent=None):
        super().__init__(parent)
        window_flags(self)
        self.manager = manager
        self.transfer = None
        self.setWindowTitle('AI Protocol · 方案库')
        self.resize(1040,690)
        layout = QVBoxLayout(self)
        heading = QLabel('AI Protocol')
        heading.setObjectName('PageTitle')
        layout.addWidget(heading)
        row = QHBoxLayout()
        self.controls = []
        for label,action in [('新建 Protocol',self.new),('打开所选',self.open_selected),
                             ('打包分享',self.share),('导入分享包',self.import_bundle),('删除所选',self.remove)]:
            button = QPushButton(label)
            button.clicked.connect(action)
            row.addWidget(button)
            self.controls.append(button)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(plain_label('点击卡片打开，拖动调整顺序。可勾选多张卡片，批量分享或删除。',True))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.board = CardBoard(columns=4)
        self.board.empty.setText('暂无 Protocol。点击「新建 Protocol」开始。')
        self.board.order_changed.connect(self.save_order)
        scroll.setWidget(self.board)
        layout.addWidget(scroll,1)
        self.status = plain_label('',True)
        layout.addWidget(self.status)
        manager.changed.connect(self.reload)
        self.reload()

    def reload(self):
        selected = set(self.selected())
        self.board.clear()
        self.checks = {}
        database = self.manager.store.database
        rows = self.manager.repository.histories('protocol')
        try:
            order = json.loads(database.get_settings().get('protocol_card_order','[]'))
        except (TypeError,ValueError):
            order = []
        positions = {identifier:index for index,identifier in enumerate(order)}
        rows.sort(key=lambda row:positions.get(row['id'],-1))
        for row in rows:
            identifier = row['id']
            card = self.board.add_card(identifier,row['title'],'构建时间：'+local_time(row['created_at']),self.open)
            check = QCheckBox('选中',card)
            check.setChecked(identifier in selected)
            card.layout().addWidget(check,0,Qt.AlignmentFlag.AlignLeft)
            self.checks[identifier] = check

    def save_order(self,order):
        self.manager.store.database.save_settings({'protocol_card_order':json.dumps(order)})

    def selected(self):
        return [identifier for identifier,box in getattr(self,'checks',{}).items() if box.isChecked()]

    def new(self):
        if not self.transfer:
            show_window(ProtocolDialog(self.manager,self.parentWidget()))
            self.close()

    def find(self,identifier):
        from PySide6.QtWidgets import QApplication
        return next((dialog for dialog in QApplication.topLevelWidgets() if isinstance(dialog,ProtocolDialog) and
                     dialog.manager is self.manager and dialog.identifier==identifier and dialog.isVisible()),None)

    def open(self,identifier):
        if self.transfer:
            return
        record = self.manager.repository.history(identifier)
        if record and record['kind']=='protocol':
            dialog = self.find(identifier) or ProtocolDialog(self.manager,self.parentWidget(),record)
            show_window(dialog)
            self.close()
            return dialog

    def open_selected(self):
        items = self.selected()
        if items:
            self.open(items[0])

    def remove(self):
        selected = self.selected()
        if not selected:
            return
        if any(self.find(identifier) for identifier in selected):
            self.status.setText('请先关闭正在打开的 Protocol。')
            return
        if QMessageBox.question(self,'删除方案','删除选中的 Protocol 及其正文、流程图缓存？')==QMessageBox.StandardButton.Yes:
            for identifier in selected:
                self.manager.repository.delete_history(identifier)
            self.manager.changed.emit()

    def share(self):
        selected = self.selected()
        if not selected:
            self.status.setText('请先选择要打包的 Protocol。')
            return
        author = ask_author(self,self.manager.store.database)
        if author is None:
            return
        path,_ = QFileDialog.getSaveFileName(self,'打包分享 Protocol','BiomedReader_Protocols.zip','ZIP (*.zip)')
        if path:
            if not path.lower().endswith('.zip'):
                path += '.zip'
            results = [{**record['result'],'author':author} for i in selected if (record:=self.manager.repository.history(i))]
            self.start_transfer(lambda:share_protocols(path,results))

    def import_bundle(self):
        path,_ = QFileDialog.getOpenFileName(self,'导入 Protocol 分享包','','ZIP (*.zip)')
        if path:
            database = self.manager.store.database
            self.start_transfer(lambda:import_protocols(database,path))

    def start_transfer(self,work):
        if self.transfer:
            return
        self.transfer = self.manager.submit(self,work,'transferred','transfer_failed')
        for button in self.controls:
            button.setEnabled(False)
        self.status.setText('正在处理 Protocol 分享包……')

    def transferred(self,count):
        self.transfer_failed(f'已处理 {count} 条 Protocol。')
        self.reload()

    def transfer_failed(self,message):
        self.transfer = None
        for button in self.controls:
            button.setEnabled(True)
        self.status.setText(message)

    def done(self,result):
        if not self.transfer:
            super().done(result)
