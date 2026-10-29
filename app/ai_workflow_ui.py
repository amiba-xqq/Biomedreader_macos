"""Offline AI histories, article summaries, reference-only reports and research assistant."""
import json,uuid,math,threading
from datetime import datetime
import shiboken6
from PySide6.QtCore import Qt,QUrl
from PySide6.QtWidgets import (QWidget,QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QPlainTextEdit,
    QComboBox,QCheckBox,QMessageBox,QListWidget,QListWidgetItem,QScrollArea,QGroupBox,QProgressBar,QSpinBox,QDialogButtonBox,QGridLayout,QStackedWidget,QSizePolicy,QTabWidget)
from ai_ui import plain_label,local_time
from ai_store import PROVIDERS
from ai_analysis import AnalysisEngine
from ai_repository import now

def window_flags(dialog):
    dialog.setWindowFlags(Qt.WindowType.Window|Qt.WindowType.WindowMinMaxButtonsHint|Qt.WindowType.WindowCloseButtonHint)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose,True)

def show_window(dialog):dialog.showNormal();dialog.raise_();dialog.activateWindow();return dialog

class HistoryPane(QWidget):
    def __init__(self,manager,kind,open_item,parent=None):
        super().__init__(parent);self.manager=manager;self.kind=kind;self.open_item=open_item;layout=QVBoxLayout(self)
        layout.addWidget(plain_label('按保存时间由近到远排列；点击卡片查看或继续使用，所有保存内容可离线查看。',True))
        from card_board import CardBoard
        self.board=CardBoard(columns=4);self.board.card_height=240;self.board.empty.setText('暂无保存记录。')
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True);self.scroll.setWidget(self.board);layout.addWidget(self.scroll,1);manager.changed.connect(self.reload);self.reload()
    def reload(self):
        self.board.clear()
        for item in self.manager.repository.histories(self.kind):
            identifier=item['id'];title=item['title'];card=self.board.add_card(str(identifier),title[:64]+('…' if len(title)>64 else ''),'保存时间：'+local_time(item['created_at']),lambda _,i=identifier:self.open_record(i));card.setToolTip(title+'\n点击查看完整内容');card.mouseMoveEvent=lambda event:None
            row=QHBoxLayout();row.addStretch();delete=QPushButton('删除');delete.setProperty('secondary',True);delete.setStyleSheet('padding:5px 10px;');delete.clicked.connect(lambda checked=False,i=identifier:self.delete_record(i));row.addWidget(delete);card.layout().addLayout(row)
    def open_record(self,identifier):
        record=self.manager.repository.history(identifier)
        if record:self.open_item(record)
    def delete_record(self,identifier):
        if QMessageBox.question(self,'删除记录','删除此保存项？已收藏文章保持保留。')==QMessageBox.StandardButton.Yes:
            self.manager.repository.delete_history(identifier);self.reload()

class HistoryDialog(QDialog):
    def __init__(self,manager,kind,parent=None):
        super().__init__(parent);window_flags(self);self.manager=manager;self.setWindowTitle('AI提出科学问题 · 历史记录');self.resize(1050,700)
        layout=QVBoxLayout(self);layout.addWidget(HistoryPane(manager,kind,self.open_record,self))
    def open_record(self,record):
        dialog=ScientificQuestionsDialog(self.manager,self.parentWidget() or self);dialog.restore_record(record);show_window(dialog)

