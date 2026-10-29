"""Authenticated lab gateway; student credentials never become provider API keys."""
import uuid,time
from ai_service import AIClient,AIAPIError,AITransportError,Bearer

LAB_URL='https://biomedreader-lab.1310305204.workers.dev'
LAB_BACKUP_URL='https://biomedreader.1310305204.workers.dev'
_preferred_gateway=LAB_URL
def configure_system_proxy(session):
    """Re-read the OS proxy for lab requests, including changes after app launch.

    Keep certificate verification enabled. Never fall back to an unverified
    connection or an arbitrary gateway supplied by a server response.
    """
    import sys,urllib.request
    if sys.platform=='win32':
        proxies=urllib.request.getproxies_registry()
    elif sys.platform=='darwin':
        proxies=urllib.request.getproxies_macosx_sysconf()
    else:
        proxies=urllib.request.getproxies()
    session.proxies.update({k:v for k,v in proxies.items() if k in ('http','https') and v})
    return bool(session.proxies)
from ai_store import PROVIDERS
LAB_PROVIDERS={**{p:n for p,n in PROVIDERS.items() if p!='codex'},'ark':'火山引擎方舟 · Ark（绘图）','bailian':'阿里云百炼（绘图）'}
LAB_PROVIDERS['glm']='GLM（含GLM-Image绘图）'
ERRORS={'session_expired_or_disabled':'课题组登录已过期或账户已被停用，请重新登录。','pi_required':'此功能仅限 PI 使用。',
 'invalid_student_id':'学生访问ID无效或已被停用，请联系 PI。','provider_not_configured':'PI 尚未配置该服务商。',
 'key_bound_elsewhere':'该 Key 已属于另一个课题组，请使用该 Key 登录原课题组。','concurrent_limit':'本用户正在处理的请求较多，请等待后重试。',
 'request_already_submitted':'该请求已提交，未重复调用模型。若响应丢失，请检查用量后再手动发起新请求。',
 'rate_limit':'操作过于频繁，请稍后重试。','not_ready':'课题组服务尚未就绪。','resource_not_owned':'此云端资料不属于当前学生账户，已拒绝访问。','invalid_provider':'此平台不属于当前功能支持的API通道。'}
ERRORS.update(provider_metadata_transport='课题组服务器暂未连接到服务商的模型列表接口。此步骤不调用生成模型，不产生模型Token费用；请稍后重试。',provider_redirect_rejected='服务商接口返回了意外跳转，已停止转发凭证。请联系开发者检查服务地址。')
ERRORS.update(invalid_key='API Key格式不正确，请只粘贴服务商提供的密钥，不含Bearer前缀或空格。',session_required='请重新登录课题组。',registration_failed='课题组登记暂未完成，请稍后重新登录。')

def gateway_error(response):
    try:code=response.json().get('error','')
    except Exception:code=''
    if code in ERRORS:return ERRORS[code]
    if code in ('provider_transport_unknown','provider_invalid_response'):return '服务商响应未能确认，可能已经计费。未自动重试，请先核对用量。'
    if code.startswith('provider_rejected_'):return '服务商拒绝了请求，请由 PI 检查密钥、额度及所选模型权限。未自动重试。'
    return f'课题组服务未完成请求（HTTP {response.status_code}），请检查登录与网络后重试。'

