"""Biomedical Biomni Lab / Phylo API. Not Biomni.com's IT service platform."""
import base64,re,time,threading
from urllib.parse import urlsplit
import requests
from ai_service import AIClient,AIAPIError,AITransportError,normal_usage

BASE='https://api.phylo.bio/v1'
TIERS=[dict(id='standard',name='standard · 均衡（官方API档位）'),dict(id='fast',name='fast · 快速（官方API档位）'),dict(id='max',name='max · 最高能力（官方API档位）')]
_project_lock=threading.Lock()

def identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',value):raise ValueError('Biomni返回的资源标识无法识别。')
    return value

def final_text(messages):
    for message in reversed(messages):
        if message.get('role')!='assistant' or message.get('status') not in (None,'completed'):continue
        parts=[]
        for block in reversed(message.get('content') or []):
            if block.get('type')=='text' and isinstance(block.get('text'),str):parts.append(block['text'])
            elif parts:break
        if parts:return '\n'.join(reversed(parts))
    raise ValueError('Biomni尚未返回最终文字结果；未缓存工具日志或思考过程。')

def storage_url(value):
    parsed=urlsplit(value)
    if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in (None,443) or not (parsed.hostname=='api.phylo.bio' or str(parsed.hostname).endswith('.amazonaws.com')):raise ValueError('Biomni文件地址不是已验证的HTTPS存储域名，未传送文件或凭据。')
    return value