class AnalysisDialog(QDialog):
    def __init__(self,manager,title,parent=None):
        super().__init__(parent);window_flags(self);self.manager=manager;self.task=None;self.result=None;self.dirty=False;self.approved=False;self.channel=uuid.uuid4().hex
        self.stop=threading.Event()
        self.setStyleSheet('font-family:Arial,Microsoft YaHei;');self.setWindowTitle(title);self.resize(1000,800);self.body_layout=QVBoxLayout(self)
        self.info=plain_label('本机先读取已有权限下的 PDF／网页，再发送其文字到所选服务。模型不继承校园网 IP；不绕过登录、验证码或付费限制。');self.body_layout.addWidget(self.info)
        controls=QHBoxLayout();controls.addWidget(QLabel('分析模型'));self.models=QComboBox();controls.addWidget(self.models);controls.addStretch();self.body_layout.addLayout(controls)
        from compact_fields import CompactFields
        self.model_field_layout=CompactFields(self,[self.models])
        self.run_button=QPushButton('开始分析');self.run_button.clicked.connect(self.run);self.body_layout.addWidget(self.run_button,0,Qt.AlignmentFlag.AlignLeft)
        self.stop_button=QPushButton('停止后续调用');self.stop_button.setProperty('secondary',True);self.stop_button.clicked.connect(self.stop.set);self.body_layout.addWidget(self.stop_button,0,Qt.AlignmentFlag.AlignLeft)
        self.progress=QProgressBar();self.progress.setRange(0,0);self.progress.hide();self.body_layout.addWidget(self.progress)
        self.reasoning_note=plain_label('',True);self.body_layout.addWidget(self.reasoning_note)
        self.status=plain_label('',True);self.body_layout.addWidget(self.status)
        self.output=QPlainTextEdit();self.output.setReadOnly(True);self.body_layout.addWidget(self.output,1)
        bottom=QHBoxLayout();self.bottom_layout=bottom;self.save_button=QPushButton('保存结果');self.save_button.clicked.connect(self.save_clicked);bottom.addWidget(self.save_button);bottom.addStretch();close=QPushButton('关闭');close.setProperty('secondary',True);close.clicked.connect(self.reject);bottom.addWidget(close);self.body_layout.addLayout(bottom)
        manager.changed.connect(self.update_models);manager.progress.connect(self.progress_message);self.update_models()
    def update_models(self):
        if self.task:return
        selected=self.models.currentData();self.models.clear()
        default=self.manager.store.database.get_settings().get('ai_report_provider','')
        for provider in PROVIDERS:
            config=self.manager.store.config(provider)
            if config['enabled'] and config['model']:self.models.addItem(PROVIDERS[provider]+' · '+config['model'],provider)
        index=self.models.findData(selected or default)
        if index>=0:self.models.setCurrentIndex(index)
    def engine(self):
        provider=self.models.currentData()
        if not provider:raise ValueError('请先在 AI部署 中设置并开启一个模型。')
        engine=AnalysisEngine(self.manager.store,provider);engine.cancelled=self.stop.is_set;return engine
    def progress_message(self,channel,text):
        if channel==self.channel:self.status.setText(text)
    def start(self,work):
        self.stop.clear()
        self.run_button.setEnabled(False);self.models.setEnabled(False);self.progress.show();self.status.setText('正在读取与分析，可最小化窗口并同时使用其它 AI 功能……')
        self.task=self.manager.submit(self,work,'completed','failed')
    def progress_callback(self,text):self.manager.progress.emit(self.channel,text)
    def completed(self,result):self.task=None;self.progress.hide();self.run_button.setEnabled(True);self.models.setEnabled(True);self.display(result)
    def display(self,result,saved=False):
        self.result=result;self.dirty=not saved;self.reasoning_note.setText(__import__('reasoning').result_label(result))
        header='模型：'+result.get('provider','')+' / '+result.get('model','')+'\n生成时间：'+local_time(result.get('created_at'))
        if result.get('coverage'):header+='\n材料范围：'+result['coverage']+'\n来源：'+result.get('source_url','')
        if result.get('chunked'):header+='\n长材料采用分段阅读与证据笔记汇总，不能替代全文人工核查。'
        if result.get('incomplete'):header+='\n注意：接口返回输出不完整，请人工核查。'
        failures=result.get('failures') or []
        if failures:header+='\n未读成功：'+'；'.join(row['title']+'：'+row['reason'] for row in failures)
        self.output.setPlainText(header+'\n\n'+result.get('text',''));self.status.setText('已加载缓存，可离线查看。' if saved else '分析完成。可保存后离线查看；结论与创新点仍需核查原文。')
    def failed(self,message):
        self.task=None;self.progress.hide();self.run_button.setEnabled(True);self.models.setEnabled(True);self.status.setText(message);QMessageBox.warning(self,'AI 分析未完成',message)
    def save_clicked(self):
        if not self.result:return
        try:self.save_result();self.dirty=False;self.status.setText('已保存，可离线查看。')
        except ValueError as error:QMessageBox.warning(self,'未保存',str(error))
    def confirm_close(self):
        if self.task:self.status.setText('请求尚在处理，可最小化窗口；返回后可保存并关闭。');return False
        if self.approved:return True
        if self.dirty and self.result:
            answer=QMessageBox.question(self,'保存 AI 输出','是否保存本次 AI 输出，以便离线查看？',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No|QMessageBox.StandardButton.Cancel)
            if answer==QMessageBox.StandardButton.Cancel:return False
            if answer==QMessageBox.StandardButton.Yes:
                try:self.save_result();self.dirty=False
                except ValueError as error:QMessageBox.warning(self,'未保存',str(error));return False
        self.approved=True;return True
    def reject(self):
        if self.confirm_close():super().reject()
    def closeEvent(self,event):
        if not self.confirm_close():event.ignore();return
        super().closeEvent(event)

