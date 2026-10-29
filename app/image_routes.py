"""Explicit image-only routes, independent of text/lab credentials."""
import base64,io,json
from PIL import Image
from ai_store import encrypt_key,decrypt_key
from software_update import session_for_updates
ROUTES={'ark':'火山引擎方舟 · Ark','bailian':'阿里云百炼','glm':'GLM-Image'}
MODELS={'ark':['doubao-seedream-5-0-260128','doubao-seedream-4-5-251128','doubao-seedream-4-0-250828'],'qwen':['qwen-image-2.0-pro','qwen-image-2.0-pro-2026-06-22','qwen-image-2.0','qwen-image-max','qwen-image-plus'],'wan':['wan2.7-image-pro','wan2.7-image'],'zimage':['z-image-turbo'],'glm':['glm-image']}
ENDPOINTS={'ark':'https://ark.cn-beijing.volces.com/api/v3/images/generations','glm':'https://open.bigmodel.cn/api/paas/v4/images/generations'}
MODELS['bailian']=MODELS.pop('qwen')+MODELS.pop('wan')+MODELS.pop('zimage')
ALI=('bailian',)

def accepts_reference(model):
    return model.startswith(('qwen-image-2.','wan2.7-'))

def ali_base(region):
    if region not in ('cn','sg'):raise ValueError('请选择百炼Key对应的北京或新加坡区域。')
    return 'https://'+('dashscope.aliyuncs.com' if region=='cn' else 'dashscope-intl.aliyuncs.com')
def image_payload(provider,model,prompt,images=None):
    images=images or []
    if provider=='ark':return dict(model=model,prompt=prompt,size='2K',response_format='b64_json',watermark=True,**(dict(image=images) if images else {}))
    if provider=='glm':return dict(model=model,prompt=prompt,size='1568x1056')
    content=([dict(image=v) for v in images] if accepts_reference(model) else [])+[dict(text=prompt)]
    return dict(model=model,input=dict(messages=[dict(role='user',content=content)]),parameters=dict(n=1,size='1536*1024',watermark=True))
def result_item(data):
    if data.get('data'):return data['data'][0]
    for choice in (data.get('output') or {}).get('choices',[]):
        for part in (choice.get('message') or {}).get('content',[]):
            if part.get('image'):return dict(url=part['image'])
    raise ValueError('图像服务没有返回可用图片。')
def discover(provider,key,region='cn',session=None):
    own=session is None;session=session or session_for_updates()
    try:
        from ai_service import Bearer
        session.auth=Bearer(key)
        url=('https://ark.cn-beijing.volces.com/api/v3/models' if provider=='ark' else ali_base(region)+'/compatible-mode/v1/models')
        response=session.get(url,timeout=(10,30),allow_redirects=False)
        try:
            if response.status_code!=200:raise ValueError('模型目录读取失败（HTTP '+str(response.status_code)+'），保留官方预置目录；可输入控制台模型或推理接入点ID。')
            rows=[r['id'] for r in response.json().get('data',[]) if isinstance(r,dict) and isinstance(r.get('id'),str)]
            if provider=='ark':rows=[r['id'] for r in response.json().get('data',[]) if isinstance(r,dict) and isinstance(r.get('id'),str) and ('seedream' in r['id'].lower() or 'image' in (r.get('output_modalities') or []) or r['id'].startswith('ep-'))]
            else:rows=[v for v in rows if (v.startswith(('qwen-image','z-image')) or (v.startswith('wan') and 'image' in v))]
            if not rows:raise ValueError('接口未返回该系列图像模型，保留预置目录，实际权限以控制台为准。')
            return sorted(set(rows))
        finally:response.close()
    finally:
        if own:session.close()
class ImageUnavailable(ValueError):pass

