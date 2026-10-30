"""Local research chats with verified recommendation cards and explicit collection."""
import json,threading,uuid,weakref,math
from pathlib import Path
from PySide6.QtCore import Qt,QTimer,QEvent,QPoint
from PySide6.QtGui import QDesktopServices,QTextBlockFormat
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QWidget,QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,
    QPlainTextEdit,QScrollArea,QFrame,QTextBrowser,QProgressBar,QMessageBox,QInputDialog,QListWidget,QListWidgetItem,
    QLineEdit,QDialogButtonBox,QCheckBox,QSizePolicy,QTextEdit,QToolButton,QSplitter,QLayout)
from ai_ui import plain_label,local_time
from ai_store import PROVIDERS
from ai_analysis import AnalysisEngine
from ai_finder import find_articles,SUPPORTED,FILE_FILTER
from ai_workflow_ui import window_flags,show_window
from card_board import CardBoard
from chat_navigation import ConversationRail,set_line_spacing


class FavoriteRecommendations(QDialog):
    def __init__(self,database,articles,parent=None):
        super().__init__(parent);self.database=database;self.articles=articles;self.setWindowTitle('收藏推荐文章');self.resize(720,540);layout=QVBoxLayout(self)
        layout.addWidget(plain_label('选择文章和目标收藏分组。仅保存文献元数据，不下载或链接原文PDF；原文可自行下载后关联。',True))
        self.list=QListWidget();layout.addWidget(self.list,1)
        for index,article in enumerate(articles):
            item=QListWidgetItem(article.get('title') or '无标题');item.setData(Qt.ItemDataRole.UserRole,index);item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable);item.setCheckState(Qt.CheckState.Checked);self.list.addItem(item)
        row=QHBoxLayout();row.addWidget(QLabel('已有分组'));self.groups=QComboBox();self.groups.addItem('请选择分组…',None)
        for group in database.list_custom_groups():self.groups.addItem(group['name'],group['id'])
        row.addWidget(self.groups,1);layout.addLayout(row)
        self.new_name=QLineEdit();self.new_name.setPlaceholderText('或输入新分组名称（优先使用新名称）');layout.addWidget(self.new_name)
        self.status=plain_label('',True);layout.addWidget(self.status);buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(self.save);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
    def save(self):
        articles=[self.articles[self.list.item(i).data(Qt.ItemDataRole.UserRole)] for i in range(self.list.count()) if self.list.item(i).checkState()==Qt.CheckState.Checked]
        if not articles:self.status.setText('请至少选择一篇文章。');return
        group=self.groups.currentData();name=self.new_name.text().strip()
        try:
            if name:
                existing=next((g for g in self.database.list_custom_groups() if g['name'].casefold()==name.casefold()),None)
                if existing:
                    if QMessageBox.question(self,'分组名称重复','已有同名分组，是否收藏到该分组？')!=QMessageBox.StandardButton.Yes:return
                    group=existing['id']
                else:group,_=self.database.create_favorite_group(name,'AI寻找文章')
            if group is None:self.status.setText('请选择已有分组或输入新分组名称。');return
            for article in articles:self.database.save_pdf_favorite(article,None,group,link_pdf=False)
        except Exception:self.status.setText('收藏未全部完成，请确认分组仍存在、数据目录可写后重试。');return
        self.accept()


class ChatInput(QTextEdit):
    def __init__(self,send,parent=None):
        super().__init__(parent);self.send=send;self.spacing_busy=False;self.setAcceptRichText(False);set_line_spacing(self)
        self.document().blockCountChanged.connect(self.format_blocks);self.textChanged.connect(self.format_current_block)
    def format_blocks(self):
        if self.spacing_busy:return
        self.spacing_busy=True
        try:set_line_spacing(self)
        finally:self.spacing_busy=False
    def format_current_block(self):
        if self.spacing_busy:return
        cursor=self.textCursor()
        if cursor.blockFormat().lineHeight()==150:return
        block=QTextBlockFormat();block.setLineHeight(150,QTextBlockFormat.LineHeightTypes.ProportionalHeight.value);self.spacing_busy=True
        try:cursor.mergeBlockFormat(block)
        finally:self.spacing_busy=False
    def keyPressEvent(self,event):
        if event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter) and not event.modifiers()&Qt.KeyboardModifier.ShiftModifier:
            self.send();event.accept();return
        super().keyPressEvent(event)


