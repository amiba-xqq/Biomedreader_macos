"""Observe a loaded article; never click, solve or bypass a verification gate."""
import hashlib,re,time,json
from urllib.parse import urlsplit,unquote

PAGE_STATE_JS=r'''(()=>{const visible=e=>{if(!e)return false;const s=getComputedStyle(e),r=e.getBoundingClientRect();return s.display!=='none'&&s.visibility!=='hidden'&&r.width>0&&r.height>0&&r.bottom>0&&r.right>0&&r.top<innerHeight&&r.left<innerWidth};const gate=Array.from(document.querySelectorAll('input[type="password"],iframe[src*="captcha"],iframe[src*="challenge"],#challenge-running,#challenge-stage,.g-recaptcha,.cf-turnstile')).some(visible);return JSON.stringify({ready:document.readyState==='complete',gate:gate,url:location.href,text:(document.body?.innerText||'').slice(0,500000)})})()'''

def decode_page_state(value):
    if isinstance(value,str):
        try:value=json.loads(value) if len(value)<=2_000_000 else None
        except ValueError:return {}
    return value if isinstance(value,dict) else {}

class ArticleReadyState:
    def __init__(self,clock=time.monotonic):self.clock=clock;self.previous=None;self.since=0;self.observations=0
    def reset(self):self.previous=None;self.since=0;self.observations=0
    def observe(self,html,url,requested_url,snapshot):
        from ai_content import ArticleHTML,html_document
        if not isinstance(snapshot,dict) or not snapshot.get('ready') or snapshot.get('gate'):self.reset();return False
        parsed=urlsplit(url)
        if parsed.scheme not in ('http','https') or not parsed.hostname or snapshot.get('url')!=url:self.reset();return False
        text=str(snapshot.get('text') or '');prefix=text[:2200].casefold()
        blocked=re.search(r'verify (?:that )?you are human|checking your browser|security verification|just a moment|access denied|complete the captcha|sign in to your account|请.{0,12}(?:人机|安全)验证|人机验证|安全验证中',prefix)
        if blocked or len(text)<350:self.reset();return False
        requested=urlsplit(requested_url);doi=unquote(requested.path).lstrip('/') if requested.hostname in ('doi.org','dx.doi.org') else ''
        parser=ArticleHTML();parser.feed(html);meta=parser.meta
        actual=str(meta.get('citation_doi') or '').strip().casefold()
        for prefix in ('https://doi.org/','http://doi.org/','doi:'):
            if actual.startswith(prefix):actual=actual[len(prefix):].strip()
        if doi:
            # A redirect/login shell is not sufficient: require the expected DOI.
            if actual!=doi.casefold():self.reset();return False
        elif not (requested.hostname==parsed.hostname and requested.path.rstrip('/')==parsed.path.rstrip('/') and meta.get('citation_title')):self.reset();return False
        try:document=html_document(html,url,dict(doi=doi,title=meta.get('citation_title') or ''))
        except ValueError:self.reset();return False
        has_abstract=bool(meta.get('citation_abstract') or re.search(r'\babstract\b|摘要',text,re.I))
        if document['blocked'] or not (document['fulltext'] or (has_abstract and meta.get('citation_title'))):self.reset();return False
        # Hash article content, not site clocks/adverts or the challenge countdown.
        content=''.join(parser.article_text) or document['text'];fingerprint=(url,hashlib.sha256(content.encode('utf8')).hexdigest(),document['fulltext'])
        now=self.clock()
        if fingerprint!=self.previous:self.previous=fingerprint;self.since=now;self.observations=1;return False
        self.observations+=1
        return self.observations>=2 and now-self.since>=(1.2 if document['fulltext'] else 6)