def config(db,provider):
    from ai_store import migrate_bailian_settings
    migrate_bailian_settings(db)
    settings=db.get_settings();prefix='image_'+provider+'_'
    from ai_store import AIStore
    lab=AIStore(db).lab_state()
    if lab and lab['active']:
        prefix='image_lab_'+lab['identity']['user_id']+'_'+provider+'_'
        return dict(model=settings.get(prefix+'model',MODELS[provider][0]),enabled=settings.get(prefix+'enabled','1' if (provider in lab['identity'].get('providers',[]) or (provider=='bailian' and set(lab['identity'].get('providers',[]))&{'qwen','wan','zimage'})) else '0')=='1',key=lab['encrypted_token'],region=settings.get(prefix+'region','cn'),route='lab',prefix=prefix,gateway=lab['identity'].get('gateway_url'))
    key=settings.get(prefix+'key','')
    if provider=='glm' and not key:
        with db.connect() as c:
            row=c.execute("SELECT encrypted_key FROM ai_provider_configs WHERE provider='glm'").fetchone()
        key=row[0] if row else ''
    return dict(model=settings.get(prefix+'model',MODELS.get(provider,['server-managed'])[0]),enabled=settings.get(prefix+'enabled','0')=='1',key=key,region=settings.get(prefix+'region','cn'),route='personal',prefix=prefix)

def pdf_from_pixels(raw):
    # Preserve original pixels; define the PDF page size at 300 pixels/inch.
    # No upsampling and no claim that the model produced vectors.
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    if len(raw)>24*1024*1024:raise ValueError('模型图文件超过24 MB。')
    with Image.open(io.BytesIO(raw)) as im:
        if im.width*im.height>32_000_000 or min(im.size)<512:raise ValueError('模型图像素尺寸不合适。')
        im.load();width,height=im.size
    result=io.BytesIO();c=canvas.Canvas(result,pagesize=(width*72/300,height*72/300));c.drawImage(ImageReader(io.BytesIO(raw)),0,0,width=width*72/300,height=height*72/300);c.showPage();c.save();return result.getvalue()

def generate(db,provider,prompt,reference,cancelled=lambda:False,session=None):
    cfg=config(db,provider)
    if not cfg['enabled'] or not cfg['key']:raise ImageUnavailable('所选绘图通道尚未配置并开启，已跳过模型图。')
    if provider=='glm' and len(prompt)>1000:raise ValueError('GLM-Image提示超过官方1000字符限制，未发送请求。')
    if cancelled():raise InterruptedError('已取消绘图。')
    if cfg.get('route')=='lab':
        from lab_service import LabAPI
        payload=dict(model=cfg['model'],prompt=prompt,region=cfg['region'])
        if provider!='glm' and (provider not in ALI or accepts_reference(cfg['model'])) and reference.get('images'):payload['image']=reference['images']
        data=LabAPI(decrypt_key(cfg['key']),cfg.get('gateway')).image(provider,payload)
        item=result_item(data)
        if item.get('b64_json'):raw=base64.b64decode(item['b64_json'],validate=True)
        elif item.get('url'):
            from ai_content import download
            raw=download(item['url'])[0]
        else:raise ValueError('课题组绘图接口没有返回图片。')
        return pdf_from_pixels(raw)
    own=session is None;session=session or session_for_updates()
    try:
        from ai_service import Bearer
        session.auth=Bearer(decrypt_key(cfg['key']));headers={}
        images=reference.get('images') or []
        payload=image_payload(provider,cfg['model'],prompt,images)
        url=ali_base(cfg['region'])+'/api/v1/services/aigc/multimodal-generation/generation' if provider in ALI else ENDPOINTS[provider]
        response=session.post(url,headers=headers,json=payload,timeout=(15,300),allow_redirects=False)
        try:
            if response.status_code!=200:raise ValueError('绘图接口返回HTTP '+str(response.status_code)+'；未自动重试或切换通道，请核对权限、额度与模型名称。')
            data=response.json();item=result_item(data)
            if item.get('b64_json'):raw=base64.b64decode(item['b64_json'],validate=True)
            elif item.get('url'):
                # Independent download client: never send the API Key to image/CDN URLs.
                from ai_content import download
                raw=download(item['url'])[0]
            else:raise ValueError('图像服务未返回图片。')
        finally:response.close()
        if cancelled():raise InterruptedError('已取消绘图。')
        result=pdf_from_pixels(raw)
        with db.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS ai_image_calls(provider TEXT,model TEXT,created_at TEXT)')
            c.execute("INSERT INTO ai_image_calls VALUES(?,?,datetime('now'))",(provider,cfg['model']))
        return result
    finally:
        if own:session.close()

