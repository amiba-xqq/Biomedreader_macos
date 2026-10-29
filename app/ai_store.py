"""Local AI configuration and confirmed usage; no prompts or plaintext secrets."""
import base64
import ctypes
import hashlib
import json
import sys
from ctypes import wintypes
from datetime import datetime,timezone

def is_text_model(identifier):
    name=str(identifier).lower().rsplit('/',1)[-1]
    return not name.startswith(('gpt-image','glm-image','cogview','seedream','seedance','stable-diffusion','sd3.','doubao-seedream','qwen-image','wan2.','z-image'))

def text_config(config):
    config['models_json']=json.dumps([row for row in json.loads(config['models_json']) if is_text_model(row.get('id'))],ensure_ascii=False)
    if not is_text_model(config['model']):config.update(model='',enabled=0)
    return config

PROVIDERS={'deepseek':'DeepSeek','codex':'ChatGPT Codex · 本机连接','openai':'ChatGPT · OpenAI API','kimi':'Kimi','glm':'GLM'}

def install_schema(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS ai_provider_configs (
        provider TEXT PRIMARY KEY, model TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 0,
        encrypted_key TEXT NOT NULL DEFAULT '',key_fingerprint TEXT NOT NULL DEFAULT '',
        models_json TEXT NOT NULL DEFAULT '[]',models_updated_at TEXT NOT NULL DEFAULT '')''')
    connection.execute('''CREATE TABLE IF NOT EXISTS ai_call_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,provider TEXT NOT NULL,key_fingerprint TEXT NOT NULL,
        model TEXT NOT NULL,called_at TEXT NOT NULL,input_tokens INTEGER NOT NULL,output_tokens INTEGER NOT NULL,
        cached_tokens INTEGER NOT NULL,total_tokens INTEGER NOT NULL,usage_known INTEGER NOT NULL,
        outcome TEXT NOT NULL)''')
    connection.execute('CREATE INDEX IF NOT EXISTS ai_usage_provider ON ai_call_usage(provider,key_fingerprint)')
    connection.execute('CREATE TABLE IF NOT EXISTS ai_lab_session(id INTEGER PRIMARY KEY CHECK(id=1),active INTEGER NOT NULL,encrypted_token TEXT NOT NULL,identity_json TEXT NOT NULL,fingerprint TEXT NOT NULL)')
    connection.execute('CREATE TABLE IF NOT EXISTS ai_model_efforts(owner TEXT NOT NULL,provider TEXT NOT NULL,model TEXT NOT NULL,effort TEXT NOT NULL,PRIMARY KEY(owner,provider,model))')
    connection.execute('CREATE TABLE IF NOT EXISTS ai_lab_models(owner TEXT NOT NULL,provider TEXT NOT NULL,model TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 0,models_json TEXT NOT NULL,PRIMARY KEY(owner,provider))')

def key_fingerprint(key):return hashlib.sha256(key.encode('utf8')).hexdigest()

def validated_key(key):
    key=key.strip()
    if not key or len(key)>2048 or any(ord(char)<33 or ord(char)>126 for char in key):
        raise ValueError('请输入有效的 API Key；密钥不能包含空格、换行或非 ASCII 字符。')
    return key

class Blob(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]

def _protect(value,decrypt=False):
    if sys.platform=='darwin':
        # Only the wrapping key is in Keychain. Database backups contain ciphertext.
        from keyring.backends.macOS import Keyring
        from cryptography.fernet import Fernet
        keyring=Keyring();key=keyring.get_password('org.orangereader.biomedliterature','api-key-encryption')
        if not key:
            if decrypt:raise ValueError('当前 Mac 钥匙串没有此资料库的加密密钥，请重新配置 API Key。')
            key=Fernet.generate_key().decode('ascii');keyring.set_password('org.orangereader.biomedliterature','api-key-encryption',key)
        cipher=Fernet(key.encode('ascii'))
        return cipher.decrypt(value) if decrypt else cipher.encrypt(value)
    if sys.platform!='win32':raise ValueError('此 Windows 版使用当前 Windows 用户的 DPAPI 加密，请在 Windows 中配置密钥。')
    buffer=ctypes.create_string_buffer(value)
    source=Blob(len(value),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));target=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    if decrypt:
        function=crypt.CryptUnprotectData
        function.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        args=(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target))
    else:
        function=crypt.CryptProtectData
        function.argtypes=[ctypes.POINTER(Blob),wintypes.LPCWSTR,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        args=(ctypes.byref(source),'Biomed Reader API Key',None,None,None,1,ctypes.byref(target))
    function.restype=wintypes.BOOL
    if not function(*args):raise ValueError('无法使用当前 Windows 用户加密或解密密钥，请在本机重新输入 API Key。')
    try:return ctypes.string_at(target.data,target.size)
    finally:
        if target.data:
            ctypes.memset(target.data,0,target.size);kernel.LocalFree(ctypes.cast(target.data,ctypes.c_void_p))

def encrypt_key(key):return base64.b64encode(_protect(validated_key(key).encode('utf8'))).decode('ascii')
def decrypt_key(value):
    try:return validated_key(_protect(base64.b64decode(value,validate=True),True).decode('utf8'))
    except Exception as error:raise ValueError('已保存的密钥不能在当前系统用户下读取，请重新配置 API Key。') from None

def migrate_bailian_settings(database):
    # Preserve each user's chosen family/model and encrypted credential. Do not
    # merge keys between lab members or replace an already configured Bailian key.
    with database.connect() as c:
        settings=dict(c.execute('SELECT key,value FROM settings'))
        route=settings.get('ai_image_route','')
        prefixes={'image_'}
        for key in settings:
            if key.startswith('image_lab_'):
                for old in ('qwen','wan','zimage'):
                    suffix='_'+old+'_model'
                    if key.endswith(suffix):prefixes.add(key[:-len(old+'_model')])
        for prefix in prefixes:
            if prefix+'bailian_model' in settings:continue
            choices=[route] if route in ('qwen','wan','zimage') else []
            choices += [v for v in ('qwen','wan','zimage') if v not in choices]
            old=next((v for v in choices if settings.get(prefix+v+'_key') or settings.get(prefix+v+'_model')),None)
            if old:
                for field in ('key','model','region','enabled'):
                    key=prefix+old+'_'+field
                    if key in settings:c.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)',(prefix+'bailian_'+field,settings[key]))
        if route in ('qwen','wan','zimage'):c.execute("UPDATE settings SET value='bailian' WHERE key='ai_image_route'")

class AIStore:
    def __init__(self,database):
        self.database=database
        migrate_bailian_settings(database)
        with database.connect() as connection:
            install_schema(connection)
            connection.execute("UPDATE ai_provider_configs SET enabled=0 WHERE provider='biomni'")
            connection.execute("UPDATE settings SET value='off' WHERE key='ai_image_route' AND value IN ('stability','byteplus','biomni')")
            connection.execute("UPDATE settings SET value='0' WHERE key='ai_incremental_reports' AND EXISTS(SELECT 1 FROM settings WHERE key='ai_report_provider' AND value='biomni')")
            connection.execute("UPDATE settings SET value='deepseek' WHERE key='ai_report_provider' AND value='biomni'")
    def config(self,provider):
        if provider not in PROVIDERS:raise ValueError('未知 AI 服务。')
        lab=self.lab_state() if provider!='codex' else None
        if lab and lab['active']:
            with self.database.connect() as connection:row=connection.execute('SELECT * FROM ai_lab_models WHERE owner=? AND provider=?',(lab['identity']['user_id'],provider)).fetchone()
            config=dict(provider=provider,model=row['model'] if row else '',enabled=row['enabled'] if row else 0,models_json=row['models_json'] if row else '[]',encrypted_key='lab-managed',key_fingerprint=lab['fingerprint'],route='lab',models_updated_at='')
            config['reasoning_effort']=self.effort(provider,config['model']);config['gateway_url']=lab['identity'].get('gateway_url','');return text_config(config)
        with self.database.connect() as connection:
            row=connection.execute('SELECT * FROM ai_provider_configs WHERE provider=?',(provider,)).fetchone()
        config=dict(row) if row else dict(provider=provider,model='',enabled=0,encrypted_key='',key_fingerprint='',models_json='[]',models_updated_at='')
        config['reasoning_effort']=self.effort(provider,config['model']);return text_config(config)
    def effort_owner(self,provider):
        lab=self.lab_state() if provider!='codex' else None
        return 'lab:'+lab['identity']['user_id'] if lab and lab['active'] else 'personal'
    def effort(self,provider,model):
        with self.database.connect() as connection:row=connection.execute('SELECT effort FROM ai_model_efforts WHERE owner=? AND provider=? AND model=?',(self.effort_owner(provider),provider,model)).fetchone()
        return row['effort'] if row else 'default'
    def save_effort(self,provider,model,effort,metadata=None):
        from reasoning import options
        if provider not in PROVIDERS or not model or effort not in options(provider,model,metadata):raise ValueError('请选择当前模型支持的推理强度。')
        with self.database.connect() as connection:connection.execute('INSERT INTO ai_model_efforts VALUES(?,?,?,?) ON CONFLICT(owner,provider,model) DO UPDATE SET effort=excluded.effort',(self.effort_owner(provider),provider,model,effort))
    def key(self,provider):
        lab=self.lab_state() if provider!='codex' else None
        if lab and lab['active']:
            # Server checks the current grant on every request. A PI may bind a
            # new provider after the student signed in; do not deny stale lists.
            return decrypt_key(lab['encrypted_token'])
        config=self.config(provider)
        if not config['encrypted_key']:raise ValueError('请先在 AI 部署中配置 API Key。')
        return decrypt_key(config['encrypted_key'])
    def save(self,provider,key,model,models):
        if not is_text_model(model):raise ValueError('此模型仅用于绘图，请在模型图设置中配置。')
        models=[row for row in models if is_text_model(row.get('id'))]
        self.config(provider);key=validated_key(key)
        if not model or model not in {row['id'] for row in models}:raise ValueError('请先联网刷新模型列表，再选择一个模型。')
        lab=self.lab_state() if provider!='codex' else None
        if lab and lab['active']:
            with self.database.connect() as connection:connection.execute('INSERT INTO ai_lab_models VALUES(?,?,?,0,?) ON CONFLICT(owner,provider) DO UPDATE SET model=excluded.model,models_json=excluded.models_json',(lab['identity']['user_id'],provider,model,json.dumps(models,ensure_ascii=False)))
            return
        encrypted=encrypt_key(key);fingerprint=key_fingerprint(key)
        with self.database.connect() as connection:
            connection.execute('''INSERT INTO ai_provider_configs(provider,model,encrypted_key,key_fingerprint,models_json,models_updated_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(provider) DO UPDATE SET model=excluded.model,
                encrypted_key=excluded.encrypted_key,key_fingerprint=excluded.key_fingerprint,
                models_json=excluded.models_json,models_updated_at=excluded.models_updated_at''',
                (provider,model,encrypted,fingerprint,json.dumps(models,ensure_ascii=False),datetime.now(timezone.utc).isoformat()))
    def toggle(self,provider):
        config=self.config(provider)
        if not config['enabled']:
            self.key(provider)
            if not config['model']:raise ValueError('请先设置并选择模型。')
        lab=self.lab_state() if provider!='codex' else None
        with self.database.connect() as connection:
            if lab and lab['active']:connection.execute('UPDATE ai_lab_models SET enabled=? WHERE owner=? AND provider=?',(not config['enabled'],lab['identity']['user_id'],provider))
            else:connection.execute('UPDATE ai_provider_configs SET enabled=? WHERE provider=?',(not config['enabled'],provider))
    def forget(self,provider):
        lab=self.lab_state() if provider!='codex' else None
        with self.database.connect() as connection:
            if lab and lab['active']:connection.execute('DELETE FROM ai_lab_models WHERE owner=? AND provider=?',(lab['identity']['user_id'],provider))
            else:connection.execute('DELETE FROM ai_provider_configs WHERE provider=?',(provider,))
    def lab_state(self):
        with self.database.connect() as connection:row=connection.execute('SELECT * FROM ai_lab_session WHERE id=1').fetchone()
        return {**dict(row),'identity':json.loads(row['identity_json'])} if row else None
    def save_lab_session(self,token,identity):
        encrypted=encrypt_key(token)
        with self.database.connect() as connection:connection.execute('INSERT INTO ai_lab_session VALUES(1,1,?,?,?) ON CONFLICT(id) DO UPDATE SET active=1,encrypted_token=excluded.encrypted_token,identity_json=excluded.identity_json,fingerprint=excluded.fingerprint',(encrypted,json.dumps(identity,ensure_ascii=False),key_fingerprint(token)))
    def lab_token(self):
        state=self.lab_state()
        if not state:raise ValueError('请先登录课题组。')
        return decrypt_key(state['encrypted_token'])
    def use_lab(self,active):
        if active and not self.lab_state():raise ValueError('请先登录 PI 或学生账户。')
        with self.database.connect() as connection:connection.execute('UPDATE ai_lab_session SET active=? WHERE id=1',(bool(active),))
    def logout_lab(self):
        with self.database.connect() as connection:connection.execute('DELETE FROM ai_lab_session')
    def client(self,provider,key=None):
        config=self.config(provider);key=key or self.key(provider)
        if key_fingerprint(key)!=config['key_fingerprint']:raise ValueError('账户或调用通道已变化，请重新发起操作。')
        return self.client_factory(provider,config)(provider,key)
    def client_factory(self,provider,config=None):
        # Freeze the transport route before dispatch. A later UI mode change
        # must never send a gateway session to an official provider endpoint.
        from copy import deepcopy
        from reasoning import configured_client
        snapshot=deepcopy(config if config is not None else self.config(provider))
        if provider=='codex':
            from codex_bridge import CodexClient
            factory=CodexClient
        elif snapshot.get('route')=='lab':
            from lab_service import LabClient
            factory=LabClient
        else:
            from ai_service import AIClient
            factory=AIClient
        if provider=='biomni':
            from biomni_service import BiomniClient
            def biomni_factory(p,k):
                from lab_service import BiomniLabClient
                client=configured_client(BiomniLabClient if snapshot.get('route')=='lab' else BiomniClient,p,k,snapshot);client.settings_database=self.database;return client
            return biomni_factory
        return lambda p,k:configured_client(factory,p,k,snapshot)
    def usage(self,provider):
        config=self.config(provider)
        with self.database.connect() as connection:
            row=connection.execute('''SELECT COUNT(*) AS calls,COALESCE(SUM(input_tokens),0) AS input_tokens,
                COALESCE(SUM(output_tokens),0) AS output_tokens,COALESCE(SUM(cached_tokens),0) AS cached_tokens,
                COALESCE(SUM(total_tokens),0) AS total_tokens,COALESCE(SUM(usage_known=0),0) AS unknown_calls,
                MAX(called_at) AS last_call FROM ai_call_usage WHERE provider=? AND key_fingerprint=?''',
                (provider,config['key_fingerprint'])).fetchone()
        return dict(row)
    def record(self,provider,fingerprint,model,usage=None,outcome='completed'):
        usage=usage or {}
        def number(name):
            value=usage.get(name,0)
            return max(0,int(value)) if isinstance(value,(int,float)) and not isinstance(value,bool) else 0
        with self.database.connect() as connection:
            connection.execute('''INSERT INTO ai_call_usage(provider,key_fingerprint,model,called_at,input_tokens,output_tokens,
                cached_tokens,total_tokens,usage_known,outcome) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (provider,fingerprint,model,datetime.now(timezone.utc).isoformat(),number('input_tokens'),number('output_tokens'),
                 number('cached_tokens'),number('total_tokens'),int(bool(usage.get('known'))),outcome))