class SummaryDialog(AnalysisDialog):
    def __init__(self,manager,article,context,parent=None):
        super().__init__(manager,'AI总结文章 · '+article.get('title',''),parent);self.article=article;self.context=context
        article_title=plain_label(article.get('title') or '无标题');article_title.setStyleSheet('font-weight:700;color:#B45309;');self.body_layout.insertWidget(2,article_title)
        self.export_task=None
        tools=QHBoxLayout();tools.addWidget(QLabel('展示方式'));self.view_mode=QComboBox();self.view_mode.addItem('简报','brief');self.view_mode.addItem('新闻稿','news');self.view_mode.setCurrentIndex(max(0,self.view_mode.findData('brief')));tools.addWidget(self.view_mode)
        self.pdf_button=QPushButton('导出新闻稿PDF');self.pdf_button.clicked.connect(self.export_pdf);tools.addWidget(self.pdf_button);self.model_pdf_button=QPushButton('保存模型图');self.model_pdf_button.clicked.connect(self.save_model_pdf);tools.addWidget(self.model_pdf_button);tools.addStretch();self.body_layout.insertLayout(3,tools)
        from report_design import ReportPresentation
        self.presentation=ReportPresentation();self.body_layout.insertWidget(self.body_layout.indexOf(self.output),self.presentation,1);self.output.hide();self.view_mode.currentIndexChanged.connect(self.change_view)
        from brief_language import BriefLanguage
        self.language_control=BriefLanguage(self,manager,self.presentation,lambda:self.result,self.translated);self.bottom_layout.insertWidget(1,self.language_control.button)
        self.epoch=manager.repository.epoch(article['article_key']);self.run_button.setText('重新进行AI总结')
        delete=QPushButton('删除AI总结');delete.setProperty('secondary',True);delete.clicked.connect(self.delete);self.body_layout.addWidget(delete)
        cached=manager.repository.summary(article['article_key'])
        if cached:self.display(cached,saved=True)
        else:self.run_button.setText('AI总结文章');self.status.setText('优先读取已链接 PDF，否则读取 DOI 网页。取不到正文时只依据摘要并明确标注；不会声称已读全文。')
        self.change_view()
    def change_view(self):
        mode=self.view_mode.currentData();self.manager.store.database.save_settings({'ai_report_mode':mode});self.presentation.set_result(self.result or {},self.article,mode);self.pdf_button.setEnabled(mode=='news' and bool(self.result and self.result.get('presentation')) and not self.task and not self.export_task)
        self.language_control.refresh(mode=='brief')
        generation=(self.result or {}).get('diagram_generation') or {};self.model_pdf_button.setEnabled(generation.get('format') in ('pdf','png','jpg','tif','bmp','webp') and generation.get('status')=='completed' and not self.task)
        page=getattr(self.manager.parent(),'settings_page',None)
        if page and hasattr(page,'ai_report_mode'):
            field=page.ai_report_mode;field.blockSignals(True);field.setCurrentIndex(max(0,field.findData(mode)));field.blockSignals(False)
    def display(self,result,saved=False):
        super().display(result,saved);self.change_view()
    def translated(self,result):self.result=result;self.dirty=True;self.change_view()
    def save_model_pdf(self):
        generation=(self.result or {}).get('diagram_generation') or {}
        if generation.get('format') not in ('pdf','png','jpg','tif','bmp','webp') or generation.get('status')!='completed':return
        from model_artifact import figure_bytes
        from path_dialog import QFileDialog
        extension=generation['format'];aliases=('jpg','jpeg') if extension=='jpg' else ('tif','tiff') if extension=='tif' else (extension,);file_filter=extension.upper()+' ('+' '.join('*.'+value for value in aliases)+')';path,_=QFileDialog.getSaveFileName(self,'保存AI原始模型图','BiomedReader_模型图.'+extension,file_filter)
        if not path:return
        if not path.lower().endswith(tuple('.'+value for value in aliases)):path+='.'+extension
        try:
            from pathlib import Path
            Path(path).write_bytes(figure_bytes(generation['artifact']));QMessageBox.information(self,'保存完成','已保存模型直接生成的原始'+extension.upper()+'文件。')
        except (ValueError,OSError,KeyError) as error:QMessageBox.warning(self,'未保存',str(error))
    def export_pdf(self):
        if self.task or self.export_task or not self.result or not self.result.get('presentation'):return
        from path_dialog import QFileDialog
        from report_design import export_news_pdf
        path,_=QFileDialog.getSaveFileName(self,'导出文章AI新闻稿','BiomedReader_文章新闻稿.pdf','PDF (*.pdf)')
        if not path:return
        if not path.lower().endswith('.pdf'):path+='.pdf'
        result=self.result;article=self.article;self.pdf_button.setEnabled(False)
        self.export_task=self.manager.submit(self,lambda:dict(path=path,pages=export_news_pdf(path,[(article,result)])),'export_done','export_failed')
    def export_done(self,result):self.export_task=None;self.change_view();QMessageBox.information(self,'导出完成','已保存带逐页模型水印的PDF：\n'+result['path'])
    def export_failed(self,message):self.export_task=None;self.change_view();QMessageBox.warning(self,'导出未完成',message)
    def confirm_close(self):
        if self.language_control.task:self.status.setText('英文简报尚在翻译，可最小化窗口，完成后再关闭。');return False
        if self.export_task:self.status.setText('PDF尚在导出，可最小化窗口，完成后再关闭。');return False
        return super().confirm_close()
    def run(self):
        try:
            engine=self.engine();web_fetch=self.manager.article_reader.get;self.epoch=self.manager.repository.epoch(self.article['article_key'])
            epoch=self.epoch;engine.cancelled=lambda:self.stop.is_set() or self.manager.repository.epoch(self.article['article_key'])!=epoch or (self.context.startswith('profile:') and not self.manager.store.database.get_profile(int(self.context.split(':')[1])))
            self.start(lambda:engine.summarize(self.manager.store.database,self.article,self.progress_callback,web_fetch))
        except ValueError as error:QMessageBox.information(self,'无法分析',str(error))
    def save_result(self):self.manager.repository.save_summary(self.article['article_key'],self.result,self.context,self.epoch);self.manager.changed.emit()
    def delete(self):
        if self.task:return
        if QMessageBox.question(self,'删除AI总结','删除该文章已保存的 AI 总结？引用它的报告将显示“总结已删除”。')!=QMessageBox.StandardButton.Yes:return
        self.manager.repository.delete_summary(self.article['article_key']);self.result=None;self.dirty=False;self.output.clear();self.change_view();self.status.setText('已删除，可重新生成。');self.manager.changed.emit()

