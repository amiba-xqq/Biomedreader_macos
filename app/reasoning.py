"""Conservative provider-native effort capabilities; unknown models stay default."""
import json,re
LABELS={'default':'服务商默认','none':'关闭思考','enabled':'开启思考','minimal':'最低','low':'低','medium':'中','high':'高','xhigh':'极高','max':'最大','ultra':'超高'}

def options(provider,model,metadata=None):
    metadata=metadata or {};levels=None
    if isinstance(metadata.get('supportedReasoningEfforts'),list):levels=[r.get('reasoningEffort') for r in metadata['supportedReasoningEfforts'] if isinstance(r,dict)]
    if isinstance(metadata.get('effort'),dict) and isinstance(metadata['effort'].get('supported_levels'),list):levels=metadata['effort']['supported_levels']
    if levels is None:
        m=re.sub(r'-\d{4}-\d{2}-\d{2}$','',model.lower())
        if provider=='openai':
            if m in ('gpt-6.1-sol','gpt-6-astra','gpt-5.6-sol','gpt-5.6-terra','gpt-5.6-luna'):levels=['low','medium','high','xhigh','max']
            elif m in ('gpt-6-sol','gpt-6-luna'):levels=['none','low','medium','high','xhigh','max']
            elif m in ('gpt-5','gpt-5-mini','gpt-5-nano'):levels=['minimal','low','medium','high']
            elif m=='gpt-5.1':levels=['none','low','medium','high']
            elif m in ('gpt-5.1-codex','gpt-5.1-codex-mini'):levels=['low','medium','high']
            elif m in ('gpt-5.2','gpt-5.4','gpt-5.4-mini','gpt-5.4-nano','gpt-5.5'):levels=['none','low','medium','high','xhigh']
            elif m in ('o1','o3','o3-mini','o4-mini'):levels=['low','medium','high']
        elif provider=='deepseek' and m in ('deepseek-flash','deepseek-v4-pro','deepseek-v4-flash'):levels=['none','low','high','max']
        elif provider=='kimi':
            if m=='kimi-k3':levels=['low','high','max']
            elif m in ('kimi-k2.6','kimi-k2.5'):levels=['none','enabled']
        elif provider=='glm':
            if m in ('glm-5.3','glm-5.3-flash'):levels=['none','low','high','max']
            elif m=='glm-5.2':levels=['none','high','max']
            elif m.startswith(('glm-4.5','glm-4.6','glm-4.7','glm-5.1')) or m=='glm-5':levels=['none','enabled']
    if provider=='deepseek' and levels and 'none' not in levels and model in ('deepseek-flash','deepseek-v4-pro','deepseek-v4-flash'):levels=['none']+list(levels)
    return ['default']+list(dict.fromkeys(v for v in (levels or []) if isinstance(v,str) and v in LABELS and v!='default'))

def metadata_for(config):return next((r for r in json.loads(config.get('models_json') or '[]') if r.get('id')==config.get('model')),{})

def description(value):return LABELS.get(value or 'default','服务商默认')

def payload_fields(provider,effort,responses=False):
    if not effort or effort=='default':return {}
    if provider=='openai':return {'reasoning':{'effort':effort}} if responses else {'reasoning_effort':effort}
    if provider=='deepseek':
        if responses:return {'reasoning':{'effort':effort}}
        return {'thinking':{'type':'disabled' if effort=='none' else 'enabled'},**({} if effort in ('none','enabled') else {'reasoning_effort':effort})}
    if provider in ('kimi','glm'):
        return {'thinking':{'type':'disabled' if effort=='none' else 'enabled'}} if effort in ('none','enabled') else {'reasoning_effort':effort,**({'thinking':{'type':'enabled'}} if provider=='glm' else {})}
    return {}

def configured_client(factory,provider,key,config):
    effort=config.get('reasoning_effort','default')
    if effort not in options(provider,config.get('model',''),metadata_for(config)):raise ValueError('模型支持的推理强度已变化，请重新打开模型设置。')
    client=factory(provider,key)
    if config.get('route')=='lab':
        from lab_service import LAB_URL,LAB_BACKUP_URL
        if config.get('gateway_url') in (LAB_URL,LAB_BACKUP_URL):client.base=config['gateway_url']+'/api/lab'
    client.reasoning_effort=effort;return client

def record(config):
    value=config.get('reasoning_effort','default');metadata=metadata_for(config)
    advertised=config.get('reasoning_default_advertised') or metadata.get('defaultReasoningEffort') or (metadata.get('effort') or {}).get('default_level')
    return dict(reasoning_effort=value,reasoning_label=description(value),reasoning_default_advertised=advertised if value=='default' else None)

def result_label(result):
    if 'reasoning_effort' not in result:return '推理强度：未记录（历史结果）'
    text='推理强度：'+description(result['reasoning_effort'])
    if result.get('reasoning_default_advertised'):text+='（接口公布默认：'+description(result['reasoning_default_advertised'])+'）'
    return text

from PySide6.QtWidgets import QWidget,QVBoxLayout,QComboBox,QLabel
class ReasoningPicker(QWidget):
    """Reload capabilities on selection; never carry an effort to another model."""
    def __init__(self,store,provider,parent=None):
        super().__init__(parent);self.store=store;self.provider=provider;self.model='';self.metadata={}
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);self.combo=QComboBox();layout.addWidget(self.combo)
        self.note=QLabel('');self.note.setWordWrap(True);self.note.setStyleSheet('color:#81776E;font-size:9pt;');layout.addWidget(self.note)
    def load(self,model,models):
        self.model=model;self.metadata=next((r for r in models if r.get('id')==model),{})
        available=options(self.provider,model,self.metadata);selected=self.store.effort(self.provider,model);self.combo.clear()
        for value in available:self.combo.addItem(description(value),value)
        self.combo.setCurrentIndex(max(0,self.combo.findData(selected)))
        self.note.setText('服务商默认不发送推理参数；实际默认档位以服务商为准。'+(' 未核实此模型可调档位，仅保留默认。' if len(available)==1 else ' 档位来自模型能力信息或官方文档。')+(' 原档位已不可用，本次恢复默认。' if selected not in available else ''))
    def save(self):self.store.save_effort(self.provider,self.model,self.combo.currentData() or 'default',self.metadata)
