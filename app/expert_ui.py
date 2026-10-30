"""Persistent evidence libraries, not model-weight training; grounded manuscript review."""
import json
from pathlib import Path
from html import escape
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter,QPainterPath,QColor,QPen
from PySide6.QtWidgets import QDialog,QVBoxLayout,QPushButton,QInputDialog,QComboBox,QLabel,QMessageBox,QListWidget,QListWidgetItem
from ai_workflow_ui import ScientificQuestionsDialog,show_window
from ai_finder import read_attachment,read_image
from ai_content import read_article
from ai_repository import now
from card_board import CardBoard

def knowledge_combo(dialog):
    combo=QComboBox();combo.setMaximumWidth(560);combo.addItem('不加载领域知识库',None)
    for row in dialog.manager.repository.histories('knowledge'):combo.addItem(row['title'],row['id'])
    combo.knowledge_caption=QLabel('领域知识库（证据记忆，不修改模型权重）')
    dialog.body_layout.insertWidget(1,combo.knowledge_caption);dialog.body_layout.insertWidget(2,combo)
    return combo

def apply_knowledge(dialog,engine):
    identifier=dialog.knowledge.currentData()
    if identifier is None:
        if getattr(dialog,'knowledge_only',False):dialog.articles=[];dialog.evidence_notes={};dialog.update_selection()
        return True
    record=dialog.manager.repository.history(identifier)
    if not record:raise ValueError('领域知识库已删除。')
    result=record['result']
    if (result.get('provider'),result.get('model'))!=(engine.provider,engine.model):
        if QMessageBox.question(dialog,'知识库模型不同','该知识库由其它模型阅读整理。是否将其证据笔记交给当前模型？',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return False
    notes=result.get('evidence_notes') or record['inputs'].get('evidence_notes') or {}
    if getattr(dialog,'knowledge_only',False):
        if not notes:raise ValueError('该领域知识库尚无已读文献资料，请先在AI模型训练中完善知识库，或选择不加载。')
        dialog.evidence_notes=dict(notes)
        rows={r['article_key']:r for r in record['inputs'].get('articles',[]) if r.get('article_key') in notes}
        for key,note in notes.items():rows.setdefault(key,dict(article_key=key,title=note.get('title',key),doi=note.get('doi','')))
        dialog.articles=list(rows.values())
    else:
        dialog.evidence_notes.update(notes);dialog.articles=list({r['article_key']:r for r in record['inputs'].get('articles',[])+dialog.articles}.values())
    dialog.update_selection();return True

class KnowledgeDialog(ScientificQuestionsDialog):
    def __init__(self,manager,parent=None):
        super().__init__(manager,parent);self.setWindowTitle('AI模型训练 · 领域知识库');self.followup.hide();self.pdf_button.hide();self.run_button.setText('读取新文献并更新领域知识库');self.topic.setPlaceholderText('输入领域知识库名称或研究范围。支持按不同模型建立多个库；再次进入可添加新文章。')
        self.info.setText('此功能建立可复用的文献证据记忆，不训练或微调云端模型权重。可增量补充文献；读取内容将发送至所选AI服务。')
    def run(self):
        try:
            engine=self.engine();articles=list(self.articles);evidence=dict(self.evidence_notes);topic=self.topic.toPlainText().strip()
            if not articles or not topic:raise ValueError('请命名知识库并选择收藏文章。')
            if self.pinned_model and self.pinned_model!=(engine.provider,engine.model):
                if QMessageBox.question(self,'模型已更换','是否将已有证据笔记交给当前模型继续整理？')!=QMessageBox.StandardButton.Yes:return
            self.generated_inputs=dict(topic=topic,articles=articles)
            def work():
                for i,article in enumerate(articles,1):
                    if article['article_key'] in evidence:continue
                    self.progress_callback(f'阅读领域文献 {i}/{len(articles)} · '+article['title'])
                    summary=engine.summarize(self.manager.store.database,article,self.progress_callback,self.manager.article_reader.get,include_diagram=False)
                    evidence[article['article_key']]=dict(title=article['title'],doi=article.get('doi',''),coverage=summary['coverage'],text=summary['text'])
                return engine.result('领域知识库已更新。',topic=topic,evidence_notes=evidence,read_count=len(evidence))
            self.start(work)
        except ValueError as error:QMessageBox.information(self,'无法更新',str(error))
    def display(self,result,saved=False):
        self.result=result;self.dirty=not saved;self.reasoning_note.setText(__import__('reasoning').result_label(result));self.evidence_notes=result.get('evidence_notes') or {};self.pinned_model=(result.get('provider'),result.get('model'));self.update_selection();self.tabs.setCurrentIndex(1)
        self.research_view.setHtml('<h1>领域证据记忆</h1><p>模型：'+escape(str(result.get('model','')))+' · 已读 '+str(len(self.evidence_notes))+' 篇</p>'+''.join('<p><b>'+escape(v['title'])+'</b><br/>'+escape(v['coverage'])+'</p>' for v in self.evidence_notes.values()))
    def save_result(self):
        self.saved_history_id=self.manager.repository.save_history('knowledge',self.topic.toPlainText(),dict(topic=self.topic.toPlainText(),articles=self.articles),self.result,self.saved_history_id)
        self.manager.changed.emit()

class KnowledgeHub(QDialog):
    def __init__(self,manager,parent=None):
        super().__init__(parent);self.manager=manager;self.setWindowTitle('AI模型训练 · 多模型领域知识库');self.resize(1050,700);layout=QVBoxLayout(self);layout.addWidget(QLabel('保存多个领域知识库，点击卡片继续补充文献。不是模型权重训练。'))
        button=QPushButton('新建知识库');button.clicked.connect(self.create);layout.addWidget(button,0,Qt.AlignmentFlag.AlignLeft)
        from ai_workflow_ui import HistoryPane
        self.history_pane=HistoryPane(manager,'knowledge',self.open_record,self);self.board=self.history_pane.board;layout.addWidget(self.history_pane,1)
    def reload(self):
        self.history_pane.reload()
    def create(self):show_window(KnowledgeDialog(self.manager,self))
    def open_record(self,record):
        dialog=KnowledgeDialog(self.manager,self.parentWidget() or self);dialog.restore_record(record);show_window(dialog)

class ReviewerDialog(ScientificQuestionsDialog):
    def __init__(self,manager,parent=None):
        super().__init__(manager,parent);self.setWindowTitle('AI reviewer · Evidence-based peer review');self.knowledge=knowledge_combo(self);self.files=[];self.source_files=[];self.pdf_button.show();self.pdf_button.setText('导出 Peer Review PDF');self.pdf_button.setEnabled(False);button=QPushButton('添加待审稿件（PDF / DOCX / 图片）');button.clicked.connect(self.choose);self.body_layout.insertWidget(3,button,0,Qt.AlignmentFlag.AlignLeft);self.run_button.setText('Read manuscript and review');self.topic.setPlaceholderText('输入研究领域及稿件范围：完整工作或部分结果。PDF会逐页进行视觉读取，按模型规则计费；请使用支持图片的模型。');self.followup.setPlaceholderText('补充审稿要求，或继续讨论上次审稿意见。')
        from manuscript_drop import ManuscriptDropList
        self.manuscript_drop=ManuscriptDropList(self);self.body_layout.insertWidget(4,self.manuscript_drop)
        clear_files=QPushButton('清空待审文件');clear_files.setProperty('secondary',True);clear_files.clicked.connect(self.clear_manuscripts)
        self.body_layout.insertWidget(5,clear_files,0,Qt.AlignmentFlag.AlignLeft)
        # Keep file/library selection on the input tab so the result has the
        # full reading area rather than sharing it with a permanent drop box.
        input_layout=self.tabs.widget(0).layout()
        for index,widget in enumerate((self.knowledge.knowledge_caption,self.knowledge,button,self.manuscript_drop,clear_files)):
            self.body_layout.removeWidget(widget)
            input_layout.insertWidget(index,widget,0,Qt.AlignmentFlag.AlignLeft if isinstance(widget,QPushButton) else Qt.AlignmentFlag(0))
        self.tabs.setTabText(1,'Peer Review')
        self.source_links=QLabel();self.source_links.setWordWrap(True);self.source_links.setOpenExternalLinks(False)
        self.source_links.setStyleSheet('color:#6D7C83;font-size:9pt;padding:6px;');self.source_links.linkActivated.connect(self.open_manuscript_folder)
        self.tabs.widget(1).layout().insertWidget(1,self.source_links)
        self.info.setText('模拟 Molecular Cell 特邀审稿人，生成英文评审，仅含 Summary and significance assessment、Major comments、Minor comments。不是期刊正式评审。历史仅保留评审结果与稿件名称／路径，不复制稿件或保存其读取内容；续审时重新读取源文件。')
    def choose(self):
        from path_dialog import QFileDialog
        from ai_finder import FILE_FILTER
        if self.task:return
        paths,_=QFileDialog.getOpenFileNames(self,'选择自己的研究稿件','',FILE_FILTER)
        self.manuscript_drop.add_paths(paths)
    def clear_manuscripts(self):
        if self.task:return
        self.files=[];self.manuscript_drop.refresh();self.status.setText('已清空待审文件选择，源文件与已保存的评审结果保持保留。')
    def run(self):
        try:
            engine=self.engine()
            if not apply_knowledge(self,engine):return
            topic=self.topic.toPlainText().strip();files=list(self.files);notes=dict(self.evidence_notes);articles=list(self.articles);previous=(self.result or {}).get('text','');followup=self.followup.toPlainText()
            if not topic or not files:raise ValueError('请输入研究领域，并添加可读取的研究稿件。历史仅保存路径；源文件移动或删除后需重新选择。')
            if any(not Path(file).is_file() for file in files):raise ValueError('部分稿件文件已移动或删除，请重新选择可读取的稿件。')
            from peer_review import manuscript_refs
            source_files=manuscript_refs(files)
            self.generated_inputs=dict(topic=topic,articles=articles,knowledge_id=self.knowledge.currentData(),source_files=source_files)
            def work():
                for article in articles:
                    if article['article_key'] not in notes:
                        summary=engine.summarize(self.manager.store.database,article,self.progress_callback,self.manager.article_reader.get,include_diagram=False);notes[article['article_key']]=dict(title=article['title'],doi=article.get('doi',''),coverage=summary['coverage'],text=summary['text'])
                material=[]
                for file in files:
                    row=read_attachment(file,engine,self.progress_callback);material.append(row['name']+'\n'+row['text'])
                    if Path(file).suffix.lower()=='.pdf' and '扫描PDF' not in row.get('coverage',''):
                        import pypdfium2,io
                        doc=pypdfium2.PdfDocument(file)
                        try:
                            for i in range(len(doc)):
                                self.progress_callback(f'视觉检查稿件第{i+1}/{len(doc)}页');page=doc[i];bitmap=page.render(scale=1.5);buffer=io.BytesIO();bitmap.to_pil().save(buffer,format='PNG');bitmap.close();page.close();buffer.seek(0);material.append(f'Page {i+1} visual evidence:\n'+read_image(engine,buffer))
                        finally:doc.close()
                catalogue=[dict(article_id=i,title=article.get('title',''),article_key=article['article_key'],evidence=notes.get(article['article_key'],{})) for i,article in enumerate(articles,1)]
                context='Field: '+topic+'\nReference evidence (numbered library materials):\n'+json.dumps(catalogue,ensure_ascii=False)+'\nManuscript:\n'+'\n'.join(material)+'\nPrevious final review:\n'+previous+'\nFollowup:\n'+followup
                from peer_review import INSTRUCTIONS,parse_review,review_text
                raw,_=engine.reduce(context,self.progress_callback,INSTRUCTIONS)
                review=parse_review(raw)
                return engine.result(review_text(review),topic=topic,peer_review=review)
            self.start(work)
        except ValueError as error:QMessageBox.information(self,'无法审稿',str(error))
    def display(self,result,saved=False):
        from peer_review import review_html
        self.result=result;self.dirty=not saved;self.reasoning_note.setText(__import__('reasoning').result_label(result))
        self.pinned_model=(result.get('provider'),result.get('model'))
        if not saved:
            self.source_files=list(getattr(self,'generated_inputs',{}).get('source_files',[]));self.followup.clear()
        self.refresh_source_links();self.manuscript_drop.refresh()
        self.research_view.setStyleSheet('QTextBrowser {font-family:"Times New Roman";font-size:12pt;background:white;border:1px solid #DCE4E8;border-radius:12px;padding:22px;}')
        self.research_view.setHtml(review_html(result))
        from PySide6.QtGui import QTextCursor,QTextBlockFormat,QTextCharFormat,QTextFormat
        cursor=QTextCursor(self.research_view.document());cursor.select(QTextCursor.SelectionType.Document)
        char=QTextCharFormat();char.setFontFamilies(['Times New Roman']);char.setFontPointSize(12);char.setProperty(QTextFormat.Property.FontSizeAdjustment,0);cursor.mergeCharFormat(char)
        block=QTextBlockFormat();block.setLineHeight(150,QTextBlockFormat.LineHeightTypes.ProportionalHeight.value);cursor.mergeBlockFormat(block)
        self.research_view.moveCursor(QTextCursor.MoveOperation.Start);self.research_view.verticalScrollBar().setValue(0)
        self.tabs.setCurrentIndex(1);self.pdf_button.setEnabled(bool(result.get('text')) and not self.task)
        self.result_model.setText('分析模型：'+str(result.get('provider',''))+' / '+str(result.get('model','')))
        self.status.setText('已加载保存的评审。稿件内容未缓存；继续审阅时重新读取源文件。' if saved else '英文评审完成，可保存结果或导出带水印的 PDF。请人工核查评审意见。')

    def refresh_source_links(self):
        self.source_links.setText('稿件（点击名称打开所在文件夹）： '+ '　'.join('<a href="biomed-manuscript:'+str(i)+'">'+escape(item['name'])+'</a>' for i,item in enumerate(self.source_files)) if self.source_files else '此历史未记录源稿件路径，可重新添加稿件。')

    def open_manuscript_folder(self,link):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        url=QUrl(link)
        if url.scheme()!='biomed-manuscript':return
        try:
            index=int(url.path())
            if index<0:return
            item=self.source_files[index]
        except (ValueError,IndexError):return
        folder=Path(item['path']).parent
        if not folder.is_dir():QMessageBox.information(self,'文件夹不可用','原文件夹已移动或删除，请重新添加稿件。');return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):QMessageBox.warning(self,'无法打开文件夹','请检查系统默认文件管理器设置。')

    def restore_record(self,record):
        self.source_files=[dict(name=Path(item['path']).name,path=item['path']) for item in record['inputs'].get('source_files',[]) if isinstance(item,dict) and isinstance(item.get('path'),str)]
        self.files=[item['path'] for item in self.source_files]
        from peer_review import history_result
        safe=dict(record,result=history_result(record['result']))
        super().restore_record(safe)
        self.refresh_source_links();self.manuscript_drop.refresh()

    def save_result(self):
        from peer_review import history_result
        inputs=dict(topic=self.topic.toPlainText(),articles=self.articles,knowledge_id=self.knowledge.currentData(),source_files=self.source_files)
        self.saved_history_id=self.manager.repository.save_history('reviewer',self.topic.toPlainText() or 'Peer review',inputs,history_result(self.result),self.saved_history_id)
        self.manager.changed.emit()

    def export_pdf(self):
        if self.task or self.export_task or not self.result or not self.result.get('text'):return
        from path_dialog import QFileDialog
        from peer_review import export_review_pdf
        path,_=QFileDialog.getSaveFileName(self,'导出 Peer Review PDF','BiomedReader_Peer_Review.pdf','PDF (*.pdf)')
        if not path:return
        if not path.lower().endswith('.pdf'):path+='.pdf'
        result=dict(self.result);self.pdf_button.setEnabled(False)
        self.export_task=self.manager.submit(self,lambda:dict(path=export_review_pdf(path,result)),'export_done','export_failed')

class ExpertBoard(CardBoard):
    def paintEvent(self,event):
        super().paintEvent(event)
        if len(self.cards)<3:return
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing);painter.setPen(QPen(QColor('#D9A26D'),2))
        for card in self.cards[1:3]:
            a=self.cards[0].geometry();b=card.geometry();path=QPainterPath();path.moveTo(a.center().x(),a.bottom());path.cubicTo(a.center().x(),a.bottom()+38,b.center().x(),b.bottom()+38,b.center().x(),b.bottom());painter.drawPath(path)
        painter.end()