def selected_route(engine):
    route=engine.store.database.get_settings().get('ai_image_route','native')
    if route in ('stability','byteplus','biomni'):return 'off'
    if route in ('qwen','wan','zimage'):return 'bailian'
    if route!='native':return route
    if engine.provider=='glm':return 'glm'
    if engine.provider in ('codex','openai'):return engine.provider
    return None

def draw(engine,text,reference,progress):
    from model_artifact import validate_figure
    route=selected_route(engine)
    if route is None or route=='off':raise ImageUnavailable('当前选择不支持图像生成，已跳过模型图。')
    if route in ROUTES:
        cfg=config(engine.store.database,route)
        if not cfg['enabled'] or not cfg['key']:raise ImageUnavailable('所选绘图通道未开启，已跳过模型图。')
        limit='不超过900个字符' if route=='glm' else '详细但避免重复'
        progress('正在由当前分析模型准备绘图提示……')
        prompt=engine.call('根据当前论文分析，编写供图像模型使用的绘图提示，'+limit+'。描述有证据支持的主要机制、实体、空间关系和关键结论，不添加未知事实。模型图不能有太多文字，要美观且符合杂志发表要求，并且易于读者理解。只返回最终提示文本。',text)
        if route!='glm' and (route not in ALI or accepts_reference(cfg['model'])) and not reference.get('images'):
            from model_reference import attachment
            reference=attachment('codex')
        raw=generate(engine.store.database,route,prompt,reference,engine.cancelled)
        return validate_figure(raw),dict(provider=route,model=cfg['model'],image_prompt=prompt,reference_mode='reference-page' if route!='glm' and (route not in ALI or accepts_reference(cfg['model'])) else 'text-only',dpi=300)
    return engine.draw_pdf(text,reference),dict(provider=engine.provider,model=engine.model)

from PySide6.QtWidgets import QDialog,QVBoxLayout,QFormLayout,QLineEdit,QComboBox,QPushButton,QLabel,QFrame,QHBoxLayout,QMessageBox,QCheckBox
from PySide6.QtCore import Signal
class ImageSettingsDialog(QDialog):
    models_ready=Signal(object)
    def __init__(self,db,provider,parent=None):
        super().__init__(parent);self.db=db;self.provider=provider;self.setWindowTitle(ROUTES[provider]+' · 绘图设置');self.resize(640,380);root=QVBoxLayout(self);form=QFormLayout();cfg=config(db,provider);self.cfg=cfg
        note=QLabel('绘图API按图片计费；课题组通道由PI承担费用，个人通道使用自己的Key。模型目录为官方预置项，权限以实际请求为准。\nArk使用国内火山方舟Key；阿里云平台请选择Key对应的北京／新加坡区域。Z-Image-Turbo与GLM-Image仅支持文字，其它通道发送参考PDF的页面图。');note.setWordWrap(True);root.addWidget(note)
        self.enabled=QCheckBox('开启此绘图通道');self.enabled.setChecked(cfg['enabled']);root.addWidget(self.enabled)
        self.key=QLineEdit();self.key.setEchoMode(QLineEdit.EchoMode.Password);self.key.setPlaceholderText('已保存，留空保留' if cfg['key'] else '输入此平台的API Key');form.addRow('API Key',self.key) if cfg.get('route')!='lab' else self.key.hide()
        self.model=QComboBox();self.model.setEditable(True);self.model.addItems(MODELS[provider]);self.model.setCurrentText(cfg['model']);form.addRow('图像模型 / endpoint ID',self.model)
        self.region=QComboBox();self.region.addItem('华北2（北京）','cn');self.region.addItem('新加坡','sg');self.region.setCurrentIndex(max(0,self.region.findData(cfg['region'])));form.addRow('百炼API区域',self.region) if provider in ALI else self.region.hide()
        self.refresh_button=QPushButton('联网刷新图像模型目录');self.refresh_button.clicked.connect(self.refresh_models);form.addRow(self.refresh_button) if provider!='glm' else self.refresh_button.hide()
        self.models_ready.connect(self.models_loaded);self.loading=False
        effort=QComboBox();effort.addItem('服务商默认（接口无推理强度参数）');form.addRow('推理强度',effort);root.addLayout(form)
        hint=QLabel('保留原生像素，按300 dpi物理尺寸嵌入PDF。生成失败时保留文字，不自动重试或调用其它平台。');hint.setWordWrap(True);root.addWidget(hint)
        from settings_wheel import ClickOnlyComboGuard
        self.guards=[ClickOnlyComboGuard(self.model),ClickOnlyComboGuard(effort),ClickOnlyComboGuard(self.region)]
        save=QPushButton('保存设置');save.clicked.connect(self.save);root.addWidget(save)
    def save(self):
        prefix=self.cfg['prefix'];values={prefix+'model':self.model.currentText().strip(),prefix+'enabled':'1' if self.enabled.isChecked() else '0',prefix+'region':self.region.currentData()}
        if not values[prefix+'model']:return
        try:
            if self.cfg.get('route')!='lab' and self.key.text().strip():values[prefix+'key']=encrypt_key(self.key.text())
            self.db.save_settings(values);self.accept()
        except ValueError as error:QMessageBox.warning(self,'设置未保存',str(error))
    def refresh_models(self):
        if self.loading:return
        cfg=self.cfg;region=self.region.currentData();key=self.key.text().strip()
        try:key=key or decrypt_key(cfg['key'])
        except ValueError as e:QMessageBox.information(self,'请先配置Key',str(e));return
        self.loading=True;self.refresh_button.setEnabled(False)
        import threading
        def work():
            try:
                if cfg.get('route')=='lab':
                    from lab_service import LabAPI
                    data=LabAPI(key,cfg.get('gateway')).request('GET','/models?provider='+self.provider+'&region='+region);rows=[r['id'] for r in data['data']]
                else:rows=discover(self.provider,key,region)
                value=(rows,'')
            except Exception as e:value=([],str(e))
            try:self.models_ready.emit(value)
            except RuntimeError:pass
        threading.Thread(target=work,daemon=True).start()
    def models_loaded(self,value):
        self.loading=False;self.refresh_button.setEnabled(True);rows,error=value
        if error:QMessageBox.information(self,'保留现有模型目录',error);return
        selected=self.model.currentText();self.model.clear();self.model.addItems(rows);self.model.setCurrentText(selected)
    def reject(self):
        if not self.loading:super().reject()
