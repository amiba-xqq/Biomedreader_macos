"""Protocol exports and portable, data-only sharing."""
import io
import json
import os
import re
import sys
import tempfile
import threading
from contextlib import closing
from html import escape
from pathlib import Path
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from ai_repository import now

MAX_BUNDLE = 96*1024*1024
FORMAT = 'BiomedReader.Protocols/1'
FONT_LOCK = threading.Lock()
FLOW_LOCK = threading.Lock()
FONT_DIR = Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'
ASCII_RUNS = re.compile(r'([\x20-\x7e\u0370-\u03ffµ]+)')
ACCENT = '#006D89'
TITLE_INK = '#132A36'
BODY_INK = '#202B31'
SECTION_NAMES = {
    '目的','适用范围','目的与适用范围','实验目的','试剂','试剂与耗材','试剂与材料','材料与试剂',
    '仪器','仪器与准备','仪器与设备','实验准备','实验流程','实验步骤','操作步骤','质量控制',
    '注意事项','质量控制与注意事项','待确认条件','资料来源','参考文献',
}


def normalize_author(value):
    if not isinstance(value,str):
        raise ValueError('请输入有效的用户 ID 或名称。')
    name = value.strip()
    if not 1<=len(name)<=80 or any(ord(character)<32 for character in name):
        raise ValueError('用户 ID 或名称须为 1–80 个字符。')
    return name


def display_title(result):
    title = result.get('title','')
    author = result.get('author')
    return f'{title} · {author}' if author else title


def flow_image(result):
    diagram = result.get('diagram')
    if diagram:
        try:
            return rendered_flow(diagram)
        except (ValueError,OSError,RuntimeError,TypeError,KeyError):
            # Retain an editable protocol and its stage outline if an older
            # cache contains an unreadable figure.
            pass
    stages = result.get('stages',[])
    if not stages:
        return None
    from PIL import Image,ImageDraw,ImageFont
    height = 36+len(stages)*120
    image = Image.new('RGB',(1200,height),'#FFFFFF')
    draw = ImageDraw.Draw(image)
    font = None if sys.platform=='darwin' else ImageFont.truetype(str(FONT_DIR/'msyh.ttc'),39)
    for index,stage in enumerate(stages):
        top = 20+index*120
        draw.rounded_rectangle((35,top,1165,top+83),radius=16,fill='#FFF5E8',outline='#CE8743',width=3)
        if font:
            draw.text((600,top+41),f'{index+1}. {stage}',font=font,anchor='mm',fill='#292321')
        if index<len(stages)-1:
            draw.line((600,top+87,600,top+108),fill='#9A5D28',width=4)
            draw.polygon([(591,top+105),(609,top+105),(600,top+116)],fill='#9A5D28')
    data = io.BytesIO()
    if sys.platform=='darwin':
        from PySide6.QtCore import QBuffer,QIODevice,QRect,Qt
        from PySide6.QtGui import QColor,QFont,QImage,QPainter
        image.save(data,'PNG')
        canvas = QImage.fromData(data.getvalue())
        painter = QPainter(canvas)
        try:
            caption = QFont()
            caption.setPixelSize(39)
            painter.setFont(caption)
            painter.setPen(QColor('#292321'))
            for index,stage in enumerate(stages):
                painter.drawText(QRect(35,20+index*120,1130,83),Qt.AlignmentFlag.AlignCenter,f'{index+1}. {stage}')
        finally:
            painter.end()
        canvas.setDotsPerMeterX(11811)
        canvas.setDotsPerMeterY(11811)
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not canvas.save(buffer,'PNG'):
            raise ValueError('无法导出实验流程图。')
        return bytes(buffer.data())
    image.save(data,'PNG',dpi=(300,300))
    return data.getvalue()


def rendered_flow(diagram):
    from model_artifact import figure_bytes
    from PIL import Image
    raw = figure_bytes(diagram)
    if raw.startswith(b'%PDF-'):
        import pypdfium2
        with FLOW_LOCK,pypdfium2.PdfDocument(raw) as pdf,closing(pdf[0]) as page:
            w,h = page.get_size()
            if not 0<min(w,h)<=max(w,h)<=20000:
                raise ValueError('流程图 PDF 页面尺寸无效。')
            with closing(page.render(scale=min(3,1600/max(w,h)))) as bitmap:
                image = bitmap.to_pil().copy()
    else:
        with Image.open(io.BytesIO(raw)) as original:
            image = original.convert('RGB')
            image.thumbnail((1700,1700))
    data = io.BytesIO()
    image.save(data,'PNG')
    return data.getvalue()


