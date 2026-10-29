"""First-page title recognition and PubMed-only PDF matching, outside the GUI thread."""
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
import re
import time
import unicodedata
import xml.etree.ElementTree as ET

from literature import LiteratureClient, NCBI_ESEARCH_URL, NCBI_EFETCH_URL
from network_transport import refresh_error_message


def normalized_title(value):
    return ' '.join(re.findall(r'[^\W_]+',unicodedata.normalize('NFKC',value).casefold()))


def recognize_pdf(path):
    """Find a substantial large-font block, combining lines and attached superscripts.

    A lone Article/Review heading is not a title. Metadata is accepted only when
    it agrees with the actual first-page large text, never merely the filename.
    """
    import pdfplumber
    with pdfplumber.open(path) as document:
        if not document.pages:raise ValueError('PDF 没有页面。')
        page=document.pages[0]
        words=page.extract_words(extra_attrs=['size'],x_tolerance=2,y_tolerance=3)
        chars=page.chars
        if not words:raise ValueError('首页没有可提取文字（可能为扫描件），无法可靠识别标题。')
        sizes=Counter(round(w['size'],1) for w in words if w['top']<page.height*.6)
        candidates=[]
        for size in sorted(sizes,reverse=True):
            if size<10:continue
            main=[w for w in words if abs(w['size']-size)<.35 and w['top']<page.height*.52]
            lines=[]
            for word in sorted(main,key=lambda w:(w['top'],w['x0'])):
                line=next((r for r in lines if abs(r[0]['top']-word['top'])<max(2,size*.12)),None)
                if line is None:lines.append([word])
                else:line.append(word)
            blocks=[]
            for line in lines:
                if blocks and line[0]['top']-blocks[-1][-1][0]['top']<size*1.9:
                    blocks[-1].append(line)
                else:blocks.append([line])
            for block in blocks:
                count=sum(len(line) for line in block)
                raw=' '.join(w['text'] for line in block for w in sorted(line,key=lambda w:w['x0']))
                if count<5 or len(normalized_title(raw))<25:continue
                candidates.append((size*(1-block[0][0]['top']/page.height)*min(count,25)**.3,size,block))
        if not candidates:raise ValueError('未找到足够完整的大字号标题，未按文件名猜测文章。')
        _,size,block=max(candidates,key=lambda c:c[0])
        x0=min(w['x0'] for line in block for w in line)-2;x1=max(w['x1'] for line in block for w in line)+2
        rows=[(min(w['top'] for w in line),max(w['bottom'] for w in line)) for line in block]
        grouped=[[] for _ in rows]
        for char in chars:
            center=(char['top']+char['bottom'])/2
            if not x0<=char['x0']<=x1 or char['size']<size*.45:continue
            index=min(range(len(rows)),key=lambda i:abs(center-sum(rows[i])/2))
            top,bottom=rows[index]
            if top-size*.35<=center<=bottom+size*.1:grouped[index].append(char)
        fragments=[]
        for row in grouped:
            text='';previous=None
            for char in sorted(row,key=lambda c:c['x0']):
                if previous and char['x0']-previous['x1']>max(1.4,size*.18) and not text.endswith(' ') and not char['text'].startswith(' '):text+=' '
                text+=char['text'];previous=char
            fragments.append(re.sub(r'\s+',' ',unicodedata.normalize('NFKC',text)).strip())
        title=' '.join(fragments)
        title=re.sub(r'[-‐]\s+(?=\w)','-',title).strip()
        metadata=str(document.metadata.get('Title') or '').strip()
        if metadata and SequenceMatcher(None,normalized_title(metadata),normalized_title(title)).ratio()>=.94:
            title=unicodedata.normalize('NFKC',metadata)
        if len(normalized_title(title).split())<5:raise ValueError('识别出的标题过短，未自动收藏。')
        first_page=page.extract_text() or ''
        dois={match.rstrip('.,;)').lower() for match in re.findall(r'10\.\d{4,9}/[^\s<>]+',first_page,re.I)}
        return {'title':title,'dois':dois}


