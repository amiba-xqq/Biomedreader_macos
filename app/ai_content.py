"""Read documents on this computer; never give cloud models campus credentials."""
import io,ipaddress,re,socket,threading
from pathlib import Path
from urllib.parse import urlsplit,urljoin
from html.parser import HTMLParser
from software_update import session_for_updates
import requests

def source_label(value):
    parsed=urlsplit(value)
    return parsed._replace(query='',fragment='').geturl() if parsed.scheme in ('http','https') else value

class ArticleHTML(HTMLParser):
    def __init__(self):super().__init__();self.text=[];self.article_text=[];self.in_article=0;self.skip=0;self.meta={};self.figures=[];self.current_figure=None
    def append(self,text):
        self.text.append(text)
        if self.in_article:self.article_text.append(text)
        if self.current_figure is not None:self.current_figure['text'].append(text)
    def handle_starttag(self,tag,attrs):
        values=dict(attrs)
        if tag=='figure':self.current_figure=dict(text=[],url='')
        if tag=='img' and self.current_figure is not None and not self.current_figure['url']:self.current_figure['url']=values.get('data-src') or values.get('src') or ''
        if tag in ('article','main'):self.in_article+=1
        if tag in ('script','style','nav','header','footer','noscript','svg'):self.skip+=1
        if tag=='meta':self.meta[(values.get('name') or values.get('property') or '').lower()]=values.get('content','')
        if tag in ('p','h1','h2','h3','h4','div','section','tr','li'):self.append('\n')
    def handle_endtag(self,tag):
        if tag=='figure' and self.current_figure is not None:self.figures.append(self.current_figure);self.current_figure=None
        if tag in ('script','style','nav','header','footer','noscript','svg') and self.skip:self.skip-=1
        if tag in ('p','h1','h2','h3','h4','section','tr','li'):self.append('\n')
        if tag in ('article','main'):self.in_article=max(0,self.in_article-1)
    def handle_data(self,data):
        if not self.skip:self.append(data+' ')

def html_document(html,url,article):
    parser=ArticleHTML();parser.feed(html)
    article_text=''.join(parser.article_text);raw=article_text if len(article_text)>1500 else ''.join(parser.text)
    text='\n'.join(line.strip() for line in re.sub('[ \t]+',' ',raw).splitlines() if line.strip())
    expected=str(article.get('doi') or '').lower().removeprefix('https://doi.org/')
    actual=parser.meta.get('citation_doi','').lower().removeprefix('https://doi.org/')
    if expected and actual and expected!=actual:raise ValueError('网页返回了另一篇文章的 DOI，未将其作为本篇内容。')
    expected_words=set(re.findall(r'[a-z0-9]{3,}',str(article.get('title') or '').lower()))-{'the','and','with','for','from'}
    observed=(parser.meta.get('citation_title') or parser.meta.get('og:title') or text).lower()
    title_matches=bool(expected_words) and len(expected_words & set(re.findall(r'[a-z0-9]{3,}',observed)))/len(expected_words)>=.55
    if expected and not actual and expected not in html.lower() and not title_matches:
        raise ValueError('未能确认网页属于目标文章，未把门户、登录或其它文章页面当作正文。')
    lowered=text.lower()
    blocked=any(word in lowered for word in ('verify you are human','just a moment','checking your browser','access denied','captcha'))
    blocked|=any(word in lowered for word in ('this is a preview of subscription content','purchase this article','rent this article'))
    sections=sum(bool(re.search(r'(?m)^\s*(?:\d+[.\s]*)?'+name+r'\s*$',lowered)) for name in ('introduction','methods','results','discussion'))
    body_available=(sections>=3 or len(article_text)>8000) and len(text)>6000 and not blocked
    coverage='网页正文候选（请核查完整性；未分析补充材料或图片像素）' if body_available else '网页片段／可能仅摘要'
    from source_figures import MODEL_CAPTION
    model_figure=next((item for item in parser.figures if item['url'] and MODEL_CAPTION.search(''.join(item['text']))),None)
    from source_figures import html_model_candidate
    candidate=html_model_candidate(html)
    if candidate:model_figure=dict(url=candidate['url'],text=[candidate['caption']])
    return dict(text=text,url=source_label(url),coverage=coverage,fulltext=body_available,
                model_image_url=urljoin(url,model_figure['url']) if model_figure else '',model_image_caption=''.join(model_figure['text']).strip()[:600] if model_figure else '',
                pdf_url=urljoin(url,parser.meta.get('citation_pdf_url','')) if parser.meta.get('citation_pdf_url') else '',blocked=blocked)