def without_local_references(result):
    sources = result.get('sources',[])
    local = [row for row in sources if row.get('origin')=='local' or row.get('coverage')=='用户提供的文件']
    if not local:
        return result
    identifiers = {str(row.get('id','')) for row in local}
    lines = []
    for line in result.get('text','').splitlines():
        stripped = re.sub(r'\[([^\[\]]+)\]',lambda m:'' if m[1] in identifiers else m[0],line)
        lines.append(stripped.rstrip())
    return {**result,'text':'\n'.join(lines),'sources':[row for row in sources if row not in local]}


def document_sections(result):
    result = without_local_references(result)
    for line in result['text'].splitlines():
        line = line.strip()
        if line:
            explicit_heading = bool(re.match(r'^#{1,6}\s+',line))
            name = re.sub(r'^#{1,6}\s+','',line).strip('* ：:')
            name = re.sub(r'^[一二三四五六七八九十]+[、.．]\s*','',name)
            if explicit_heading or name in SECTION_NAMES:
                yield 'heading',name
            else:
                # Older drafts sometimes separate named recipes with a semicolon.
                recipe = r'(?i)[；;]\s*(?=(?:[-*]\s+|\d+[.、)]\s*)?[^；;。:：,，]{0,40}(?:缓冲液|缓冲溶液|buffer)(?:\s*[（(][^）)]{1,20}[）)])?\s*[:：])'
                if re.search(r'(?i)(?:缓冲液|缓冲溶液|buffer)(?:\s*[（(][^）)]{1,20}[）)])?\s*[:：]',line):
                    for part in re.split(recipe,line):
                        if part.strip():
                            yield 'body',part.strip()
                else:
                    yield 'body',line
    if result.get('sources'):
        yield 'heading','资料来源'
        for source in result['sources']:
            yield 'source',f"[{source['id']}] {source['title']}（{source['coverage']}） {source.get('url','')}".strip()


def preview_html(result,diagram_size=None):
    safe = lambda text:escape(str(text or ''))
    html = [
        f'<html><body style="background:white;color:{BODY_INK};font-family:Arial,Microsoft YaHei;font-size:12pt;">',
        f'<h1 style="color:{TITLE_INK};font-size:19pt;margin-bottom:8px;">{safe(display_title(result))}</h1>',
    ]
    if result.get('model'):
        html.append(f'<p style="color:#64747C;font-size:9pt;">AI Protocol · {safe(result["model"])}</p>')
    html.append('<hr/>')
    if diagram_size:
        width,height = diagram_size
        html.append(f'<h2 style="color:{ACCENT};font-size:13pt;">实验流程</h2>')
        html.append(f'<p align="center"><img src="protocol-flow" width="{width}" height="{height}"/></p>')
    for role,line in document_sections(result):
        if role=='heading':
            html.append(f'<h2 style="color:{ACCENT};font-size:13pt;margin-top:16px;">{safe(line)}</h2>')
        elif role=='source':
            html.append(f'<p style="color:#52666F;font-size:10pt;line-height:150%;">{safe(line)}</p>')
        else:
            html.append(f'<p style="line-height:150%;text-indent:32px;margin-top:5px;margin-bottom:6px;">{safe(line)}</p>')
    html.append('</body></html>')
    return ''.join(html)


def export_docx(path,result):
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches,Pt,RGBColor
    document = Document()
    normal = document.styles['Normal']
    normal.font.name = 'Arial'
    normal.font.size = Pt(12)
    normal.element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),'Microsoft YaHei')
    normal.paragraph_format.line_spacing = 1.5
    normal.paragraph_format.space_after = Pt(0)
    section = document.sections[0]
    section.header_distance = Inches(.42)
    section.footer_distance = Inches(.42)
    header = section.header.paragraphs[0]
    header_run = header.add_run('AI Protocol  |  '+display_title(result)[:68])
    header_run.font.size = Pt(9)
    header_run.font.color.rgb = RGBColor.from_string('52666F')
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    footer.add_run('第 ').font.size = Pt(9)
    field = OxmlElement('w:fldSimple')
    field.set(qn('w:instr'),' PAGE ')
    footer._p.append(field)
    footer.add_run(' 页').font.size = Pt(9)

    def add(line,role='body'):
        paragraph = document.add_paragraph()
        if role=='body':
            props = paragraph._p.get_or_add_pPr()
            ind = OxmlElement('w:ind')
            ind.set(qn('w:firstLineChars'),'200')
            ind.set(qn('w:firstLine'),'480')
            props.append(ind)
        run = paragraph.add_run(line)
        if role in ('heading','title'):
            run.bold = True
            run.font.color.rgb = RGBColor.from_string((TITLE_INK if role=='title' else ACCENT)[1:])
            paragraph.paragraph_format.keep_with_next = True
            if role=='title':
                run.font.size = Pt(19)
                paragraph.paragraph_format.space_after = Pt(10)
            else:
                paragraph.paragraph_format.space_before = Pt(10)
                paragraph.paragraph_format.space_after = Pt(3)
        elif role=='source':
            run.font.color.rgb = RGBColor.from_string('52666F')
        return paragraph

    add(display_title(result),'title')
    document.core_properties.title = display_title(result)
    document.core_properties.author = result.get('author','')
    image = flow_image(result)
    if image:
        from PIL import Image
        with Image.open(io.BytesIO(image)) as picture:
            display_width = min(5.8,3.2*picture.width/picture.height)
        add('实验流程','heading')
        paragraph = document.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.add_run().add_picture(io.BytesIO(image),width=Inches(display_width))
    for role,line in document_sections(result):
        add(line,role)
    document.save(str(path))