class ImageModelCard(QFrame):
    def __init__(self,manager,provider):
        super().__init__();self.setObjectName('Card');self.manager=manager;self.provider=provider;box=QVBoxLayout(self);title=QLabel(ROUTES[provider]);title.setStyleSheet('font-size:16pt;font-weight:700;color:#A45D23;');box.addWidget(title);self.state=QLabel();box.addWidget(self.state);row=QHBoxLayout();settings=QPushButton('设置');settings.setProperty('secondary',True);settings.clicked.connect(self.settings);row.addWidget(settings);self.toggle=QPushButton();self.toggle.clicked.connect(self.switch);row.addWidget(self.toggle);row.addStretch();box.addLayout(row);manager.changed.connect(self.refresh);self.refresh()
        if provider=='bailian':
            bill=QPushButton('打开用户用量/账单');bill.setProperty('secondary',True);bill.clicked.connect(self.open_billing);row.insertWidget(2,bill)
            note=QLabel('余额请在官方控制台查看；百炼 API Key 不等同于费用中心 AccessKey。课题组学生请由 PI 核对账单。');note.setWordWrap(True);box.addWidget(note)
    def open_billing(self):
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl('https://bailian.console.aliyun.com/'))
    def refresh(self):
        cfg=config(self.manager.store.database,self.provider);self.state.setText(cfg['model']+' · '+('已开启 · 仅用于模型图' if cfg['enabled'] else '未开启 · 仅用于模型图'));self.toggle.setText('关闭模型' if cfg['enabled'] else '开启模型')
    def settings(self):ImageSettingsDialog(self.manager.store.database,self.provider,self).exec();self.refresh()
    def switch(self):
        db=self.manager.store.database;cfg=config(db,self.provider)
        if not cfg['key']:self.settings();return
        db.save_settings({cfg['prefix']+'enabled':'0' if cfg['enabled'] else '1'});self.refresh()
