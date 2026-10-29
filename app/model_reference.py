"""Original reference PDF and high-resolution visual attachments."""
import base64,hashlib,io,json,re,sys
from pathlib import Path
from functools import lru_cache
REFERENCE_SHA256='6ed1ea9a4c2de244915a45e8b491d3ecbd206dd44e428d0967c818c2eca835f6'
DIAGRAM_INSTRUCTIONS='模型图不能有太多文字，要美观且符合杂志发表要求，并且易于读者理解'

@lru_cache(maxsize=1)
def reference_pdf():
    path=Path(getattr(sys,'_MEIPASS',Path(__file__).parent))/'assets/model_reference/Model20250528.pdf';data=path.read_bytes()
    if not data.startswith(b'%PDF') or hashlib.sha256(data).hexdigest()!=REFERENCE_SHA256:raise ValueError('原始模型图参考PDF缺失或校验失败，请重新安装。')
    return data

@lru_cache(maxsize=1)
def page_images():
    import pypdfium2
    document=pypdfium2.PdfDocument(reference_pdf());result=[]
    try:
        for index in range(len(document)):
            page=document[index];bitmap=page.render(scale=3);buffer=io.BytesIO()
            try:bitmap.to_pil().save(buffer,format='PNG');result.append('data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode('ascii'))
            finally:bitmap.close();page.close()
    finally:document.close()
    return tuple(result)

def attachment(provider):
    common=dict(filename='Model20250528.pdf',sha256=REFERENCE_SHA256)
    if provider in ('openai','biomni'):return dict(**common,mode='pdf',file_data='data:application/pdf;base64,'+base64.b64encode(reference_pdf()).decode('ascii'))
    return dict(**common,mode='page_images',images=page_images())

def parse_diagram(text):
    from ai_editorial import parse_editorial
    data=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',text.strip(),flags=re.I))
    if not isinstance(data,dict):raise ValueError('模型未返回可识别的机制图结构。')
    raw=data.get('diagram',data)
    if not isinstance(raw,dict) or not raw.get('nodes'):raise ValueError('模型未返回机制图对象，未显示旧图。')
    return parse_editorial(json.dumps(dict(scientific_question='机制图结构校验',diagram=raw),ensure_ascii=False))['diagram']