def match_pdf(client, recognized, issns, scope='default'):
    """Search the complete recognized title; no publication date/JIF/candidate presets."""
    if not issns:raise ValueError('必须选择至少一个非空期刊目录。')
    title=recognized['title'];clean=re.sub(r'["\[\]]',' ',title).strip()
    journal_clause=' OR '.join('"'+i+'"[Journal]' for i in dict.fromkeys(issns))
    title_term='("'+clean+'"'+('[Title/Abstract]' if scope=='title_abstract' else '')+')'
    term=title_term+' AND ('+journal_clause+')'
    response=client.pubmed_request(NCBI_ESEARCH_URL,dict(db='pubmed',term=term,sort='relevance',retmode='json',retmax=10,tool='BiomedReader'))
    response.raise_for_status();payload=response.json()
    if payload.get('error'):raise ValueError(str(payload['error']))
    ids=payload.get('esearchresult',{}).get('idlist',[]);time.sleep(.36)
    if not ids and recognized.get('dois'):
        # Superscripts/ligatures can be indexed with different word boundaries.
        # DOI is only a retrieval aid: the complete title is still verified below,
        # and the selected journal clause is never removed.
        doi_clause=' OR '.join('"'+doi+'"[AID]' for doi in sorted(recognized['dois']))
        assisted='('+title_term+' OR ('+doi_clause+')) AND ('+journal_clause+')'
        response=client.pubmed_request(NCBI_ESEARCH_URL,dict(db='pubmed',term=assisted,sort='relevance',retmode='json',retmax=10,tool='BiomedReader'))
        response.raise_for_status();ids=response.json().get('esearchresult',{}).get('idlist',[]);time.sleep(.36)
    if not ids:raise ValueError('所选期刊范围内，PubMed 未找到符合完整标题的文章。')
    response=client.pubmed_request(NCBI_EFETCH_URL,dict(db='pubmed',id=','.join(ids),retmode='xml',tool='BiomedReader'))
    response.raise_for_status();records={}
    for node in ET.fromstring(response.content).findall('./PubmedArticle'):
        article,_=client.normalize_pubmed(node,title);records[article['pmid']]=article
    time.sleep(.36)
    target=normalized_title(title);tokens=set(target.split())
    for identifier in ids:
        article=records.get(identifier)
        if not article:continue
        other=normalized_title(article['title']);other_tokens=set(other.split())
        similarity=SequenceMatcher(None,target,other).ratio()
        overlap=len(tokens&other_tokens)/max(1,len(tokens|other_tokens))
        # Respect PubMed Best Match order, but reject unrelated top results.
        if similarity>=.86 and overlap>=.70:
            if recognized.get('dois') and article.get('doi') and article['doi'].lower() not in recognized['dois']:continue
            return article
    raise ValueError('PubMed 返回结果与首页标题/DOI 不一致，未自动收藏，避免误匹配。')


def import_pdfs(database, files, group_id, issns, scope='default', progress=lambda value:None, cancelled=lambda:False, client=None, link_pdf=True):
    own=client is None;client=client or LiteratureClient('')
    report={'total':len(files),'completed':0,'successes':[],'failures':[],'cancelled':False,'link_pdf':bool(link_pdf)}
    try:
        for path in files:
            if cancelled():report['cancelled']=True;break
            path=Path(path)
            progress({**report,'current':path.name})
            try:
                recognized=recognize_pdf(path)
                if cancelled():report['cancelled']=True;break
                article=match_pdf(client,recognized,issns,scope)
                if cancelled():report['cancelled']=True;break
                key=database.save_pdf_favorite(article,path,group_id,link_pdf=link_pdf)
                report['successes'].append({'file':str(path),'title':article['title'],'article_key':key})
            except Exception as error:
                report['failures'].append({'file':str(path),'reason':refresh_error_message(error)})
            report['completed']+=1
            progress({**report,'current':path.name})
    finally:
        if own:client.session.close()
    return report


def pdf_runtime_self_test(paths,destination):
    """Verify bundled PDF parser resources without opening the user's library."""
    import json
    from version import APP_VERSION
    report={'version':APP_VERSION,'files':[]}
    try:
        for path in paths:
            row=recognize_pdf(path);row['dois']=sorted(row['dois']);row['file']=str(path);report['files'].append(row)
        report['passed']=True;status=0
    except Exception as error:report.update(passed=False,error=refresh_error_message(error));status=1
    Path(destination).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return status
