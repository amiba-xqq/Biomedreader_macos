"""Editorial UI and PDF share one escaped, offline document representation."""
import base64,math,os,tempfile,weakref
import shiboken6
from html import escape
from pathlib import Path
from PySide6.QtCore import Qt,QByteArray,QUrl,QSizeF,QRectF,QMarginsF,QTimer
from PySide6.QtGui import QImage,QPainter,QPicture,QTextDocument,QTextCursor,QTextFormat,QFont,QColor,QPdfWriter,QPageSize,QPageLayout
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QFrame,QScrollArea,QGridLayout,QTextBrowser,QStackedWidget
from ai_editorial import diagram_svg,legacy_brief

def report_image(summary):
    figure=summary.get('figure') or {}
    if figure.get('kind')=='paper' and figure.get('image_base64'):
        image=QImage.fromData(QByteArray.fromBase64(figure['image_base64'].encode('ascii')))
        if not image.isNull():return image,figure.get('caption') or '论文模型图候选（请核对）'
    generation=summary.get('diagram_generation')
    if generation and generation.get('format') in ('pdf','png','jpg','tif','bmp','webp'):
        if generation.get('status')!='completed':return QImage(),'本次模型图未生成：'+str(generation.get('error') or '模型未附上可用的图像文件。')
        try:
            from model_artifact import figure_image
            artifact=generation['artifact'];image=figure_image(artifact);return image,'AI直接生成的'+artifact['format'].upper()+'模型图 · 至少300 dpi（非论文原图，请核对原文）。'
        except (ValueError,KeyError,OSError):return QImage(),'缓存模型图文件无法读取，请重新生成。'
    if generation and generation.get('status')!='completed':return QImage(),'本次机制图未生成：'+str(generation.get('error') or '所选模型未返回支持参考附件的图式。')
    presentation=summary.get('presentation') or {}
    if not presentation:return QImage(),'旧缓存尚无机制图，请生成新版图文报告。'
    svg=diagram_svg(presentation.get('diagram') or {})
    renderer=QSvgRenderer(QByteArray(svg.encode('utf8')));size=renderer.defaultSize();image=QImage(size,QImage.Format.Format_ARGB32);image.fill(Qt.GlobalColor.white)
    painter=QPainter(image);renderer.render(painter);painter.end()
    return image,'AI生成的机制示意图（非论文原图，需核对原文）。'+(presentation.get('diagram') or {}).get('caption','')