class LabAPI:
    def __init__(self,token=None,gateway=None):self.token=token;self.gateway=gateway if gateway in (LAB_URL,LAB_BACKUP_URL) else None
    def request(self,method,path,payload=None):
        global _preferred_gateway
        # Authentication/read-only metadata may reconnect; never replay mutations
        # such as creating/resetting students, or any billable generation request.
        reconnect=path in ('/pi/login','/student/login') or method=='GET'
        initial_endpoint=self.gateway or _preferred_gateway
        for attempt in range(3 if reconnect else 1):
            with AIClient('deepseek',self.token or 'public-registration-request') as client:
                endpoint=initial_endpoint if attempt!=1 else (LAB_BACKUP_URL if initial_endpoint==LAB_URL else LAB_URL)
                if attempt==2:configure_system_proxy(client.session)
                client.base=endpoint+'/api/lab';client.request_timeout=(20,600 if path=='/image' else 120 if path=='/biomni' else 60)
                client.timeout_message='课题组登录／管理连接超时；未调用生成模型，不产生模型Token费用。'
                client.http_error=gateway_error
                if not self.token:
                    from software_update import PublicRequest
                    client.session.auth=PublicRequest()
                try:
                    data=client.request(method,path,payload);_preferred_gateway=endpoint
                    if data.get('ok') is False:
                        from types import SimpleNamespace
                        raise AIAPIError(gateway_error(SimpleNamespace(status_code=502,json=lambda:data)))
                    if path in ('/pi/login','/student/login') and isinstance(data.get('identity'),dict):data['identity']['gateway_url']=endpoint
                    return data
                except AITransportError:
                    if reconnect and attempt<2:time.sleep(.7);continue
                    suffix='未调用生成模型，不产生模型Token费用。' if reconnect else '请求可能已送达，请核对用量或操作结果后再试。'
                    raise AITransportError('无法连接课题组服务，已尝试备用服务和重新读取系统代理。请确认当前网络可访问 workers.dev，并检查系统代理是否正常。此提示不代表API Key或学生ID错误。'+suffix) from None
                except AIAPIError as error:raise AIAPIError('课题组服务请求未成功：'+str(error)) from None
    def login_pi(self,provider,key):return self.request('POST','/pi/login',dict(provider=provider,api_key=key))
    def login_student(self,access):return self.request('POST','/student/login',dict(user_id=access.strip()))
    def me(self):return self.request('GET','/me')['identity']
    def usage(self):return self.request('GET','/usage')['members']
    def create_student(self,name):return self.request('POST','/members',dict(name=name))
    def member_action(self,identifier,action):return self.request('POST','/members/action',dict(user_id=identifier,action=action))
    def save_key(self,provider,key):return self.request('POST','/keys',dict(provider=provider,api_key=key))
    def image(self,provider,payload):return self.request('POST','/image',dict(provider=provider,payload=payload,request_id=str(uuid.uuid4())))
    def biomni(self,method,path,payload=None):return self.request('POST','/biomni',dict(method=method,path=path,payload=payload,request_id=str(uuid.uuid4())))
    def logout(self):return self.request('POST','/logout',{})

class LabClient(AIClient):
    def generate_pdf(self,*args,**kwargs):return super().generate_pdf(*args,**kwargs)
    def __init__(self,provider,token):super().__init__(provider,token);self.base=_preferred_gateway+'/api/lab'
    def http_error(self,response):return gateway_error(response)
    def request(self,method,path,payload=None):
        self.request_timeout=(10,600 if method=='POST' else 45)
        if path=='/models':return super().request('GET','/models?provider='+self.provider)
        if path in ('/user/balance','/users/me/balance'):return super().request('GET','/balance?provider='+self.provider)
        if path in ('/responses','/chat/completions'):
            data=super().request('POST','/invoke',dict(provider=self.provider,endpoint=path,payload=payload,request_id=str(uuid.uuid4())))
            if data.get('ok') is False:
                from types import SimpleNamespace
                raise AIAPIError(gateway_error(SimpleNamespace(status_code=502,json=lambda:data)))
            return data
        raise AIAPIError('不支持的课题组接口。')

from biomni_service import BiomniClient,TIERS
class BiomniLabClient(BiomniClient):
    def __init__(self,provider,token):
        super().__init__(provider,token);self.base=_preferred_gateway+'/api/lab'
    def request(self,method,path,payload=None):
        gateway=self.base.removesuffix('/api/lab');return LabAPI(self.key,gateway).biomni(method,path,payload)
    def models(self):
        with AIClient('deepseek',self.key) as client:
            client.base=self.base;client.http_error=gateway_error
            result=client.request('GET','/models?provider=biomni')
        return result['data']