def public_url(url,resolve=True):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,443):raise ValueError('仅允许读取公开 HTTPS 文章地址。')
    host=parsed.hostname.lower()
    if host=='localhost' or host.endswith(('.local','.localhost','.internal')):raise ValueError('不读取本机或内部网络地址。')
    try:addresses=[ipaddress.ip_address(host)]
    except ValueError:
        addresses=[ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)] if resolve else []
    if any(not address.is_global for address in addresses):raise ValueError('文章地址指向非公开网络，已停止读取。')
    return url

def download(url,session=None):
    own=session is None;session=session or session_for_updates()
    try:
        for _ in range(8):
            public_url(url)
            response=session.get(url,timeout=(10,40),allow_redirects=False,stream=True)
            try:
                if response.status_code in (301,302,303,307,308):url=urljoin(url,response.headers.get('Location',''));continue
                if response.status_code!=200:raise ValueError(f'文章网页暂不可读取（HTTP {response.status_code}）；可尝试本机浏览器登录，或链接已下载 PDF。')
                chunks=[];length=0
                for chunk in response.iter_content(65536):
                    length+=len(chunk)
                    if length>50*1024*1024:raise ValueError('文章文件超过 50 MB，请链接较小的正文 PDF。')
                    chunks.append(chunk)
                data=b''.join(chunks)
                return data,url,response.headers.get('Content-Type','')
            finally:response.close()
        raise ValueError('文章网页重定向过多，请使用本机浏览器读取。')
    finally:
        if own:session.close()

def pdf_document(source,label):
    import pdfplumber
    pages=[];characters=0
    with pdfplumber.open(source) as document:
        for index,page in enumerate(document.pages,1):
            text=page.extract_text() or '';characters+=len(text)
            pages.append(f'[PDF 第 {index} 页]\n{text}')
        try:
            from source_figures import pdf_model_candidate
            figure=pdf_model_candidate(document,source_label(label))
        except Exception:figure=None
    if characters<300:raise ValueError('PDF 可提取文字太少，可能是扫描件；本版本不把未识别的图片当作已读全文。')
    return dict(text='\n\n'.join(pages),url=source_label(label),coverage='PDF 可提取文字（未分析图片像素及未包含的补充材料）',fulltext=True,figure=figure)

def read_article(database,article,web_fetch=None,prefer_pdf=True,progress=lambda text:None):
    errors=[];key=article['article_key']
    if prefer_pdf:
        path=database.linked_pdf(key)
        if path:
            try:
                progress('正在读取已链接 PDF：'+path.name)
                return pdf_document(path,'已链接本地 PDF：'+path.name)
            except Exception as error:errors.append(str(error))
    doi=str(article.get('doi') or '')
    url=('https://doi.org/'+doi) if doi else str(article.get('landing_page_url') or '')
    if url:
        try:
            progress('正在通过本机网络读取 DOI 网页……')
            if web_fetch:
                payload=web_fetch(url)
                if payload.get('pdf_path'):
                    try:return pdf_document(payload['pdf_path'],payload['url'])
                    finally:payload['temporary'].cleanup()
                document=html_document(payload['html'],payload['url'],article)
            else:
                data,final,kind=download(url)
                if data.startswith(b'%PDF'):return pdf_document(io.BytesIO(data),final)
                document=html_document(data.decode('utf-8',errors='replace'),final,article)
            if document.get('model_image_url'):
                try:
                    from source_figures import web_model_candidate,image_record
                    captured=payload.get('model_image_base64') if web_fetch else None
                    if captured:
                        import base64
                        from PIL import Image
                        with Image.open(io.BytesIO(base64.b64decode(captured,validate=True))) as original:document['figure']=image_record(original,document['model_image_caption'],document['model_image_url'])
                    else:document['figure']=web_model_candidate(document['model_image_url'],document['model_image_caption'])
                except Exception:pass
            if document['fulltext']:
                return document
            if document.get('pdf_url'):
                try:
                    data,final,kind=download(document['pdf_url'])
                    if data.startswith(b'%PDF'):return pdf_document(io.BytesIO(data),final)
                except Exception:errors.append('网页提供的 PDF 暂不可读取。')
            errors.append('未确认获取到正文。网页片段不作为完整文章。')
            if document.get('figure'):figure=document['figure']
        except InterruptedError:raise
        except requests.RequestException:errors.append('本机网络未取得文章正文，可能需要授权登录、网页验证或检查网络。')
        except Exception as error:errors.append(str(error)[:300])
    abstract=str(article.get('abstract') or '')
    if not abstract.strip():raise ValueError('未取得可用正文或摘要。请链接 PDF，或在本机文章浏览器中完成访问验证后重试。')
    return dict(text='标题：'+str(article.get('title') or '')+'\n摘要：'+abstract,url=source_label(url),figure=locals().get('figure'),
        coverage='仅标题与摘要；'+ '；'.join(errors)[:600],fulltext=False)