def news_html(summary,article,image_width=660,pdf=False):
    data=summary.get('presentation') or {};news=data.get('news') or {}
    if not data:return '<h2>此缓存尚无新闻稿</h2><p>请点击“生成新版图文报告”，基于原文重新生成。不会把旧总结自动编造为新闻稿。</p>'
    e=lambda value:escape(str(value or '')).replace('\n','<br/>')
    provenance='<p style="font-family:Arial,Microsoft YaHei;font-size:8.5pt;color:#68777E;">AI解读 / '+e(summary.get('provider'))+' · '+e(summary.get('model'))+'<br/>'+e(__import__('reasoning').result_label(summary))+'<br/>读取范围：'+e(summary.get('coverage'))+'<br/>证据边界：'+e(data.get('evidence_limits'))+'<br/>本解读需人工核对原文；AI示意图不代表原始实验数据。</p>'
    image,caption=report_image(summary)
    if pdf and (summary.get('diagram_generation') or {}).get('artifact') and not image.isNull():
        dpi=max(300,image.dotsPerMeterX()*.0254);image_width=min(image_width,max(1,int(image.width()*144/dpi)))
    height=int(image_width*image.height()/max(1,image.width()))
    parts=['<html><body style="font-family:Arial,Microsoft YaHei;font-size:11.5pt;color:#202B31;background:white;">',
        '<p style="font-family:Arial;font-size:9pt;color:#006D89;letter-spacing:1px;">BIOMED READER &nbsp; / &nbsp; RESEARCH NEWS</p>',
        '<h1 style="font-family:Arial,Microsoft YaHei;font-size:19pt;color:#132A36;">'+e(article.get('title') or summary.get('title') or '研究解读')+'</h1>',
        '<p style="font-family:Arial;font-size:9pt;color:#64747C;">'+e(article.get('journal_title'))+' &nbsp; '+e(article.get('publication_date'))+'<br/>DOI: '+e(article.get('doi') or summary.get('doi'))+'</p>'+(provenance if pdf else ''),
        '<hr/><h2 style="color:#006D89;font-size:13pt;">研究背景</h2><p style="line-height:150%;">'+e(news.get('background') or '材料未提供足够背景。')+'</p>',
        '<h2 style="color:#A72B33;font-size:13pt;">主要发现</h2><p style="color:#B2222D;line-height:150%;font-weight:600;">'+e(news.get('main_discovery') or '材料未提供足够主要发现。')+'</p>']
    if not image.isNull():parts.append(f'<p align="center"><img src="report-model" width="{image_width}" height="{height}"/></p><p style="font-size:9pt;color:#62727A;">'+e(caption)+'</p>')
    elif summary.get('diagram_generation'):parts.append('<p style="color:#8A5C42;">'+e(caption)+'</p>')
    if summary.get('news_warning'):parts.append('<p style="color:#8A5C42;">'+e(summary['news_warning'])+'</p>')
    for index,paragraph in enumerate(news.get('research') or [],1):
        parts.append('<h2 style="color:#006D89;font-size:13pt;">'+e(paragraph.get('heading') or '研究内容 '+str(index))+'</h2><p style="line-height:150%;">'+e(paragraph.get('body'))+'</p>')
    parts.extend(['<h2 style="color:#006D89;font-size:13pt;">领域贡献</h2><p style="line-height:150%;">'+e(news.get('contribution') or '材料未提供。')+'</p>',
        ('<hr/>'+provenance if not pdf else '')+'</body></html>'])
    return ''.join(parts)

def fill_document(document,summary,article,width,pdf=False):
    image,_=report_image(summary)
    document.addResource(QTextDocument.ResourceType.ImageResource,QUrl('report-model'),image)
    html=news_html(summary,article,max(240,min(660 if pdf else 960,int(width)-42)),pdf=pdf)
    if pdf:html=html.replace('font-size:11.5pt','font-size:10.5pt').replace('line-height:150%','line-height:140%')
    document.setHtml(html)

