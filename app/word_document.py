"""Portable OOXML documents with embedded EndNote records, independent of COM.

This exports a reference collection, not a native Mac Word toolbar. Every field
has a cached nonempty result so Word can display it before CWYW formats it.
"""
from io import BytesIO
import json,re,zipfile
import xml.etree.ElementTree as ET
from citations import ROOT,render

W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
ET.register_namespace('w',W)
def w(name):return '{'+W+'}'+name
def element(parent,local_name,text=None,**attributes):
    child=ET.SubElement(parent,w(local_name),{w(key):str(value) for key,value in attributes.items()})
    if text is not None:child.text=str(text)
    return child

def run(parent,text,superscript=False):
    value=element(parent,'r');properties=element(value,'rPr')
    element(properties,'rFonts',ascii='Arial',hAnsi='Arial',eastAsia='Arial',cs='Arial')
    element(properties,'sz',val=21);element(properties,'szCs',val=21)
    if superscript:element(properties,'vertAlign',val='superscript')
    node=element(value,'t',text);node.set('{http://www.w3.org/XML/1998/namespace}space','preserve')
    return value

def field(paragraph,code,lines,superscript=False):
    begin=element(paragraph,'r');element(begin,'fldChar',fldCharType='begin',dirty='false')
    instruction=element(element(paragraph,'r'),'instrText',' '+code+' ')
    instruction.set('{http://www.w3.org/XML/1998/namespace}space','preserve')
    element(element(paragraph,'r'),'fldChar',fldCharType='separate')
    for index,line in enumerate(lines):
        if index:element(element(paragraph,'r'),'br')
        run(paragraph,line,superscript)
    element(element(paragraph,'r'),'fldChar',fldCharType='end')

def endnote_citation(article,number,marker,superscript=False):
    node=ET.Element('EndNote');cite=ET.SubElement(node,'Cite')
    authors=[name.strip() for name in re.split(r';|\n',article.get('authors') or '') if name.strip()]
    year=str(article.get('publication_date') or '')[:4]
    for name,text in (('Author',authors[0] if authors else ''),('Year',year),('RecNum',number),('IDText',article.get('title') or '')):
        ET.SubElement(cite,name).text=str(text)
    display=ET.SubElement(cite,'DisplayText')
    if superscript:ET.SubElement(display,'style',face='superscript').text=str(marker)
    else:display.text=str(marker)
    record=ET.SubElement(cite,'record');ET.SubElement(record,'rec-number').text=str(number)
    ET.SubElement(record,'ref-type',name='Journal Article').text='17'
    people=ET.SubElement(ET.SubElement(record,'contributors'),'authors')
    for name in authors:ET.SubElement(people,'author').text=name
    titles=ET.SubElement(record,'titles')
    ET.SubElement(titles,'title').text=article.get('title') or ''
    ET.SubElement(titles,'secondary-title').text=article.get('journal_title') or ''
    ET.SubElement(ET.SubElement(record,'periodical'),'full-title').text=article.get('journal_title') or ''
    for field_name,tag in (('volume','volume'),('issue','number'),('pages','pages'),('doi','electronic-resource-num')):
        if article.get(field_name):ET.SubElement(record,tag).text=str(article[field_name])
    ET.SubElement(ET.SubElement(record,'dates'),'year').text=year
    ET.SubElement(record,'urls')
    return ET.tostring(node,encoding='unicode')

def document_bytes(articles,journal='Cell'):
    unique={}
    for article in articles:unique.setdefault(article['article_key'],dict(article))
    articles=list(unique.values())
    if not articles:raise ValueError('请至少选择一篇文章。')
    allowed=json.loads((ROOT/'word_catalogue.json').read_text(encoding='utf8'))
    if journal not in allowed:raise ValueError('请选择已核实的 NC 扩选与 EndNote 样式交集中的期刊。')
    rendered=render(articles,journal);rich=render(articles,journal,rich=True)
    document=ET.Element(w('document'));body=element(document,'body')
    run(element(body,'p'),'Biomed Reader · 引文集合')
    run(element(body,'p'),'以下标题用于识别各个引文。将需要的引文域复制到稿件中，再用 EndNote 的 Update Citations and Bibliography 更新。')
    for index,article in enumerate(articles):
        paragraph=element(body,'p');run(paragraph,(article.get('title') or '')+' ')
        superscript='<sup>' in rich['markers'][index]
        record=endnote_citation(article,1000001+index,rendered['markers'][index],superscript)
        field(paragraph,'ADDIN EN.CITE '+record,[rendered['markers'][index]],superscript)
    run(element(body,'p'),'参考文献')
    field(element(body,'p'),'ADDIN EN.REFLIST',rendered['references'])
    section=element(body,'sectPr');element(section,'pgSz',w=11906,h=16838)
    element(section,'pgMar',top=1440,right=1440,bottom=1440,left=1440)
    settings=ET.Element(w('settings'));variables=element(settings,'docVars')
    layout=ET.Element('ENLayout')
    for tag,text in (('Style',journal),('FontName','Arial'),('FontSize','10.5'),('StartingRefnum','1'),('HyperlinksEnabled','0')):
        ET.SubElement(layout,tag).text=text
    for name,value in [('EN.Layout',ET.tostring(layout,encoding='unicode')),('EN.Libraries','<Libraries></Libraries>'),
            ('EN.InstantFormat','<ENInstantFormat><Enabled>0</Enabled><ScanUnformatted>0</ScanUnformatted><ScanChanges>0</ScanChanges><Suspended>0</Suspended></ENInstantFormat>')]:
        element(variables,'docVar',name=name,val=value)
    element(settings,'updateFields',val='false')
    styles=ET.Element(w('styles'));defaults=element(styles,'docDefaults');props=element(element(defaults,'rPrDefault'),'rPr')
    element(props,'rFonts',ascii='Arial',hAnsi='Arial');element(props,'sz',val=21)
    content_types=b'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>'
    package_relationships=b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    document_relationships=b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'
    output=BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,data in [('word/document.xml',ET.tostring(document,encoding='utf-8',xml_declaration=True)),
                ('word/settings.xml',ET.tostring(settings,encoding='utf-8',xml_declaration=True)),('word/styles.xml',ET.tostring(styles,encoding='utf-8',xml_declaration=True)),
                ('[Content_Types].xml',content_types),('_rels/.rels',package_relationships),('word/_rels/document.xml.rels',document_relationships)]:archive.writestr(name,data)
    return output.getvalue()