class BiomniClient(AIClient):
    def __init__(self,provider='biomni',key=None,session=None):
        super().__init__('openai',key,session);self.provider='biomni';self.base=BASE;self.cancelled=lambda:False;self.project_id=None;self.settings_database=None
    def request(self,method,path,payload=None):
        if not path.startswith('/') or '://' in path:raise ValueError('Biomni接口路径无效。')
        try:
            response=self.session.request(method,self.base+path,json=payload,timeout=(10,90),allow_redirects=False)
            try:
                if response.status_code not in (200,201,202,204):
                    message={401:'API Key无效或已失效。请使用Biomni Lab的Phylo API Key，不是基础模型Key或IT平台Key。',403:'账户尚无Biomni Lab API权限；Phylo API为早期访问，需在官方平台申请开通。',429:'Biomni请求受限，请稍后重试。'}.get(response.status_code,'Biomni请求未成功（HTTP '+str(response.status_code)+'）；未自动重新提交任务。')
                    raise AIAPIError(message)
                value={} if response.status_code==204 else response.json()
                if not isinstance(value,dict):raise AIAPIError('Biomni响应格式无法识别。')
                return value
            finally:response.close()
        except requests.RequestException:raise AITransportError('Biomni连接中断；未自动重新提交任务。已提交的云端任务可能仍运行，请在官方平台核对。') from None
    def models(self):
        self.request('GET','/projects?limit=1');return [dict(row) for row in TIERS]
    def balance(self):return None
    def project(self):
        with _project_lock:
            if self.project_id:return self.project_id
            from ai_store import key_fingerprint
            setting='biomni_project_'+key_fingerprint(self.key)
            if self.settings_database:self.project_id=self.settings_database.get_settings().get(setting)
            if self.project_id:return identifier(self.project_id)
            self.project_id=identifier(self.request('POST','/projects',dict(title='Biomed Reader',description='Biomedical literature analysis requested by Biomed Reader'))['id'])
            if self.settings_database:self.settings_database.save_settings({setting:self.project_id})
            return self.project_id
    def wait(self,task_id):
        deadline=time.monotonic()+24*60*60
        while time.monotonic()<deadline:
            if self.cancelled():
                self.request('POST','/experimental/tasks/'+task_id+'/cancel',{});raise InterruptedError('已请求取消Biomni云端任务；用量以官方平台为准。')
            task=self.request('GET','/experimental/tasks/'+task_id);status=task.get('status')
            if status=='completed':return
            if status in ('failed','cancelled'):raise ValueError('Biomni云端任务'+status+'；未自动重新创建任务。')
            until=time.monotonic()+10
            while time.monotonic()<until and not self.cancelled():time.sleep(.25)
        raise ValueError('Biomni任务仍未结束；未自动重新提交，请在官方平台检查任务。')
    def messages(self,task_id):
        rows=[];cursor=None;seen=set()
        for _ in range(1000):
            page=self.request('GET','/experimental/tasks/'+task_id+'/messages?limit=500'+('&before_id='+identifier(cursor) if cursor else ''));rows=(page.get('data') or [])+rows
            if not page.get('has_more'):return rows
            cursor=page.get('first_id')
            if not cursor or cursor in seen:raise ValueError('Biomni消息分页异常，未拼接不完整结果。')
            seen.add(cursor)
        raise ValueError('Biomni消息分页过多，请在官方平台查看。')
    def run(self,model,instructions,text,file_ids=None):
        if model not in {r['id'] for r in TIERS}:raise ValueError('请选择Biomni官方任务档位。')
        content='任务要求（只交付最终结果，不输出内部思考或工具日志）：\n'+instructions+'\n\n提供的科研材料：\n'+text
        payload=dict(project_id=self.project(),title='Biomed Reader · biomedical task',model=model,disable_fallbacks=True,auto_mode=True)
        if not file_ids:payload['initial_messages']=[dict(role='user',content=content)]
        task_id=identifier(self.request('POST','/experimental/tasks',payload)['id']);self.last_task_id=task_id
        if file_ids:self.request('POST','/experimental/tasks/'+task_id+'/messages',dict(content=content,file_ids=file_ids))
        self.wait(task_id);return task_id
    def complete(self,model,instructions,text,max_output_tokens=None,reference=None):
        files=[]
        if reference:
            from model_reference import reference_pdf
            files=[self.upload(reference['filename'],reference_pdf(),'application/pdf')]
        task=self.run(model,instructions,text,files);return final_text(self.messages(task)),normal_usage(None),False
    def transfer(self,url,raw=None,fields=None,filename=None):
        from network_transport import VerifiedHTTPSAdapter
        from model_pdf import MAX_PDF_BYTES
        url=storage_url(url)
        # Presigned storage URLs never receive the account Authorization header.
        with requests.Session() as session:
            session.trust_env=False;session.mount('https://',VerifiedHTTPSAdapter())
            if raw is not None:
                response=session.post(url,data=fields or {},files={'file':(filename,raw)},timeout=(10,120),allow_redirects=False)
                try:
                    if response.status_code not in (200,201,204):raise ValueError('Biomni参考文件上传未完成。')
                    return None
                finally:response.close()
            response=session.get(url,timeout=(10,120),allow_redirects=False,stream=True)
            try:
                if response.status_code!=200:raise ValueError('Biomni模型图下载未完成。')
                blocks=[];size=0
                for block in response.iter_content(65536):
                    size+=len(block)
                    if size>MAX_PDF_BYTES:raise ValueError('Biomni PDF超过24 MB，已停止下载。')
                    blocks.append(block)
                return b''.join(blocks)
            finally:response.close()
    def upload(self,filename,raw,mime):
        intent=self.request('POST','/experimental/files',dict(project_id=self.project(),filename=filename,size_bytes=len(raw),mime_type=mime))
        if intent.get('upload_type')!='single':raise ValueError('参考文件需要分块上传，当前Biomni连接未上传该文件。')
        self.transfer(intent['upload_url'],raw,intent.get('fields'),filename);file=identifier(intent['file_id']);self.request('POST','/experimental/files/'+file+'/finalize',{});return file
    def generate_pdf(self,model,instructions,text,reference):
        from model_reference import reference_pdf
        from model_artifact import FIGURE_DELIVERY,choose_file,file_format
        file=self.upload(reference['filename'],reference_pdf(),'application/pdf');self.pdf_started=True
        task=self.run(model,instructions,FIGURE_DELIVERY+'\n\n'+text,[file]);self.pdf_usage=normal_usage(None);cursor=None;files=[]
        for _ in range(1000):
            page=self.request('GET','/experimental/tasks/'+task+'/results?with_urls=true&limit=100'+('&after_id='+identifier(cursor) if cursor else ''))
            for item in page.get('data') or []:
                if file_format(item.get('name')):files.append(dict(filename=item['name'],download_url=item.get('download_url','')))
            if not page.get('has_more'):break
            next_cursor=page.get('last_id')
            if not next_cursor or next_cursor==cursor:break
            cursor=next_cursor
        return choose_file(files,lambda item:self.transfer(item['download_url']),reference),self.pdf_usage,False
    def describe_image(self,model,instructions,data_url):
        mime=data_url.split(';',1)[0].removeprefix('data:')
        if mime not in ('image/png','image/jpeg'):raise ValueError('Biomni图片格式暂只接受PNG或JPEG。')
        raw=base64.b64decode(data_url.split(',',1)[1],validate=True);file=self.upload('research-image.png' if mime=='image/png' else 'research-image.jpg',raw,mime);task=self.run(model,instructions,'请读取上传的科研图片。',[file]);return final_text(self.messages(task)),normal_usage(None),False