def export_pdf(path,result):
    from PIL import Image as PILImage
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer

    if sys.platform=='darwin':
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from figure_render import arial_paths
        regular,bold = arial_paths()
        with FONT_LOCK:
            if 'BRProtocolArial' not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont('BRProtocolArial',str(regular)))
                pdfmetrics.registerFont(TTFont('BRProtocolArialBold',str(bold)))
                pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        chinese = chinese_bold = 'STSong-Light'
    else:
        with FONT_LOCK:
            fonts = [('BRProtocolYaHei','msyh.ttc'),('BRProtocolArial','arial.ttf'),
                     ('BRProtocolYaHeiBold','msyhbd.ttc'),('BRProtocolArialBold','arialbd.ttf')]
            for name,file in fonts:
                if name not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(name,str(FONT_DIR/file),subfontIndex=0))
        chinese,chinese_bold = 'BRProtocolYaHei','BRProtocolYaHeiBold'
    body = ParagraphStyle('ProtocolBody',fontName=chinese,fontSize=12,leading=18,
                          firstLineIndent=24,alignment=TA_LEFT,wordWrap='CJK')
    title_style = ParagraphStyle('ProtocolTitle',parent=body,fontName=chinese_bold,
                                 fontSize=19,leading=27,firstLineIndent=0,spaceAfter=12,textColor=HexColor(TITLE_INK),keepWithNext=True)
    heading = ParagraphStyle('ProtocolHeading',parent=body,fontName=chinese_bold,
                              firstLineIndent=0,spaceBefore=10,spaceAfter=3,textColor=HexColor(ACCENT),keepWithNext=True)
    source_style = ParagraphStyle('ProtocolSource',parent=body,firstLineIndent=0,textColor=HexColor('#52666F'))

    def markup(text,bold=False):
        latin = 'BRProtocolArialBold' if bold else 'BRProtocolArial'
        return ''.join(f'<font face="{latin}">{escape(run)}</font>' if i%2 else escape(run)
                       for i,run in enumerate(ASCII_RUNS.split(text)))
    story = [Paragraph(markup(display_title(result),True),title_style)]
    image = flow_image(result)
    if image:
        with PILImage.open(io.BytesIO(image)) as picture:
            factor = min(415/picture.width,236/picture.height)
            story.append(Paragraph('实验流程',heading))
            story.append(Image(io.BytesIO(image),width=picture.width*factor,height=picture.height*factor))
        story.append(Spacer(1,12))
    styles = {'body':body,'heading':heading,'source':source_style}
    story.extend(Paragraph(markup(line,role=='heading'),styles[role]) for role,line in document_sections(result))
    def page_chrome(canvas,document):
        page_width,page_height = A4
        canvas.saveState()
        canvas.setFont('BRProtocolYaHei',9)
        canvas.setFillColor(HexColor('#52666F'))
        header = 'AI Protocol  |  '+display_title(result)
        while pdfmetrics.stringWidth(header,'BRProtocolYaHei',9)>page_width-128:
            header = header.rstrip('…')[:-2]+'…'
        canvas.drawString(64,page_height-33,header)
        canvas.drawRightString(page_width-64,28,f'第 {document.page} 页')
        canvas.setStrokeColor(HexColor('#DCE4E8'))
        canvas.setLineWidth(.5)
        canvas.line(64,page_height-39,page_width-64,page_height-39)
        canvas.line(64,41,page_width-64,41)
        canvas.restoreState()

    SimpleDocTemplate(str(path),pagesize=A4,title=display_title(result),author=result.get('author',''),
                      leftMargin=64,rightMargin=64,topMargin=58,bottomMargin=54).build(
                          story,onFirstPage=page_chrome,onLaterPages=page_chrome)