class ScientificQuestionsDialog(AnalysisDialog):
    def __init__(self,manager,parent=None):
        super().__init__(manager,'AI提出科学问题',parent);self.articles=[];self.evidence_notes={};self.turns=[];self.saved_history_id=None;self.pinned_model=None;self.export_task=None
        self.knowledge_only=type(self).__name__ in ('ScientificQuestionsDialog','ReviewerDialog')
        select=QPushButton('选择收藏文章…');self.select_articles_button=select;select.clicked.connect(self.select_articles);self.body_layout.insertWidget(1,select)
        self.selection=plain_label('尽量选择同一主题分组内相互相关的文章；文献越多，读取与模型调用费用越高。',True);self.body_layout.insertWidget(2,self.selection)
        self.topic=QPlainTextEdit();self.topic.setPlaceholderText('输入一个具体课题领域：研究对象＋机制／疾病／细胞过程＋希望探索的方向或限制。例如：IRAK1 在转录相关 DNA 损伤修复中的调控机制。避免只写“生物学”或“肿瘤”等过宽主题。');self.topic.setMaximumHeight(110);self.body_layout.insertWidget(3,self.topic)
        self.run_button.setText('逐篇读取并提出科学问题')
        self.followup=QPlainTextEdit();self.followup.setMaximumHeight(70);self.followup.setPlaceholderText('继续对话时填写本次补充要求；可增加新文章，再更新分类与科学问题。');self.body_layout.insertWidget(4,self.followup)
        from PySide6.QtWidgets import QTextBrowser
        self.research_view=QTextBrowser();self.research_view.setOpenExternalLinks(False);self.research_view.setStyleSheet('QTextBrowser {background:white;border:1px solid #DCE4E8;border-radius:12px;padding:22px;}');self.body_layout.insertWidget(self.body_layout.indexOf(self.output),self.research_view,1);self.output.hide()
        self.pdf_button=QPushButton('导出科学问题报告PDF');self.pdf_button.clicked.connect(self.export_pdf);self.body_layout.addWidget(self.pdf_button,0,Qt.AlignmentFlag.AlignLeft)
        self.result_model=plain_label('',True);self.body_layout.insertWidget(self.body_layout.indexOf(self.output),self.result_model)
        self.tabs=QTabWidget();inputs=QWidget();form=QVBoxLayout(inputs)
        for widget in (select,self.selection,self.topic,self.followup,self.run_button):
            self.body_layout.removeWidget(widget)
            if widget in (select,self.run_button):form.addWidget(widget,0,Qt.AlignmentFlag.AlignLeft)
            else:form.addWidget(widget)
        form.addStretch();self.tabs.addTab(inputs,'文献与对话')
        self.body_layout.removeWidget(self.research_view);self.body_layout.removeWidget(self.pdf_button);result_page=QWidget();result_layout=QVBoxLayout(result_page);result_layout.addWidget(self.pdf_button,0,Qt.AlignmentFlag.AlignLeft);result_layout.addWidget(self.research_view,1);self.tabs.addTab(result_page,'研究报告')
        self.body_layout.insertWidget(self.body_layout.indexOf(self.output),self.tabs,1)
        if type(self).__name__=='ScientificQuestionsDialog':
            from expert_ui import knowledge_combo
            self.knowledge=knowledge_combo(self)
        if self.knowledge_only:
            select.hide();self.run_button.setText('提出领域科学问题');self.tabs.setTabText(0,'领域与对话')
            self.topic.setPlaceholderText('描述希望探索的研究领域和关注的宏观矛盾或瓶颈，例如：转录与复制时序、核内空间组织及其对转录复制叉冲突的影响。不必预先指定某个蛋白或分子的作用。')
            self.followup.setPlaceholderText('补充新的视角、领域瓶颈或希望突破的理论假设；可更换或不加载领域知识库。')
            self.update_selection()
            if type(self).__name__=='ScientificQuestionsDialog':self.info.setText('输入研究领域，可选加载已整理的领域知识库来启发视角；不加载时依据领域与模型一般知识推演，未实时检索文献。问题是待验证的探索方向，不保证全球新颖性。')
    def restore_record(self,record):
        inputs=record['inputs'];self.saved_history_id=record['id'];self.topic.setPlainText(inputs.get('topic',''));self.articles=inputs.get('articles',[]);self.evidence_notes=inputs.get('evidence_notes') or record['result'].get('evidence_notes') or {};self.turns=inputs.get('turns') or [];self.pinned_model=(record['result'].get('provider'),record['result'].get('model'));self.update_selection();self.display(record['result'],saved=True);self.run_button.setText('继续对话并更新结果')
        if hasattr(self,'knowledge'):self.knowledge.setCurrentIndex(max(0,self.knowledge.findData(inputs.get('knowledge_id'))))
        if not self.turns and record['result'].get('text'):self.turns=[dict(message=inputs.get('topic',''),provider=record['result'].get('provider'),model=record['result'].get('model'),legacy_text=record['result']['text'])]
    def update_selection(self):
        if type(self).__name__=='ReviewerDialog':self.selection.setText('可选加载已整理的领域知识库作为审稿参考；不重新选择收藏文章。不加载时依据研究领域和您提供的稿件审阅。当前参考资料 '+str(len(self.evidence_notes))+' 篇。')
        elif getattr(self,'knowledge_only',False):self.selection.setText('领域知识库可选；加载时复用已整理的文献资料，不重新选择收藏文章。资料用于启发领域视角，不将问题限定为单篇论文的后续实验。当前资料 '+str(len(self.evidence_notes))+' 篇。')
        else:self.selection.setText(f'文献共 {len(self.articles)} 篇；已有笔记 {len(getattr(self,"evidence_notes",{}))} 篇。建议同一主题；续聊复用已读证据，只读取新增文章。')
    def select_articles(self):
        from ui import FavoriteExportDialog
        dialog=FavoriteExportDialog(self.manager.store.database,[row['article_key'] for row in self.articles],self);dialog.setWindowTitle('选择用于 AI 提问的收藏文章');dialog.explanation.setText('推荐选择同一分组主题内相关的论文。只处理右侧勾选的文章；原始 PDF 不会直接上传，提取的文字会发送给所选 AI 服务。');dialog.include_pdf.hide();dialog.action_buttons.button(QDialogButtonBox.StandardButton.Save).setText('选择这些文章')
        if dialog.exec()==QDialog.DialogCode.Accepted:
            keys=set(dialog.checked(dialog.articles));selected=[row for row in dialog.article_records if row['article_key'] in keys];old=[row for row in self.articles if row['article_key'] in self.evidence_notes];self.articles=list({row['article_key']:row for row in old+selected}.values());self.update_selection()
    def run(self):
        topic=self.topic.toPlainText().strip()
        if len(topic)<3:QMessageBox.information(self,'请完善输入','请输入希望探究的研究领域；领域知识库可选。');return
        try:
            engine=self.engine()
            if hasattr(self,'knowledge'):
                from expert_ui import apply_knowledge
                if not apply_knowledge(self,engine):return
            articles=list(self.articles);web_fetch=self.manager.article_reader.get
            if self.pinned_model and self.pinned_model!=(engine.provider,engine.model):
                old=' / '.join(str(v or '未知') for v in self.pinned_model);new=engine.provider+' / '+engine.model
                if QMessageBox.question(self,'对话模型已变化',f'原对话模型：{old}\n当前模型：{new}\n是否将已有对话及文献笔记交给当前模型继续？',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
            self.generated_inputs=dict(topic=topic,articles=articles,knowledge_id=self.knowledge.currentData() if hasattr(self,'knowledge') else None);self.pending_message=self.followup.toPlainText().strip() or topic
            conversation=dict(evidence_notes=dict(self.evidence_notes),turns=list(self.turns),message=self.pending_message)
            self.start(lambda:engine.questions(self.manager.store.database,articles,topic,self.progress_callback,web_fetch,conversation=conversation))
        except ValueError as error:QMessageBox.information(self,'无法分析',str(error))
    def display(self,result,saved=False):
        self.result=result;self.dirty=not saved;self.reasoning_note.setText(__import__('reasoning').result_label(result));self.evidence_notes=result.get('evidence_notes') or self.evidence_notes;self.pinned_model=(result.get('provider'),result.get('model'))
        if not saved:
            self.turns.append(dict(message=getattr(self,'pending_message',self.topic.toPlainText()),provider=result.get('provider'),model=result.get('model'),created_at=result.get('created_at'),research=result.get('research')));self.followup.clear()
        from research_report import fill_research_document
        fill_research_document(self.research_view.document(),result)
        from PySide6.QtGui import QTextCursor
        self.research_view.moveCursor(QTextCursor.MoveOperation.Start);self.research_view.verticalScrollBar().setValue(0);self.run_button.setText('继续对话并更新结果');self.update_selection()
        self.result_model.setText('分析模型：'+str(result.get('provider') or '')+' / '+str(result.get('model') or '')+(' · 知识库资料 '+str(result.get('read_count',0))+' 篇' if result.get('read_count') else ' · 未加载知识库，领域探索')+' · 对话 '+str(len(self.turns))+' 轮')
        self.tabs.setCurrentIndex(1)
        self.pdf_button.setEnabled(bool(result.get('research')) and len(result['research']['questions'])>=10 and not result.get('incomplete'))
        self.status.setText(result.get('validation_error') or ('本次分析未完成，已保留已有资料，可继续对话。' if result.get('incomplete') else '已加载保存的对话，可补充要求或更换知识库后继续。' if saved else '结果已更新；问题属于待验证的研究建议，不保证全球首创。'))
    def export_pdf(self):
        if self.task or self.export_task or not self.result or not self.result.get('research'):return
        from path_dialog import QFileDialog
        from report_design import export_news_pdf
        path,_=QFileDialog.getSaveFileName(self,'导出科学问题报告','BiomedReader_科学问题报告.pdf','PDF (*.pdf)')
        if not path:return
        if not path.lower().endswith('.pdf'):path+='.pdf'
        result=self.result;self.pdf_button.setEnabled(False);self.export_task=self.manager.submit(self,lambda:dict(path=path,pages=export_news_pdf(path,[(dict(title=result.get('topic','')),result)],research=True)),'export_done','export_failed')
    def export_done(self,result):self.export_task=None;self.pdf_button.setEnabled(True);QMessageBox.information(self,'导出完成','已保存带逐页模型水印的PDF：\n'+result['path'])
    def export_failed(self,message):self.export_task=None;self.pdf_button.setEnabled(True);QMessageBox.warning(self,'导出未完成',message)
    def confirm_close(self):
        if self.export_task:self.status.setText('PDF尚在导出，可最小化窗口，完成后再关闭。');return False
        return super().confirm_close()
    def save_result(self):
        inputs=getattr(self,'generated_inputs',dict(topic=self.topic.toPlainText(),articles=self.articles))
        inputs={**inputs,'evidence_notes':self.evidence_notes,'turns':self.turns};result={key:value for key,value in self.result.items() if key!='evidence_notes'}
        self.saved_history_id=self.manager.repository.save_history('questions',inputs['topic'][:100] or '科学问题报告',inputs,result,self.saved_history_id)
        self.manager.changed.emit()

class AIAssistantPage(QWidget):
    def __init__(self,manager,parent=None):
        super().__init__(parent);self.manager=manager;layout=QVBoxLayout(self);title=QLabel('AI助手');title.setObjectName('PageTitle');layout.addWidget(title)
        layout.addWidget(plain_label('建立领域知识库，可选加载用于选题探索或同行评审；也可仅输入研究领域，或提供自己的稿件。可与文章总结、关键词推荐和增量报告同时运行。',True))
        from card_board import CardBoard
        from expert_ui import ExpertBoard,KnowledgeHub,ReviewerDialog
        self.assistant_board=ExpertBoard(columns=4);self.assistant_board.setMinimumHeight(360)
        self.assistant_board.add_card('knowledge','AI模型训练','领域文献知识库 · 非权重训练\n支持多个模型与持续补充文献',lambda _:show_window(KnowledgeHub(manager,self)))
        card=self.assistant_board.add_card('questions','AI提出科学问题','可选加载领域知识库\n探索宏观、跨尺度且可检验的科学问题',lambda _:show_window(ScientificQuestionsDialog(manager,self)));card.setToolTip('输入研究领域，可选加载知识库后探索科学问题');card.mouseMoveEvent=lambda event:None
        history=QPushButton('历史记录',card);history.setProperty('secondary',True);history.setStyleSheet('padding:6px 10px;');history.setSizePolicy(QSizePolicy.Policy.Fixed,QSizePolicy.Policy.Fixed);history.clicked.connect(lambda:show_window(HistoryDialog(manager,'questions',self)));history_row=QHBoxLayout();history_row.addStretch();history_row.addWidget(history);card.layout().addLayout(history_row);layout.addWidget(self.assistant_board,1)
        review=self.assistant_board.add_card('reviewer','AI reviewer','可选加载领域知识库 + 自己的稿件\n英文同行评审与持续对话',lambda _:show_window(ReviewerDialog(manager,self)))
        def review_history():
            from expert_history import open_history
            open_history(manager,'reviewer',ReviewerDialog,self)
        review_history_button=QPushButton('历史记录');review_history_button.setProperty('secondary',True);review_history_button.setStyleSheet('padding:6px 10px;');review_history_button.setSizePolicy(QSizePolicy.Policy.Fixed,QSizePolicy.Policy.Fixed);review_history_button.clicked.connect(review_history);review_history_row=QHBoxLayout();review_history_row.addStretch();review_history_row.addWidget(review_history_button);review.layout().addLayout(review_history_row)
        def open_finder(_):
            from ai_finder_ui import FinderHub
            show_window(FinderHub(manager,self))
        finder=self.assistant_board.add_card('finder','AI寻找文章','描述研究方向、待解决问题并提供资料\n检索真实文献，寻找可借鉴的方法与理论',open_finder);finder.mouseMoveEvent=lambda event:None

class AIReportDialog(QDialog):
    def __init__(self,coordinator,profile_id,parent=None):
        super().__init__(parent);window_flags(self);self.coordinator=coordinator;self.manager=coordinator.manager;self.profile_id=profile_id;self.page=0;self.dirty=False;self.approved=False;self.editorial_task=None
        self.setWindowTitle('AI报告');self.resize(1000,820);layout=QVBoxLayout(self);self.heading=plain_label();layout.addWidget(self.heading)
        row=QHBoxLayout();self.generate=QPushButton('生成／重新生成当前新增批次报告');self.generate.clicked.connect(lambda:coordinator.generate(profile_id));row.addWidget(self.generate);row.addStretch();previous=QPushButton('上一页');previous.clicked.connect(lambda:self.change_page(-1));row.addWidget(previous);following=QPushButton('下一页');following.clicked.connect(lambda:self.change_page(1));row.addWidget(following);layout.addLayout(row)
        tools=QHBoxLayout();tools.addWidget(QLabel('报告模式'));self.mode=QComboBox();self.mode.addItem('简报','brief');self.mode.addItem('新闻稿','news');self.mode.setCurrentIndex(max(0,self.mode.findData('brief')));self.mode.currentIndexChanged.connect(self.change_mode);tools.addWidget(self.mode)
        self.export_scope=QComboBox();self.export_scope.addItems(['当前文章','本批全部文章']);tools.addWidget(self.export_scope);self.export_button=QPushButton('导出新闻稿PDF');self.export_button.clicked.connect(self.export_pdf);tools.addWidget(self.export_button);tools.addStretch()
        stop=QPushButton('停止本批后续AI调用');stop.setProperty('secondary',True);stop.clicked.connect(lambda:coordinator.cancel(profile_id));tools.addWidget(stop);layout.addLayout(tools)
        self.report_status=plain_label('',True);layout.addWidget(self.report_status)
        self.stack=QStackedWidget();layout.addWidget(self.stack,1)
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True);self.content=QWidget();self.rows=QGridLayout(self.content);self.rows.setAlignment(Qt.AlignmentFlag.AlignTop);self.scroll.setWidget(self.content);self.stack.addWidget(self.scroll)
        detail=QWidget();detail_layout=QVBoxLayout(detail);back=QPushButton('← 返回文章标题');back.setProperty('secondary',True);back.clicked.connect(lambda:self.stack.setCurrentIndex(0));detail_layout.addWidget(back,0,Qt.AlignmentFlag.AlignLeft)
        self.detail_title=plain_label();self.detail_title.setStyleSheet('font-weight:700;color:#B45309;');detail_layout.addWidget(self.detail_title)
        self.render_button=QPushButton('生成新版图文报告');self.render_button.clicked.connect(self.generate_editorial);detail_layout.addWidget(self.render_button,0,Qt.AlignmentFlag.AlignLeft)
        from report_design import ReportPresentation
        self.presentation=ReportPresentation();self.detail_text=self.presentation.news;detail_layout.addWidget(self.presentation,1);self.stack.addWidget(detail);self.selected_key=None
        bottom=QHBoxLayout();save=QPushButton('保存AI报告');save.clicked.connect(self.save);bottom.addWidget(save)
        from brief_language import BriefLanguage
        self.language_summary=None;self.language_control=BriefLanguage(self,self.manager,self.presentation,lambda:self.language_summary,self.translated);bottom.addWidget(self.language_control.button)
        bottom.addStretch();close=QPushButton('关闭');close.clicked.connect(self.reject);bottom.addWidget(close);layout.addLayout(bottom)
        coordinator.changed.connect(self.reload);self.manager.changed.connect(self.reload);self.reload()
        self.change_mode()
    def reload(self):
        self.report=self.coordinator.drafts.get(self.profile_id) or self.manager.repository.report(self.profile_id)
        self.dirty=self.profile_id in self.coordinator.drafts
        if not self.report:self.heading.setText('尚无报告。可为当前新增批次生成；零新增不会调用 AI，也不会替换上次已保存报告。');return
        keys=self.report['keys'];ranges='；'.join(source+'：'+str(value.get('from') or '完整范围')+' 至 '+str(value.get('to') or '') for source,value in (self.report.get('window') or {}).items())
        self.page=min(self.page,max(0,(len(keys)-1)//24))
        self.heading.setText('搜索时间范围：'+ranges+'\n新文章数量：'+str(len(keys))+f' · 第 {self.page+1}/{max(1,math.ceil(len(keys)/24))} 页（每行4篇，点击标题查看总结）')
        while self.rows.count():
            item=self.rows.takeAt(0)
            if item.widget():item.widget().deleteLater()
        for index,key in enumerate(keys[self.page*24:(self.page+1)*24]):
            article=self.manager.store.database.article_record(key) or {};title=article.get('title') or key
            card=QPushButton();card.setMinimumHeight(140);card.setMinimumWidth(0);card.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
            card.setStyleSheet('QPushButton {background:white;border:1px solid #E8D5C4;border-radius:12px;padding:12px;} QPushButton:hover {border:2px solid #F28A22;background:#FFF7EB;}')
            box=QVBoxLayout(card);label=plain_label(title);label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents);box.addWidget(label);card.setToolTip(title);card.clicked.connect(lambda checked=False,k=key:self.open_article(k));self.rows.addWidget(card,index//4,index%4)
        for col in range(4):self.rows.setColumnStretch(col,1)
        if self.selected_key and self.stack.currentIndex()==1:self.open_article(self.selected_key)
        self.generate.setEnabled(self.profile_id not in self.coordinator.running)
    def open_article(self,key):
        if self.selected_key!=key:self.export_scope.setCurrentIndex(0)
        self.selected_key=key;article=self.manager.store.database.article_record(key) or {};self.detail_title.setText(article.get('title') or key)
        summary=self.manager.repository.summary(key);failures=(self.report or {}).get('errors',{})
        self.language_summary=summary
        self.presentation.set_result(summary or dict(text=failures.get(key,'总结已删除或尚未生成。')),article,self.mode.currentData());self.stack.setCurrentIndex(1)
        self.language_control.refresh(self.mode.currentData()=='brief')
        self.render_button.setText('重新生成图文报告' if summary and summary.get('presentation') else '生成新版图文报告')
        self.report_status.setText(('分析模型：'+summary.get('provider','')+' / '+summary.get('model','')+' · '+__import__('reasoning').result_label(summary)+' · 读取范围：'+summary.get('coverage','')) if summary else failures.get(key,'尚无总结，可生成图文报告。'))
    def change_mode(self):
        mode=self.mode.currentData();self.manager.store.database.save_settings({'ai_report_mode':mode});self.export_button.setEnabled(mode=='news' and not self.editorial_task)
        if self.selected_key:self.open_article(self.selected_key)
        self.language_control.refresh(mode=='brief')
        settings_page=getattr(self.coordinator.window,'settings_page',None)
        if settings_page and hasattr(settings_page,'ai_report_mode'):
            control=settings_page.ai_report_mode;control.blockSignals(True);control.setCurrentIndex(max(0,control.findData(mode)));control.blockSignals(False)
    def generate_editorial(self):
        if self.editorial_task or not self.selected_key:return
        provider=self.manager.store.database.get_settings().get('ai_report_provider','deepseek')
        if provider not in PROVIDERS or not self.manager.store.config(provider)['enabled']:provider=next((p for p in PROVIDERS if self.manager.store.config(p)['enabled']),None)
        if not provider:QMessageBox.information(self,'请开启模型','请先在AI部署中设置并开启模型。');return
        key=self.selected_key;article=self.manager.store.database.article_record(key);epoch=self.manager.repository.epoch(key)
        try:engine=AnalysisEngine(self.manager.store,provider)
        except ValueError as error:QMessageBox.information(self,'无法生成',str(error));return
        engine.cancelled=lambda:not self.manager.store.database.get_profile(self.profile_id) or self.manager.repository.epoch(key)!=epoch
        self.render_button.setEnabled(False);self.export_button.setEnabled(False);self.report_status.setText('正在读取原文并生成简报、新闻稿和机制示意图；可最小化本窗口。')
        def work():return dict(key=key,epoch=epoch,result=engine.summarize(self.manager.store.database,article,web_fetch=self.manager.article_reader.get))
        self.editorial_task=self.manager.submit(self,work,'editorial_loaded','editorial_failed')
    def editorial_loaded(self,payload):
        self.editorial_task=None;self.render_button.setEnabled(True)
        try:self.manager.repository.save_summary(payload['key'],payload['result'],'report:'+str(self.profile_id),payload['epoch'])
        except ValueError as error:self.editorial_failed(str(error));return
        self.change_mode();self.manager.changed.emit()
        if not payload['result'].get('presentation'):self.report_status.setText('模型未返回完整的结构化稿件，保留文字总结；未自动重复付费调用。可人工核查后重试。')
    def editorial_failed(self,message):
        self.editorial_task=None;self.render_button.setEnabled(True);self.export_button.setEnabled(self.mode.currentData()=='news');self.report_status.setText(message);QMessageBox.warning(self,'报告操作未完成',message)
    def export_pdf(self):
        if self.editorial_task or self.mode.currentData()!='news' or not self.report:return
        keys=[self.selected_key] if self.selected_key and self.export_scope.currentIndex()==0 else self.report['keys']
        items=[(self.manager.store.database.article_record(key) or {},self.manager.repository.summary(key)) for key in keys]
        if any(not summary or not summary.get('presentation') for _,summary in items):QMessageBox.information(self,'请先生成新闻稿','所选范围包含没有新版图文报告的文章，请先逐篇生成后导出。');return
        from path_dialog import QFileDialog
        path,_=QFileDialog.getSaveFileName(self,'导出带模型水印的新闻稿','BiomedReader_研究新闻稿.pdf','PDF (*.pdf)')
        if not path:return
        if not path.lower().endswith('.pdf'):path+='.pdf'
        from report_design import export_news_pdf
        self.export_button.setEnabled(False);self.render_button.setEnabled(False);self.report_status.setText('正在排版并导出PDF……')
        def work():return dict(path=path,pages=export_news_pdf(path,items))
        self.editorial_task=self.manager.submit(self,work,'pdf_exported','editorial_failed')
    def pdf_exported(self,result):
        self.editorial_task=None;self.render_button.setEnabled(True);self.export_button.setEnabled(self.mode.currentData()=='news');self.report_status.setText('已导出 '+str(result['pages'])+' 页：'+result['path']);QMessageBox.information(self,'导出完成','新闻稿PDF已保存，每页均含Biomed Reader和分析模型名称水印。')
    def change_page(self,delta):
        if not self.report:return
        self.page=max(0,min(max(0,(len(self.report['keys'])-1)//24),self.page+delta));self.stack.setCurrentIndex(0);self.reload();self.scroll.verticalScrollBar().setValue(0)
    def save(self):
        if self.report and self.manager.repository.save_report(self.profile_id,self.report):self.coordinator.drafts.pop(self.profile_id,None);self.dirty=False;self.heading.setText(self.heading.text()+'\n已保存，可离线查看。')
    def translated(self,result):
        key=self.selected_key
        if key:
            try:self.manager.repository.save_summary(key,result,'report:'+str(self.profile_id),self.manager.repository.epoch(key))
            except ValueError as error:QMessageBox.warning(self,'未保存翻译',str(error));return
            self.language_summary=result;self.presentation.set_result(result,self.manager.store.database.article_record(key) or {},self.mode.currentData())
    def confirm(self):
        if self.language_control.task:self.report_status.setText('英文简报尚在翻译，请等待完成后关闭。');return False
        if self.editorial_task:self.report_status.setText('报告生成或导出尚未完成，可最小化窗口，完成后再关闭。');return False
        if self.approved:return True
        if self.dirty:
            answer=QMessageBox.question(self,'保存AI报告','是否保存本次 AI 报告？保存后才替换上次报告。',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No|QMessageBox.StandardButton.Cancel)
            if answer==QMessageBox.StandardButton.Cancel:return False
            if answer==QMessageBox.StandardButton.Yes:self.save()
            else:self.coordinator.drafts.pop(self.profile_id,None)
        self.approved=True;return True
    def reject(self):
        if self.confirm():super().reject()
    def closeEvent(self,event):
        if not self.confirm():event.ignore();return
        super().closeEvent(event)

from PySide6.QtCore import QObject,Signal,QTimer
class ReportReceiver(QObject):
    def __init__(self,coordinator,identifier):super().__init__(coordinator);self.coordinator=coordinator;self.identifier=identifier
    def completed(self,result):self.coordinator.completed(result);self.coordinator.jobs.pop(self.identifier,None);self.deleteLater()
    def failed(self,message):self.coordinator.failed_for(self.identifier,message);self.coordinator.jobs.pop(self.identifier,None);self.deleteLater()

class AIReportCoordinator(QObject):
    changed=Signal()
    def __init__(self,manager,window):
        super().__init__(window);self.manager=manager;self.window=window;self.running=set();self.drafts={};self.jobs={};self.zero_notice=None
    def after_refresh(self,profile_id,report):
        if not report.incremental:return
        keys=list(dict.fromkeys(getattr(report,'incremental_new_keys',[])))
        if not keys:
            if self.zero_notice is None or not shiboken6.isValid(self.zero_notice):
                from notice_ui import NoNewArticlesDialog
                self.zero_notice=NoNewArticlesDialog(self.window)
            profile=self.window.database.get_profile(profile_id) or {};self.zero_notice.setInformativeText(self.zero_notice.informativeText()+'\n'+profile.get('name',str(profile_id)));self.zero_notice.show();return
        batch=dict(keys=keys,window=getattr(report,'ai_search_window',{}),created_at=now())
        self.manager.repository.save_batch(profile_id,batch)
        if self.window.database.get_settings().get('ai_incremental_reports','0')=='1':self.generate(profile_id)
    def generate(self,profile_id):
        if profile_id in self.running:return
        batch=self.manager.repository.batch(profile_id)
        if not batch or not batch.get('keys'):QMessageBox.information(self.window,'无新文章','无新文章');return
        settings=self.window.database.get_settings();provider=settings.get('ai_report_provider')
        if provider not in PROVIDERS or not self.manager.store.config(provider)['enabled']:
            provider=next((p for p in PROVIDERS if self.manager.store.config(p)['enabled']),None)
        if not provider:QMessageBox.information(self.window,'AI报告未启动','请先在 AI部署 中设置并开启模型。新文章批次已保留，可稍后点击 AI报告。');return
        try:engine=AnalysisEngine(self.manager.store,provider)
        except ValueError as error:QMessageBox.information(self.window,'无法启动',str(error));return
        self.running.add(profile_id);self.changed.emit()
        stop=threading.Event();self.jobs[('stop',profile_id)]=stop;engine.cancelled=stop.is_set
        def work():
            errors={};keys=batch['keys']
            for index,key in enumerate(keys,1):
                if stop.is_set():
                    for remaining in keys[index-1:]:errors[remaining]='已停止后续调用。'
                    break
                if not self.window.database.get_profile(profile_id):raise ValueError('方案已删除，已停止后续 AI 调用。')
                self.manager.progress.emit('report:'+str(profile_id),f'AI报告 {index}/{len(keys)}')
                epoch=self.manager.repository.epoch(key);cached=self.manager.repository.summary(key)
                try:
                    if not cached or (cached.get('provider'),cached.get('model'))!=(engine.provider,engine.model):
                        article=self.window.database.article_record(key);cached=engine.summarize(self.window.database,article,web_fetch=self.manager.article_reader.get)
                    self.manager.repository.save_summary(key,cached,'report:'+str(profile_id),epoch)
                except Exception as error:
                    errors[key]=str(error)[:400]
                    from ai_service import AIAPIError
                    if isinstance(error,(AIAPIError,InterruptedError)):
                        for remaining in keys[index:]:errors[remaining]='未继续调用：AI 服务返回错误，请检查额度、网络或模型。'
                        break
            return dict(profile_id=profile_id,keys=keys,window=batch['window'],errors=errors,created_at=now(),batch_id=batch['created_at'])
        receiver=ReportReceiver(self,profile_id);self.jobs[profile_id]=receiver
        self.manager.submit(receiver,work,'completed','failed')
    def completed(self,result):
        identifier=result['profile_id'];self.running.discard(identifier)
        self.jobs.pop(('stop',identifier),None)
        if self.window.database.get_profile(identifier):
            if any(key not in result.get('errors',{}) for key in result['keys']):self.manager.repository.retire_previous_report(identifier,result['keys'])
            self.drafts[identifier]=result
        self.window.home.reload();self.changed.emit()
        newest=self.manager.repository.batch(identifier)
        if newest and newest['created_at']!=result.get('batch_id') and self.window.database.get_settings().get('ai_incremental_reports','0')=='1':QTimer.singleShot(0,lambda:self.generate(identifier))
    def failed_for(self,identifier,message):
        # A deleted profile is the only expected job-wide failure; per-article
        # failures are returned as report rows rather than destroying old reports.
        self.running.discard(identifier);self.changed.emit()
        self.jobs.pop(('stop',identifier),None)
        QMessageBox.warning(self.window,'AI报告未完成',message)
    def open(self,profile_id):show_window(AIReportDialog(self,profile_id,self.window))
    def cancel(self,profile_id):
        event=self.jobs.get(('stop',profile_id))
        if event:event.set()