class FinderChat(QDialog):
    def __init__(self,manager,record,parent=None):
        super().__init__(parent);window_flags(self);self.manager=manager;self.identifier=record['id'];self.title=record['title'];self.setWindowTitle('AI寻找文章 · '+self.title);self.resize(1080,min(720,int(self.screen().availableGeometry().height()*.86)));self.setAcceptDrops(True)
        inputs=record.get('inputs') or {};result=record.get('result') or {};self.messages=inputs.get('messages') or [];self.attachments=inputs.get('attachments') or [];self.notes=inputs.get('notes') or {};self.pinned=inputs.get('model');self.ended=bool(result.get('ended'));self.pending=[];self.task=None;self.stop=threading.Event();self.channel=uuid.uuid4().hex;self.approved=False
        self.articles=manager.repository.finder_cards(self.identifier);self.result=result;self.editing_index=None;self.edit_request=None
        self.setObjectName('FinderChat')
        self.setStyleSheet("""
QDialog#FinderChat {background:#F4F5F6;font-family:Arial,Microsoft YaHei;}
QFrame#ResearchHeader {background:#FFFFFF;border:1px solid #E4E3E0;border-radius:14px;}
QFrame#UserMessage {background:#FFF2E4;border:1px solid #EED8C2;border-radius:14px;}
QFrame#AssistantMessage {background:#FFFFFF;border:1px solid #DFE4E7;border-left:3px solid #77949B;border-radius:12px;}
QFrame#NoticeMessage {background:#FFF9EA;border:1px solid #E9D9B7;border-radius:12px;}
QFrame#FinderArticle {background:#F8FAFB;border:1px solid #DDE5E8;border-radius:10px;}
QFrame#FinderArticle:hover {border-color:#B3C7CC;background:#FCFDFD;}
QFrame#ResearchComposer {background:white;border:1px solid #DFE3E5;border-bottom:2px solid #D5DCDF;border-radius:14px;}
QFrame#ResearchWelcome {background:white;border:1px solid #E1E5E7;border-radius:14px;}
""")
        root=QVBoxLayout(self);root.setContentsMargins(22,16,22,16);root.setSpacing(10)
        heading=QFrame();heading.setObjectName('ResearchHeader');heading_box=QVBoxLayout(heading);heading_box.setContentsMargins(20,14,20,14);heading_box.setSpacing(6)
        header=QHBoxLayout();brand=QLabel('BIOMED READER   /   RESEARCH DESK');brand.setStyleSheet('color:#A86831;font-size:9pt;font-weight:700;');header.addWidget(brand);header.addStretch();self.models=QComboBox();self.models.setToolTip('选择用于本次科研对话的模型');header.addWidget(self.models);heading_box.addLayout(header)
        title=plain_label(self.title);title.setStyleSheet('font-size:18pt;font-weight:700;color:#263F49;');heading_box.addWidget(title)
        description=plain_label('文献检索  ·  原文阅读  ·  方法与理论探索',True);description.setStyleSheet('color:#7A8589;font-size:9.5pt;');heading_box.addWidget(description)
        description.setToolTip('真实PubMed检索＋DOI原文读取。优先读取本机有权限的全文，不绕过登录或付费限制；仅缓存最终回答及续聊资料，不缓存模型思考过程。')
        root.addWidget(heading)
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True);self.scroll.setStyleSheet('QScrollArea {border:none;background:transparent;}');self.content=QWidget();self.content.setObjectName('FinderThread');self.content.setStyleSheet('QWidget#FinderThread {background:transparent;}');self.thread=QVBoxLayout(self.content);self.thread.setContentsMargins(8,16,12,16);self.thread.setSpacing(22);self.thread.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize);self.scroll.setWidget(self.content)
        thread_row=QHBoxLayout();thread_row.setSpacing(6);self.turn_rail=ConversationRail();thread_row.addWidget(self.turn_rail);thread_row.addWidget(self.scroll,1);thread_host=QWidget();thread_host.setLayout(thread_row);self.composer_splitter=QSplitter(Qt.Orientation.Vertical);self.composer_splitter.setChildrenCollapsible(False);self.composer_splitter.setHandleWidth(7);self.composer_splitter.setStyleSheet('QSplitter::handle {background:#DFE3E5;border-radius:3px;} QSplitter::handle:hover {background:#F4AD62;}');self.composer_splitter.addWidget(thread_host);root.addWidget(self.composer_splitter,1);self.turn_rail.jumpRequested.connect(self.jump_to_turn);self.scroll.verticalScrollBar().valueChanged.connect(self.update_turn_marker);self._jumping=False
        self.status=plain_label('',True);root.addWidget(self.status);self.progress=QProgressBar();self.progress.setRange(0,0);self.progress.hide();root.addWidget(self.progress)
        composer=QFrame();composer.setObjectName('ResearchComposer');composer_box=QVBoxLayout(composer);composer_box.setContentsMargins(14,10,14,12);composer_box.setSpacing(8)
        self.input=ChatInput(self.send);self.input.setPlaceholderText('描述研究方向、科学问题或方法瓶颈…\n可添加图片、PDF、PPTX、Excel等研究资料。');self.input.setMinimumHeight(0);self.input.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Expanding);self.input.setStyleSheet('QTextEdit {background:transparent;border:none;padding:4px;font-family:Arial,Microsoft YaHei;font-size:11pt;}');self.input.setAccessibleName('科研问题输入');composer_box.addWidget(self.input)
        self.file_label=plain_label('',True);self.file_label.setStyleSheet('color:#879094;font-size:9pt;');composer_box.addWidget(self.file_label)
        controls=QHBoxLayout();self.add=QPushButton('＋ 科研资料');self.add.setProperty('secondary',True);self.add.clicked.connect(self.add_files);controls.addWidget(self.add);self.clear=QPushButton('清除附件');self.clear.setProperty('secondary',True);self.clear.clicked.connect(self.clear_files);controls.addWidget(self.clear);self.cancel=QPushButton('停止');self.cancel.setToolTip('停止后续模型调用');self.cancel.setProperty('secondary',True);self.cancel.clicked.connect(self.stop.set);controls.addWidget(self.cancel);controls.addStretch();hint=QLabel('Enter 发送 · Shift+Enter 换行');hint.setStyleSheet('font-size:8.5pt;color:#8C9295;');controls.addWidget(hint);self.send_button=QPushButton('查找文献  ↑');self.send_button.clicked.connect(self.send);controls.addWidget(self.send_button);composer_box.addLayout(controls);self.composer=composer;composer_box.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint);composer.setMinimumHeight(0);composer.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Ignored);self.composer_splitter.addWidget(composer);self.composer_splitter.setCollapsible(1,True);self.composer_splitter.widget(0).setMinimumHeight(80);self.composer_splitter.setStretchFactor(0,4);self.composer_splitter.setStretchFactor(1,1);self.composer_splitter.setSizes([410,175])
        manager.changed.connect(self.update_models);manager.progress.connect(self.progress_message);self.update_models();self.render();self.update_state()
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);self.scroll.viewport().installEventFilter(self)
        from compact_fields import CompactFields
        self.compact_models=CompactFields(self,[self.models])
    def update_models(self):
        if self.task:return
        selected=self.models.currentData();self.models.clear()
        for provider in PROVIDERS:
            config=self.manager.store.config(provider)
            if config['enabled'] and config['model']:self.models.addItem(PROVIDERS[provider]+' · '+config['model'],provider)
        default=selected or (self.pinned or {}).get('provider');index=self.models.findData(default)
        if index>=0:self.models.setCurrentIndex(index)
    def update_state(self):
        busy=bool(self.task);self.send_button.setEnabled(not busy and not self.ended);self.input.setEnabled(not busy and not self.ended);self.add.setEnabled(not busy and not self.ended);self.clear.setEnabled(not busy);self.models.setEnabled(not busy);self.cancel.setEnabled(busy);self.cancel.setVisible(busy);self.clear.setVisible(bool(self.pending));self.progress.setVisible(busy)
        self.file_label.setText(('待发送：'+'；'.join(Path(p).name for p in self.pending)) if self.pending else '支持拖放资料 · 附件将发送至所选AI服务，请确认使用权限；原文件不复制到资料库。')
        if self.ended:self.status.setText('此对话仅限科研咨询；非科研对话已结束，请返回新建科研对话。')
    def add_files(self):
        from path_dialog import QFileDialog
        paths,_=QFileDialog.getOpenFileNames(self,'添加研究相关资料','',FILE_FILTER)
        self.append_files(paths)
    def append_files(self,paths):
        rejected=[]
        for value in paths:
            path=Path(value)
            if path.is_file() and path.suffix.lower() in SUPPORTED:
                if str(path) not in self.pending:self.pending.append(str(path))
            else:rejected.append(path.name)
        if rejected:QMessageBox.information(self,'文件格式提示','未添加：'+'、'.join(rejected)+'\n旧版PPT/XLS/DOC请先另存为PPTX/XLSX/DOCX。图片需要所选模型支持视觉。')
        self.update_state()
    def clear_files(self):self.pending=[];self.update_state()
    def dragEnterEvent(self,event):
        if not self.task and not self.ended and event.mimeData().hasUrls():event.acceptProposedAction()
    def dropEvent(self,event):
        if not self.task and not self.ended:self.append_files([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]);event.acceptProposedAction()
    def progress_message(self,channel,text):
        if channel==self.channel:self.status.setText(text)
    def send(self):
        if self.task or (self.ended and not self.edit_request):return
        request=self.edit_request;self.edit_request=None
        text=(request[1] if request else self.input.toPlainText()).strip();provider=self.models.currentData()
        if not text:QMessageBox.information(self,'请描述研究问题','请说明研究领域、希望解决的问题以及方法或理论上的限制。');return
        if not provider:QMessageBox.information(self,'请开启模型','请在AI部署中配置并开启模型。');return
        config=self.manager.store.config(provider);current=dict(provider=provider,model=config['model'])
        if self.pinned and self.pinned!=current and QMessageBox.question(self,'分析模型已更换','此对话原模型为 '+self.pinned['provider']+' / '+self.pinned['model']+'。是否将已保存的最终回答和必要资料交给当前模型继续？',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        try:engine=AnalysisEngine(self.manager.store,provider)
        except ValueError as error:QMessageBox.information(self,'无法调用',str(error));return
        self.stop.clear();engine.cancelled=self.stop.is_set;self.pinned=current
        old_files=[]
        if request:
            index=request[0];old_files=self.messages[index].get('files',[])
            self.messages=self.messages[:index];self.ended=False;self.result={}
            retained={r['article_key'] for m in self.messages for r in m.get('references',[])}
            self.articles=[a for a in self.articles if a['article_key'] in retained];self.notes={k:v for k,v in self.notes.items() if k in retained};self.manager.repository.replace_finder_cards(self.identifier,self.articles)
        self.editing_index=None
        entry=dict(role='user',content=text,files=old_files+[Path(p).name for p in self.pending]);self.messages.append(entry);self.input.clear();self.render();files=list(self.pending);messages=[{k:v for k,v in m.items() if k!='display_width'} for m in self.messages];attachments=list(self.attachments);notes=dict(self.notes);self.persist()
        web_fetch=lambda url:self.manager.article_reader.get(url,automatic=True)
        work=lambda:find_articles(engine,self.manager.store.database,messages,files,attachments,notes,web_fetch,lambda text:self.manager.progress.emit(self.channel,text))
        self.task=self.manager.submit(self,work,'completed','failed');self.update_state();self.status.setText('正在识别问题、检索真实文献并读取证据；可最小化，可与其它AI功能并行运行。')
    def completed(self,result):
        self.task=None;self.result=result;self.ended=bool(result.get('ended'));self.attachments=result.get('attachments') or self.attachments;self.notes=result.get('notes') or self.notes;self.pending=[]
        self.messages.append(dict(role='assistant',content=result['text'],model=result.get('model',''),created_at=result.get('created_at',''),**__import__('reasoning').record(result),references=[dict(article_key=a['article_key'],title=a['title'],doi=a.get('doi',''),pmid=a.get('pmid',''),number=a.get('finder_reference')) for a in result.get('articles',[])]))
        found={row['article_key']:row for row in self.articles}
        for row in result.get('articles',[]):found[row['article_key']]=row
        self.articles=list(found.values());self.persist();self.manager.repository.replace_finder_cards(self.identifier,self.articles);self.render();self.update_state();self.status.setText('已完成。推荐卡片来自实际检索记录；请核对原文。'+(' 部分请求失败，本次检索不完整。' if result.get('errors') or result.get('incomplete') else ''))
        self.manager.changed.emit()
        if self.ended:self.status.setText('只能咨询科研相关问题，此对话已结束。')
    def failed(self,message):
        self.task=None;self.messages.append(dict(role='notice',content='本次未完成：'+message+'\n请求未自动重试。可修改或补充问题后再次发送。'));self.persist();self.render();self.update_state();self.status.setText(message)
    def persist(self):
        # Only final output/evidence are serialized. Never provider raw response,
        # chain-of-thought, credentials, absolute attachment paths or binary files.
        inputs=dict(messages=self.messages,attachments=self.attachments,notes=self.notes,model=self.pinned)
        result=dict(text=self.result.get('text',''),ended=self.ended)
        try:self.manager.repository.save_history('finder',self.title,inputs,result,self.identifier)
        except ValueError:self.ended=True;self.status.setText('此对话已删除，未恢复已删除的缓存。')
    def render(self,focus_index=None,anchor_y=None):
        self._render_revision=getattr(self,'_render_revision',0)+1
        revision=self._render_revision
        self.message_frames=[]
        while self.thread.count():
            item=self.thread.takeAt(0)
            if item.widget():item.widget().deleteLater()
        if not self.messages:
            welcome=QFrame();welcome.setObjectName('ResearchWelcome');welcome_box=QVBoxLayout(welcome);welcome_box.setContentsMargins(26,24,26,24);welcome_box.setSpacing(12)
            for text,style in [('从科学问题出发，寻找文献证据','font-size:17pt;font-weight:700;color:#2D4C57;'),('描述研究对象与尚未解决的问题，结合资料比较可参考的方法和理论。','color:#75838A;font-size:11pt;'),('例如：我研究转录相关DNA损伤修复。如何区分某蛋白促进R-loop清除，还是抑制其形成？有哪些方法可以交叉验证？','color:#495F68;font-size:11pt;'),('01  明确问题     02  补充研究资料     03  核查推荐文献','color:#AC703E;font-size:10pt;font-weight:600;')]:
                label=plain_label(text);label.setStyleSheet(style);welcome_box.addWidget(label)
            self.thread.addWidget(welcome)
        card_by_key={a['article_key']:a for a in self.articles}
        for message_index,message in enumerate(self.messages):
            user=message['role']=='user';frame=QFrame();frame.setObjectName('UserMessage' if user else ('NoticeMessage' if message['role']=='notice' else 'AssistantMessage'));box=QVBoxLayout(frame);box.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize);box.setContentsMargins(20,16,20,18);who=QLabel('研究问题 · YOU' if user else '文献分析 · '+str(message.get('model') or 'AI寻找文章'));who.setStyleSheet('font-size:9pt;font-weight:700;color:#A76B36;' if user else 'font-size:9pt;font-weight:700;color:#567A87;');box.addWidget(who)
            if message['role']=='assistant':
                model_note=plain_label(__import__('reasoning').result_label(message),True);model_note.setStyleSheet('color:#8A9498;font-size:9pt;');box.addWidget(model_note)
            browser=QTextBrowser();browser.setFrameShape(QFrame.Shape.NoFrame);browser.setOpenExternalLinks(False);browser.setPlainText(message.get('content',''));browser.setStyleSheet('QTextBrowser {background:transparent;color:#30424B;font-family:Arial,Microsoft YaHei;font-size:11.5pt;border:none;padding:0;}');browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);browser.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed);box.addWidget(browser)
            set_line_spacing(browser)
            if user and self.editing_index==message_index:
                browser.hide();self.message_editor=QPlainTextEdit(message.get('content',''));self.message_editor.setMinimumHeight(150);box.addWidget(self.message_editor)
                edit_row=QHBoxLayout();submit=QPushButton('发送');submit.clicked.connect(lambda checked=False,i=message_index:self.send_edit(i));edit_row.addWidget(submit);cancel_edit=QPushButton('取消');cancel_edit.setProperty('secondary',True);cancel_edit.clicked.connect(self.cancel_edit);edit_row.addWidget(cancel_edit);edit_row.addStretch();box.addLayout(edit_row)
            if message.get('files'):box.addWidget(plain_label('科研资料：'+' · '.join(message['files']),True))
            if message['role'] in ('user','assistant'):
                actions=QHBoxLayout();copy=QToolButton();copy.setText('⧉');copy.setToolTip('复制消息');copy.setAccessibleName('复制消息');copy.setFixedSize(32,30);copy.clicked.connect(lambda checked=False,i=message_index:self.copy_message(i));actions.addWidget(copy)
                if user:
                    edit=QToolButton();edit.setText('✎');edit.setToolTip('编辑并重新发送');edit.setAccessibleName('编辑消息');edit.setFixedSize(32,30);edit.clicked.connect(lambda checked=False,i=message_index:self.edit_message(i));actions.addWidget(edit)
                actions.addStretch();box.addLayout(actions)
            for ref in message.get('references',[]):
                article=card_by_key.get(ref['article_key'])
                if article:box.addWidget(self.article_card(article,ref.get('number')))
            self.thread.addWidget(frame,0,Qt.AlignmentFlag.AlignRight if user else Qt.AlignmentFlag.AlignLeft);frame.setMaximumWidth(780 if user else 1300);frame.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred);self.message_frames.append((frame,browser,user))
            if message['role']=='assistant':
                grip=QFrame(frame);grip.setObjectName('ReplyWidthGrip');grip.setProperty('reply_width_index',message_index);grip.setCursor(Qt.CursorShape.SizeHorCursor);grip.setToolTip('左右拖动，调整回复与文章卡片宽度');grip.setStyleSheet('QFrame#ReplyWidthGrip {border-left:2px solid #E8C9A6;background:transparent;} QFrame#ReplyWidthGrip:hover {border-left:3px solid #F28A22;}');grip.setMouseTracking(True);grip.installEventFilter(self);frame.width_grip=grip;grip.show()
        self.thread.addStretch();self.turn_rail.rebuild(self.messages,self.message_frames);self.reflow_messages()
        def settle():
            if revision!=self._render_revision:return
            self.reflow_messages()
            if focus_index is not None and 0<=focus_index<len(self.message_frames):
                frame=self.message_frames[focus_index][0]
                if self.editing_index==focus_index:self.message_editor.setFocus(Qt.FocusReason.OtherFocusReason)
                y=frame.mapTo(self.content,QPoint(0,0)).y()
                self.scroll.verticalScrollBar().setValue(max(0,y-max(8,anchor_y or 8)))
            else:self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())
        QTimer.singleShot(0,lambda:QTimer.singleShot(0,settle))
    def jump_to_turn(self,index):
        if not 0<=index<len(self.turn_rail.targets):return
        self._jumping=True
        try:
            target=self.turn_rail.targets[index];self.scroll.verticalScrollBar().setValue(max(0,target.mapTo(self.content,QPoint(0,0)).y()-10));self.turn_rail.setCurrentRow(index)
        finally:self._jumping=False
    def update_turn_marker(self,*_):
        if self._jumping or not self.turn_rail.targets:return
        top=self.scroll.verticalScrollBar().value()+16;index=0
        for i,target in enumerate(self.turn_rail.targets):
            if target.mapTo(self.content,QPoint(0,0)).y()<=top:index=i
            else:break
        self.turn_rail.setCurrentRow(index)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'message_frames'):self.reflow_messages()
    def eventFilter(self,watched,event):
        index=watched.property('reply_width_index')
        if index is not None:
            if event.type()==QEvent.Type.MouseButtonPress and event.button()==Qt.MouseButton.LeftButton:
                self._reply_drag=(index,event.globalPosition().x(),self.message_frames[index][0].width());watched.grabMouse();return True
            if event.type()==QEvent.Type.MouseMove and getattr(self,'_reply_drag',None):
                i,start,width=self._reply_drag;self.resize_reply(i,width+event.globalPosition().x()-start);return True
            if event.type()==QEvent.Type.MouseButtonRelease and getattr(self,'_reply_drag',None):
                self._reply_drag=None;watched.releaseMouse();self.persist();return True
        if hasattr(self,'scroll') and watched is self.scroll.viewport() and event.type()==QEvent.Type.Resize and hasattr(self,'message_frames'):self.reflow_messages()
        return super().eventFilter(watched,event)
    def resize_reply(self,index,width):
        if not 0<=index<len(self.messages) or self.messages[index]['role']!='assistant':return
        available=max(180,self.scroll.viewport().width()-40)
        self.messages[index]['display_width']=max(min(340,available),min(available,round(width)));self.reflow_messages()
    def copy_message(self,index):
        from PySide6.QtWidgets import QApplication
        if 0<=index<len(self.messages):QApplication.clipboard().setText(self.messages[index].get('content',''));self.status.setText('已复制，可以粘贴。')
    def edit_message(self,index):
        if self.task or not 0<=index<len(self.messages) or self.messages[index]['role']!='user':return
        anchor=self.message_frames[index][0].mapTo(self.scroll.viewport(),QPoint()).y()
        self.editing_index=index;self.render(focus_index=index,anchor_y=anchor)
    def cancel_edit(self):
        index=self.editing_index
        anchor=self.message_frames[index][0].mapTo(self.scroll.viewport(),QPoint()).y() if index is not None else 8
        self.editing_index=None;self.render(focus_index=index,anchor_y=anchor)
    def send_edit(self,index):
        if self.task:return
        text=self.message_editor.toPlainText().strip()
        if not text:self.status.setText('编辑内容不能为空。');return
        if index<len(self.messages)-1 and QMessageBox.question(self,'重新发送编辑后的问题','重新发送将从这条问题继续，替换其后的对话和推荐卡片；已收藏文章保留。此前上传的已解析科研资料仍可用于续聊。是否继续？',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        self.edit_request=(index,text);self.send()
    def reflow_messages(self):
        if getattr(self,'_reflowing',False):return
        self._reflowing=True
        try:
            available=max(180,self.scroll.viewport().width()-40)
            for index,(frame,browser,user) in enumerate(self.message_frames):
                width=min(640 if user else 900,available,max(330,int(browser.fontMetrics().horizontalAdvance(max(browser.toPlainText().splitlines() or [''],key=len))+90)))
                if self.messages[index]['role']=='assistant':width=available
                if not user and isinstance(self.messages[index].get('display_width'),(int,float)):width=max(min(340,available),min(available,int(self.messages[index]['display_width'])))
                frame.setFixedWidth(width);browser.setFixedWidth(max(140,width-44));frame.layout().activate()
                # QTextBrowser viewport is narrower than the widget due to its
                # frame/content margins. Measure the real wrapping width and
                # reserve the FULL document height before positioning cards.
                browser.document().setTextWidth(max(100,browser.viewport().width()))
                browser.setFixedHeight(max(34,math.ceil(browser.document().documentLayout().documentSize().height())+20))
                frame.layout().invalidate();frame.layout().activate();frame.setMinimumHeight(max(frame.layout().minimumSize().height(),frame.layout().heightForWidth(width)))
                if hasattr(frame,'width_grip'):frame.width_grip.setGeometry(width-12,14,10,max(28,frame.height()-28));frame.width_grip.raise_()
            self.thread.invalidate();self.thread.activate();self.content.adjustSize()
        finally:self._reflowing=False
    def article_card(self,article,number):
        card=QFrame();card.setObjectName('FinderArticle');box=QVBoxLayout(card);journal=plain_label(article.get('journal_title','')+' · '+article.get('publication_date',''),True);box.addWidget(journal)
        title=plain_label(('['+str(number)+'] ' if number else '')+article['title']);title.setStyleSheet('font-weight:700;color:#255366;font-size:12pt;');box.addWidget(title);abstract=article.get('abstract') or '未提供摘要';box.addWidget(plain_label(abstract[:650]+('…' if len(abstract)>650 else '')));box.addWidget(plain_label('本次材料：'+article.get('finder_coverage','检索元数据'),True))
        row=QHBoxLayout();details=QPushButton('文章详情');details.setProperty('secondary',True);details.clicked.connect(lambda:self.article_detail(article));row.addWidget(details);collect=QPushButton('收藏♡');collect.clicked.connect(lambda:self.collect([article]));row.addWidget(collect);remove=QPushButton('删除卡片');remove.setProperty('secondary',True);remove.clicked.connect(lambda:self.remove_card(article['article_key']));row.addWidget(remove);row.addStretch();box.addLayout(row);card.setCursor(Qt.CursorShape.PointingHandCursor);card.mouseReleaseEvent=lambda event:self.article_detail(article) if event.button()==Qt.MouseButton.LeftButton else None;return card
    def remove_card(self,key):
        if self.task:return
        self.articles=[a for a in self.articles if a['article_key']!=key]
        for message in self.messages:message['references']=[r for r in message.get('references',[]) if r.get('article_key')!=key]
        self.notes.pop(key,None)
        self.manager.repository.replace_finder_cards(self.identifier,self.articles);self.persist();self.render()
        self.status.setText('已删除推荐卡片；清理无用缓存会回收无其它引用的资料，已收藏文章保留。')
    def collect(self,articles):
        dialog=FavoriteRecommendations(self.manager.store.database,articles,self)
        if dialog.exec()==QDialog.DialogCode.Accepted:self.manager.changed.emit();self.status.setText('已收藏到选定分组，未链接PDF。');return True
        return False
    def article_detail(self,article):
        dialog=QDialog(self);dialog.setWindowTitle('推荐文章详情');dialog.resize(790,620);layout=QVBoxLayout(dialog);layout.addWidget(plain_label(article.get('journal_title','')+' · '+article.get('publication_date',''),True));title=plain_label(article['title']);title.setStyleSheet('font-size:14pt;font-weight:700;');layout.addWidget(title);text=QTextBrowser();text.setPlainText(article.get('abstract') or '未提供摘要');layout.addWidget(text,1)
        row=QHBoxLayout();link=QPushButton('打开DOI/文章页面');link.clicked.connect(lambda:QDesktopServices.openUrl(QUrl('https://doi.org/'+article['doi'] if article.get('doi') else 'https://pubmed.ncbi.nlm.nih.gov/'+article.get('pmid','')+'/')));row.addWidget(link);favorite=QPushButton('收藏到指定分组');favorite.clicked.connect(lambda:self.collect([article]));row.addWidget(favorite);layout.addLayout(row);dialog.exec()
    def confirm_close(self):
        if self.approved:return True
        if self.task:self.status.setText('请求尚在处理，可最小化窗口，或停止后续调用后等待返回。');return False
        self.persist();self.manager.repository.replace_finder_cards(self.identifier,self.articles);self.approved=True;return True
    def reject(self):
        if self.confirm_close():super().reject()
    def closeEvent(self,event):
        if not self.confirm_close():event.ignore();return
        super().closeEvent(event)


class FinderHub(QDialog):
    def __init__(self,manager,parent=None):
        super().__init__(parent);window_flags(self);self.manager=manager;self.setWindowTitle('AI寻找文章 · 科研对话');self.resize(1050,760);layout=QVBoxLayout(self);heading=QLabel('AI寻找文章');heading.setStyleSheet('font-size:21pt;font-weight:700;color:#183E4C;');layout.addWidget(heading);layout.addWidget(plain_label('每张卡片是一段可持续的科研对话。描述领域和研究问题，结合自己的资料寻找可参考的方法与理论。',True));row=QHBoxLayout();add=QPushButton('＋ 添加对话卡片');add.clicked.connect(self.create);row.addWidget(add);row.addStretch();layout.addLayout(row);scroll=QScrollArea();scroll.setWidgetResizable(True);self.board=CardBoard(columns=3);scroll.setWidget(self.board);layout.addWidget(scroll,1);manager.changed.connect(self.reload);self.reload()
    def reload(self):
        self.board.clear()
        for record in self.manager.repository.histories('finder'):
            identifier=record['id'];card=self.board.add_card(str(identifier),record['title'],'保存：'+local_time(record['created_at'])+'\n科研问题 · 资料 · 真实文献建议',lambda _,i=identifier:self.open(i));card.mouseMoveEvent=lambda event:None;delete=QPushButton('删除对话');delete.setProperty('secondary',True);delete.clicked.connect(lambda checked=False,i=identifier:self.delete(i));card.layout().addWidget(delete)
    def create(self):
        title,ok=QInputDialog.getText(self,'新建科研对话','对话名称（例如：R-loop测量方法比较）')
        if ok and title.strip():
            identifier=self.manager.repository.save_history('finder',title.strip(),dict(messages=[],attachments=[],notes={}),{});self.reload();self.open(identifier)
    def open(self,identifier):
        if not hasattr(self.manager,'finder_windows'):self.manager.finder_windows={}
        reference=self.manager.finder_windows.get(identifier);dialog=reference() if reference else None
        import shiboken6
        if dialog is not None and shiboken6.isValid(dialog):
            self.accept();QTimer.singleShot(0,lambda:show_window(dialog) if shiboken6.isValid(dialog) else None);return
        record=self.manager.repository.history(identifier)
        if record:
            # A conversation must outlive the card-list window which is closed
            # when entering it. Keep ownership on the application, not this hub.
            parent=self.manager.parent();dialog=FinderChat(self.manager,record,parent if isinstance(parent,QWidget) else None)
            self.manager.finder_windows[identifier]=weakref.ref(dialog)
            if not hasattr(self.manager,'finder_dialog_owners'):self.manager.finder_dialog_owners={}
            self.manager.finder_dialog_owners[identifier]=dialog;owners=self.manager.finder_dialog_owners
            dialog.finished.connect(lambda *_:owners.pop(identifier,None))
            self.accept();QTimer.singleShot(0,lambda:show_window(dialog) if shiboken6.isValid(dialog) else None)
    def delete(self,identifier):
        reference=getattr(self.manager,'finder_windows',{}).get(identifier);dialog=reference() if reference else None
        import shiboken6
        if dialog is not None and shiboken6.isValid(dialog):QMessageBox.information(self,'请先关闭对话','请先关闭此对话窗口，再删除历史记录。');return
        if QMessageBox.question(self,'删除科研对话','删除最终对话、续聊资料和候选文章卡片？已收藏文章保持保留。')==QMessageBox.StandardButton.Yes:self.manager.repository.delete_history(identifier);self.reload()
    def can_close(self):
        import shiboken6
        active=[reference() for reference in getattr(self.manager,'finder_windows',{}).values()]
        if any(dialog is not None and shiboken6.isValid(dialog) and dialog.isVisible() and dialog.parent() is self for dialog in active):
            QMessageBox.information(self,'请先关闭对话窗口','请先关闭本页打开的科研对话，以便确认是否收藏推荐文章。');return False
        return True
    def reject(self):
        if self.can_close():super().reject()
    def closeEvent(self,event):
        if not self.can_close():event.ignore();return
        super().closeEvent(event)