class ArticleWebReader:
    """QObject bridge is constructed lazily to keep HTTP/PDF-only tests headless."""
    def __new__(cls,data_directory,parent):
        from PySide6.QtCore import QObject,Signal,QTimer,QUrl,Qt
        from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QPushButton,QHBoxLayout
        class Reader(QObject):
            requested=Signal(object)
            expired=Signal(object)
            def __init__(self):
                super().__init__(parent);self.queue=[];self.active=None;self.dialog=None;self.requested.connect(self.enqueue);self.expired.connect(self.expire)
                self.auto_timer=QTimer(self);self.auto_timer.setInterval(3000);self.auto_timer.timeout.connect(self.auto_probe);self.probing=False;self.capturing=False
            def get(self,url,automatic=False):
                public_url(url)
                request=dict(url=url,event=threading.Event(),result=None,error=None,automatic=automatic)
                self.requested.emit(request)
                if not request['event'].wait(600):request['error']='本机网页读取等待超时。';request['event'].set();self.expired.emit(request)
                if request['error']:
                    if request['error'].startswith('用户取消'):raise InterruptedError(request['error'])
                    raise ValueError(request['error'])
                return request['result']
            def enqueue(self,request):self.queue.append(request);self.advance()
            def advance(self):
                if self.active or not self.queue:return
                self.active=self.queue.pop(0)
                if self.active['event'].is_set():self.active=None;self.advance();return
                if self.dialog is None:
                    from PySide6.QtWebEngineWidgets import QWebEngineView
                    from PySide6.QtWebEngineCore import QWebEngineProfile,QWebEnginePage,QWebEngineSettings
                    self.dialog=QDialog(parent);self.dialog.setWindowFlags(Qt.WindowType.Window|Qt.WindowType.WindowMinMaxButtonsHint|Qt.WindowType.WindowCloseButtonHint)
                    self.dialog.setWindowTitle('本机文章读取 · 请自行完成授权登录');self.dialog.resize(1100,820)
                    layout=QVBoxLayout(self.dialog);self.read_note=QLabel();self.read_note.setWordWrap(True);layout.addWidget(self.read_note)
                    self.view=QWebEngineView(self.dialog);self.profile=QWebEngineProfile('ArticleAIReader',self.dialog)
                    self.profile.setPersistentStoragePath(str(Path(data_directory)/'article_web_session'));self.profile.setCachePath(str(Path(data_directory)/'article_web_cache'))
                    self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
                    self.profile.downloadRequested.connect(self.download_pdf)
                    page=QWebEnginePage(self.profile,self.view);page.settings().setAttribute(QWebEngineSettings.WebAttribute.ForceDarkMode,False);self.view.setPage(page);layout.addWidget(self.view,1)
                    page.settings().setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled,False)
                    row=QHBoxLayout();read=QPushButton('读取当前文章');read.clicked.connect(self.capture);row.addWidget(read);cancel=QPushButton('取消本次网页读取');cancel.clicked.connect(self.dialog.reject);row.addWidget(cancel);layout.addLayout(row);self.dialog.rejected.connect(self.cancel)
                from article_autoread import ArticleReadyState
                self.active['ready_state']=ArticleReadyState();self.probing=False;self.capturing=False
                self.dialog.setWindowTitle('本机文章读取 · 自动检测正文' if self.active.get('automatic') else '本机文章读取 · 请自行完成授权登录')
                self.read_note.setText('进入目标文章且内容加载稳定后会自动读取；如需学校／出版社登录或人机验证，请自行完成。本窗口不会自动操作验证或将密码、Cookie发送给AI。也可点击“读取当前文章”。' if self.active.get('automatic') else '请确认已进入目标文章，必要时自行完成学校／出版社登录、验证或展开正文，然后点击读取。不会将密码或Cookie发送给AI。')
                self.view.load(QUrl(self.active['url']));self.dialog.showNormal()
                if self.active.get('automatic'):self.auto_timer.start()
            def auto_probe(self):
                request=self.active
                if not request or not request.get('automatic') or self.probing or self.capturing:return
                from article_autoread import PAGE_STATE_JS,decode_page_state
                self.probing=True;url=self.view.url().toString()
                def state_received(snapshot):
                    if self.active is not request:return
                    snapshot=decode_page_state(snapshot)
                    if snapshot.get('gate') or __import__('re').search(r'verify.*human|just a moment|checking your browser|人机验证|安全验证',str(snapshot.get('text') or '')[:2200],__import__('re').I):
                        self.probing=False;request['ready_state'].reset();self.auto_timer.setInterval(10000);return
                    self.auto_timer.setInterval(3000)
                    if not isinstance(snapshot,dict) or snapshot.get('url')!=url or self.view.url().toString()!=url:self.probing=False;request['ready_state'].reset();return
                    def html_received(html):
                        if self.active is not request:return
                        self.probing=False
                        if self.view.url().toString()!=url:request['ready_state'].reset();return
                        if request['ready_state'].observe(html,url,request['url'],snapshot):self.capture()
                    self.view.page().toHtml(html_received)
                self.view.page().runJavaScript(PAGE_STATE_JS,state_received)
            def capture(self):
                request=self.active
                if not request or self.capturing:return
                url=self.view.url().toString()
                try:public_url(url,False)
                except ValueError:return
                self.capturing=True
                def received(html):
                    if self.active is not request:return
                    if self.view.url().toString()!=url:self.capturing=False;return
                    from source_figures import html_model_candidate
                    candidate=html_model_candidate(html)
                    if not candidate:self.finish(dict(html=html,url=url));return
                    target=urljoin(url,candidate['url'])
                    # Read already-rendered pixels in this local browser session.
                    # Canvas access respects CORS. Never export cookies/passwords.
                    javascript='''(()=>{const target=%s;const im=Array.from(document.images).find(i=>i.src===target||i.currentSrc===target);if(!im||!im.complete||im.naturalWidth<120||im.naturalWidth*im.naturalHeight>24000000)return '';try{const canvas=document.createElement('canvas');const scale=Math.min(1,1600/im.naturalWidth,1200/im.naturalHeight);canvas.width=im.naturalWidth*scale;canvas.height=im.naturalHeight*scale;canvas.getContext('2d').drawImage(im,0,0,canvas.width,canvas.height);return canvas.toDataURL('image/png').split(',')[1]}catch(e){return ''}})()'''
                    import json
                    def pixels(data):
                        if self.active is request:
                            if self.view.url().toString()!=url:self.capturing=False;return
                            self.finish(dict(html=html,url=url,model_image_base64=data if isinstance(data,str) and len(data)<6*1024*1024 else ''))
                    self.view.page().runJavaScript(javascript % json.dumps(target),pixels)
                self.view.page().toHtml(received)
            def expire(self,request):
                if self.active is request:self.finish(error=request['error'])
                elif request in self.queue:self.queue.remove(request)
            def download_pdf(self,download):
                request=self.active
                if not request:download.cancel();return
                url=download.url().toString()
                try:public_url(url,False)
                except ValueError:download.cancel();return
                if 'pdf' not in download.mimeType().lower() and not urlsplit(url).path.lower().endswith('.pdf'):download.cancel();return
                import tempfile
                temporary=tempfile.TemporaryDirectory(prefix='biomed-ai-paper-')
                download.setDownloadDirectory(temporary.name);download.setDownloadFileName('paper.pdf')
                def size_check():
                    if download.receivedBytes()>50*1024*1024 or download.totalBytes()>50*1024*1024:download.cancel()
                def changed(state):
                    from PySide6.QtWebEngineCore import QWebEngineDownloadRequest
                    if state==QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
                        path=Path(temporary.name)/'paper.pdf'
                        if self.active is request and path.is_file():self.finish(dict(pdf_path=path,temporary=temporary,url=url))
                        else:temporary.cleanup()
                    elif state in (QWebEngineDownloadRequest.DownloadState.DownloadCancelled,QWebEngineDownloadRequest.DownloadState.DownloadInterrupted):
                        temporary.cleanup()
                        if self.active is request:self.finish(error='本机 PDF 下载未完成。可下载后在收藏中链接该 PDF。')
                download.receivedBytesChanged.connect(size_check);download.totalBytesChanged.connect(size_check);download.stateChanged.connect(changed);download.accept()
            def finish(self,result=None,error=None):
                self.auto_timer.stop();self.probing=False;self.capturing=False
                if self.active:
                    self.active['result']=result;self.active['error']=error;self.active['event'].set();self.active=None
                if self.dialog:self.dialog.hide()
                QTimer.singleShot(0,self.advance)
            def cancel(self):
                for request in self.queue:request['error']='用户取消本机网页读取。';request['event'].set()
                self.queue=[];self.finish(error='用户取消本机网页读取。')
        return Reader()