def write_protocol(path,result,writer):
    from ai_cancel import check_cancelled
    target = Path(path)
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent,suffix=target.suffix,prefix='.protocol-',delete=False) as stream:
            temporary = Path(stream.name)
        try:
            clean = re.compile(r'[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD\U00010000-\U0010FFFF]')
            document = {**result,**{key:clean.sub('',result.get(key,'')) for key in ('title','text','author')}}
            writer(temporary,document)
            expected = b'%PDF-' if target.suffix.lower()=='.pdf' else b'PK'
            with temporary.open('rb') as file:
                if not file.read(5).startswith(expected):
                    raise ValueError('导出文件不完整，已保留原文件。')
            check_cancelled()
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    except InterruptedError:
        raise
    except PermissionError:
        raise ValueError('无法写入此位置。请关闭已打开的同名文件，或选择可写入的文件夹。') from None
    except OSError:
        raise ValueError('无法保存 Protocol。请检查目录、文件名和剩余磁盘空间。') from None
    return str(target)


def portable(result):
    title,text = result.get('title'),result.get('text')
    if not isinstance(title,str) or not title.strip() or len(title)>160 or not isinstance(text,str) or not text.strip() or len(text)>1_000_000:
        raise ValueError('分享包中的 Protocol 标题或正文无效。')
    sources = []
    raw_sources = result.get('sources',[])
    if not isinstance(raw_sources,list) or len(raw_sources)>120:
        raise ValueError('分享包的来源列表无效。')
    for source in raw_sources:
        if not isinstance(source,dict):
            raise ValueError('分享包的来源条目无效。')
        fields = {key:source.get(key,'') for key in ('id','title','coverage','url')}
        if any(not isinstance(value,str) or len(value)>1600 for value in fields.values()):
            raise ValueError('分享包的来源字段无效。')
        if not fields['url'].startswith(('https://','http://')):
            fields['url'] = ''
        sources.append({**fields,'origin':source.get('origin','')})
    filtered = without_local_references(dict(result,sources=sources))
    sources = [{key:value for key,value in row.items() if key!='origin'} for row in filtered['sources']]
    text = filtered['text']
    stages = result.get('stages',[])
    if not isinstance(stages,list) or len(stages)>7 or any(not isinstance(s,str) or len(s)>24 for s in stages):
        raise ValueError('分享包的实验阶段无效。')
    diagram = result.get('diagram')
    if diagram:
        from model_artifact import figure_bytes, validate_figure
        if not isinstance(diagram,dict):
            raise ValueError('分享包的流程图格式无效。')
        diagram = validate_figure(figure_bytes(diagram))
    author = result.get('author','')
    if author:
        author = normalize_author(author)
    return dict(title=title,text=text,sources=sources,stages=stages,diagram=diagram,author=author)


def share_protocols(path,results):
    records = [portable(result) for result in results]
    if not 1<=len(records)<=250:
        raise ValueError('每次可分享 1–250 条 Protocol。')
    payload = json.dumps(dict(format=FORMAT,records=records),ensure_ascii=False).encode('utf-8')
    if len(payload)>MAX_BUNDLE:
        raise ValueError('所选方案和流程图过大，请分批分享。')
    target = Path(path)
    with tempfile.NamedTemporaryFile(dir=target.parent,suffix='.tmp',delete=False) as stream:
        temporary = Path(stream.name)
    try:
        with ZipFile(temporary,'w',ZIP_DEFLATED) as archive:
            archive.writestr('protocols.json',payload)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return len(records)


def import_protocols(database,path):
    try:
        with ZipFile(path) as archive:
            if archive.namelist()!=['protocols.json'] or archive.getinfo('protocols.json').file_size>MAX_BUNDLE:
                raise ValueError('分享包结构无效或文件过大。')
            payload = json.loads(archive.read('protocols.json'))
    except (BadZipFile,RuntimeError,UnicodeError,RecursionError) as error:
        raise ValueError('无法读取此 Protocol 分享包。') from error
    if not isinstance(payload,dict) or payload.get('format')!=FORMAT or not isinstance(payload.get('records'),list) or not 1<=len(payload['records'])<=250:
        raise ValueError('此文件不是受支持的 Protocol 分享包。')
    if any(not isinstance(item,dict) for item in payload['records']):
        raise ValueError('分享包含有无效的方案。')
    records = [portable(item) for item in payload['records']]
    with database.connect() as connection:
        connection.executemany(
            "INSERT INTO ai_histories(kind,title,input_json,result_json,created_at) VALUES('protocol',?,'{}',?,?)",
            [(display_title(item),json.dumps(item,ensure_ascii=False),now()) for item in records])
    return len(records)
