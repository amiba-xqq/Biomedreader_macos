"""Conservative paper-model candidates; never substitute a random website logo."""
import base64,io,re
from html.parser import HTMLParser

MODEL_CAPTION=re.compile(r'(graphical\s+abstract|(?:proposed|working|schematic|mechanistic)\s+model|model\s+(?:for|of)\s+(?:the\s+)?(?:mechanism|regulation))',re.I)


class ModelImageHTML(HTMLParser):
    """Cell/Elsevier div figures, JATS figures and regular HTML figures."""
    def __init__(self):super().__init__();self.stack=[];self.candidates=[]
    def handle_starttag(self,tag,attrs):
        values=dict(attrs)
        if tag in ('figure','div','section'):
            identity=' '.join(values.get(k,'') for k in ('id','class','aria-label'));self.stack.append(dict(tag=tag,identity=identity,text='',images=[]))
        if tag=='img':
            src=values.get('data-src') or values.get('data-lazy-src') or values.get('src') or ''
            sources=values.get('data-srcset') or values.get('srcset')
            if sources:src=sources.split(',')[-1].strip().split()[0]
            for block in self.stack:block['images'].append(dict(url=src,alt=values.get('alt','')))
    def handle_data(self,data):
        for block in self.stack:
            if len(block['text'])<14000:block['text']+=data+' '
    def handle_endtag(self,tag):
        if tag not in ('figure','div','section'):return
        index=next((i for i in range(len(self.stack)-1,-1,-1) if self.stack[i]['tag']==tag),None)
        if index is None:return
        blocks=self.stack[index:];self.stack=self.stack[:index]
        for block in blocks:
            caption=re.sub(r'\s+',' ',block['identity']+' '+block['text']).strip();explicit=bool(re.search(r'graphical[-_\s]*abstract',caption,re.I))
            if not (explicit or MODEL_CAPTION.search(caption)) or len(block['text'])>10000:continue
            for row in block['images']:
                if not row['url'] or any(word in row['url'].lower() for word in ('logo','icon','avatar','tracking')):continue
                self.candidates.append(dict(url=row['url'],caption=caption[:900],priority=0 if explicit else 1,size=len(block['text'])))


def html_model_candidate(html):
    parser=ModelImageHTML();parser.feed(html)
    return min(parser.candidates,key=lambda row:(row['priority'],row['size'])) if parser.candidates else None

def image_record(image,caption,source):
    if image.width*image.height>24000000:raise ValueError('Image dimensions too large')
    image=image.convert('RGB');image.thumbnail((1360,1100));buffer=io.BytesIO();image.save(buffer,format='PNG')
    if len(buffer.getvalue())>3*1024*1024:raise ValueError('Image candidate too large')
    return dict(kind='paper',image_base64=base64.b64encode(buffer.getvalue()).decode('ascii'),caption=caption,source=source)

def pdf_model_candidate(document,label):
    # Graphical abstracts often precede the article, while mechanism models are
    # usually near the end. Include vector artwork by rendering the original page.
    ordered=list(document.pages[:4])+list(reversed(document.pages[-8:]));seen=set()
    for page in ordered:
        if page.page_number in seen:continue
        seen.add(page.page_number)
        text=page.extract_text() or '';match=MODEL_CAPTION.search(text)
        if not match:continue
        nearby=text[max(0,match.start()-100):match.end()+250]
        if 'graphical' not in match.group().lower() and not re.search(r'(?:Fig(?:ure)?\.?\s*\d|schematic|图\s*\d)',nearby,re.I):continue
        if 'graphical' in match.group().lower() and len(text)<2600:
            try:return image_record(page.to_image(resolution=120).original,'论文Graphical Abstract · PDF 第'+str(page.page_number)+'页',label)
            except Exception:continue
        images=[item for item in page.images+page.curves+page.rects if item.get('width',0)>90 and item.get('height',0)>50]
        if not images:continue
        bounds=(max(page.bbox[0],min(item['x0'] for item in images)),max(page.bbox[1],min(item['top'] for item in images)),min(page.bbox[2],max(item['x1'] for item in images)),min(page.bbox[3],max(item['bottom'] for item in images)))
        try:return image_record(page.crop(bounds).to_image(resolution=120).original,'论文模型图候选 · PDF 第'+str(page.page_number)+'页（请核对）',label)
        except Exception:continue
    return None

def web_model_candidate(url,caption):
    from ai_content import download,source_label
    from PIL import Image
    data,final,kind=download(url)
    if len(data)>8*1024*1024 or not kind.lower().startswith('image/'):return None
    with Image.open(io.BytesIO(data)) as image:return image_record(image,caption or '论文模型图候选（请核对）',source_label(final))