def keep_headings_with_text(document,page_height):
    block=document.begin()
    while block.isValid():
        following=block.next()
        if block.blockFormat().headingLevel() and following.isValid():
            layout=document.documentLayout();heading=layout.blockBoundingRect(block);paragraph=layout.blockBoundingRect(following)
            lines=following.layout();line_height=lines.lineAt(0).height() if lines.lineCount() else 24
            page=int((heading.top()+1)//page_height);needed=paragraph.top()+min(paragraph.height(),line_height*2)
            if int((needed-1)//page_height)>page and heading.top()%page_height>30:
                cursor=QTextCursor(block);format=block.blockFormat();format.setPageBreakPolicy(QTextFormat.PageBreakFlag.PageBreak_AlwaysBefore);cursor.setBlockFormat(format);layout.documentSize()
        elif block.text().startswith(('AI生成的机制示意图','论文模型图','论文Graphical Abstract')):
            layout=document.documentLayout();bounds=layout.blockBoundingRect(block)
            if int((bounds.bottom()-1)//page_height)>int((bounds.top()+1)//page_height):
                cursor=QTextCursor(block);format=block.blockFormat();format.setPageBreakPolicy(QTextFormat.PageBreakFlag.PageBreak_AlwaysBefore);cursor.setBlockFormat(format);layout.documentSize()
        block=following

class ReportPresentation(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent);layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);self.stack=QStackedWidget();layout.addWidget(self.stack)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setStyleSheet('QScrollArea {border:none;background:#F5F7F8;}');content=QWidget();self.panels=QGridLayout(content);self.panels.setContentsMargins(16,16,16,16);self.panels.setSpacing(16);self.brief_labels=[]
        self.brief_headings=[];self.language='zh'
        for col,(title,accent) in enumerate([('科学问题','#17627A'),('Highlights','#AC5320'),('创新点','#65609C')]):
            card=QFrame();card.setObjectName('EditorialPanel');card.setStyleSheet('QFrame#EditorialPanel {background:white;border:1px solid #DCE4E8;border-radius:14px;}');box=QVBoxLayout(card);box.setContentsMargins(22,22,22,24)
            heading=QLabel(title);heading.setStyleSheet('font-family:Arial,Microsoft YaHei;font-size:15pt;font-weight:700;color:'+accent+';');box.addWidget(heading);self.brief_headings.append(heading)
            rule=QFrame();rule.setFixedHeight(3);rule.setStyleSheet('background:'+accent+';border:none;');box.addWidget(rule)
            text=QLabel();text.setTextFormat(Qt.TextFormat.PlainText);text.setWordWrap(True);text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);text.setAlignment(Qt.AlignmentFlag.AlignTop);text.setStyleSheet('font-family:Arial,Microsoft YaHei;font-size:11.5pt;color:#273942;line-height:1.6;');box.addWidget(text,1);self.brief_labels.append(text);self.panels.addWidget(card,0,col);self.panels.setColumnStretch(col,1)
        self.panels.setRowStretch(0,1);scroll.setWidget(content);self.stack.addWidget(scroll)
        self.news=QTextBrowser();self.news.setOpenExternalLinks(False);self.news.setStyleSheet('QTextBrowser {background:white;border:1px solid #DCE4E8;border-radius:12px;padding:24px;}');self.stack.addWidget(self.news)
        self.summary={};self.article={};self.mode='brief'
    def set_result(self,summary,article,mode):
        same=(self.article.get('article_key')==(article or {}).get('article_key') and self.summary==(summary or {}) and self.mode==mode)
        self.summary=summary or {};self.article=article or {};self.mode=mode
        data=self.summary.get('presentation') or legacy_brief(self.summary)
        if self.language=='en':data=data.get('english_brief') or self.summary.get('english_brief') or data
        for heading,title in zip(self.brief_headings,('Scientific question','Highlights','Innovations') if self.language=='en' else ('科学问题','Highlights','创新点')):heading.setText(title)
        self.brief_labels[0].setText(data['scientific_question'])
        for label,key in zip(self.brief_labels[1:],('highlights','innovations')):label.setText('\n\n'.join('• '+item for item in data[key]) or '材料未提供。')
        if not same:
            fill_document(self.news.document(),self.summary,self.article,max(300,self.news.viewport().width()-50));self.news.moveCursor(QTextCursor.MoveOperation.Start);self.news.verticalScrollBar().setValue(0)
        self.stack.setCurrentIndex(1 if mode=='news' else 0)
        if not same:
            target=weakref.ref(self.news)
            def reset_scroll():
                browser=target()
                if browser is not None and shiboken6.isValid(browser):browser.verticalScrollBar().setValue(0)
            QTimer.singleShot(0,reset_scroll)
    def set_language(self,language):
        self.language=language;self.set_result(self.summary,self.article,self.mode)

def export_news_pdf(path,items,research=False):
    """A4 editorial pages, embedded system fonts, repeated model watermarks."""
    if not items:raise ValueError('没有可以导出的文章。')
    missing=[article.get('title') or (summary or {}).get('title') or '无标题' for article,summary in items if not summary or not summary.get('research' if research else 'presentation')]
    if missing:raise ValueError(f'{len(missing)} 篇尚无新版图文报告，请先生成后导出。\n'+ '\n'.join(missing[:5]))
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);fd,temporary=tempfile.mkstemp(prefix='.biomed-report-',suffix='.pdf',dir=path.parent);os.close(fd)
    try:
        writer=QPdfWriter(temporary);writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4));writer.setPageMargins(QMarginsF(18,20,18,18),QPageLayout.Unit.Millimeter);writer.setResolution(144);writer.setTitle('Biomed Reader · '+('AI科学问题报告' if research else 'AI研究新闻稿'));writer.setCreator('Biomed Reader 2.2')
        width=writer.width();height=writer.height();header=58;footer=50;body_height=height-header-footer;documents=[]
        for article,summary in items:
            document=QTextDocument();document.documentLayout().setPaintDevice(writer);document.setDefaultFont(QFont('Microsoft YaHei',11));document.setDocumentMargin(0);document.setPageSize(QSizeF(width,body_height))
            if research:
                from research_report import fill_research_document
                fill_research_document(document,summary,pdf=True)
            else:fill_document(document,summary,article,width,pdf=True)
            # Resolve lazy font/image and paged-table layout before reading the
            # final page count. QPicture is a small vector recording, not a
            # potentially enormous raster of an entire long article.
            warm=QPicture();warm_painter=QPainter(warm);document.drawContents(warm_painter);warm_painter.end()
            keep_headings_with_text(document,body_height)
            warm=QPicture();warm_painter=QPainter(warm);document.drawContents(warm_painter);warm_painter.end()
            # QTextDocument can lazily retain an earlier page count after the
            # heading rules insert page breaks, particularly in worker threads.
            # Force the final block to lay out and include its actual extent.
            last=document.documentLayout().blockBoundingRect(document.lastBlock())
            pages=max(1,document.pageCount(),math.ceil(last.bottom()/body_height))
            documents.append((article,summary,document,pages))
        total=sum(item[3] for item in documents);page_number=0;painter=QPainter(writer)
        if not painter.isActive():raise ValueError('无法写入 PDF，请检查保存路径是否可写。')
        try:
            for article,summary,document,pages in documents:
                model=summary.get('model') or '未记录模型'
                for page in range(pages):
                    if page_number:writer.newPage()
                    page_number+=1;painter.save();painter.setPen(QColor('#006D89'));painter.setFont(QFont('Arial',9,QFont.Weight.Bold));painter.drawText(QRectF(0,0,width,28),Qt.AlignmentFlag.AlignLeft,'BIOMED READER  /  '+('RESEARCH QUESTIONS' if research else 'RESEARCH NEWS'));painter.drawLine(0,37,width,37);painter.restore()
                    painter.save();painter.setClipRect(QRectF(0,header,width,body_height));painter.translate(0,header-page*body_height)
                    # Bound writes to this page as well as clipping the canvas;
                    # otherwise invisible off-page text confuses PDF copying.
                    document.drawContents(painter,QRectF(0,page*body_height,width,body_height))
                    painter.restore()
                    painter.save();painter.translate(width/2,height/2);painter.rotate(-32);painter.setOpacity(.075);painter.setPen(QColor('#426571'));font=QFont('Arial',29);painter.setFont(font)
                    text='Biomed Reader + '+model
                    if painter.fontMetrics().horizontalAdvance(text)>width*1.1:font.setPointSizeF(max(10,29*width*1.1/painter.fontMetrics().horizontalAdvance(text)));painter.setFont(font)
                    painter.drawText(QRectF(-width*.7,-70,width*1.4,140),Qt.AlignmentFlag.AlignCenter,text);painter.restore()
                    painter.save();painter.setPen(QColor('#66767F'));painter.setFont(QFont('Arial',8));painter.drawText(QRectF(0,height-footer+16,width-90,30),Qt.AlignmentFlag.AlignLeft,'Biomed Reader + '+model);painter.drawText(QRectF(width-85,height-footer+16,85,30),Qt.AlignmentFlag.AlignRight,f'{page_number} / {total}');painter.restore()
        finally:painter.end()
        del writer
        if Path(temporary).stat().st_size<1000:raise ValueError('PDF 未完整生成。')
        os.replace(temporary,path)
        return page_number
    finally:
        if Path(temporary).exists():Path(temporary).unlink()
