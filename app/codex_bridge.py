"""Official stdio app-server adapter. Credentials remain managed by Codex."""
import json,os,queue,shutil,subprocess,threading,time,tempfile,base64
from pathlib import Path
from ai_service import INSTRUCTIONS,normal_usage

def executable():
    path=shutil.which('codex')
    if path:return path
    import sys
    if sys.platform=='darwin':
        for candidate in ('/Applications/Codex.app/Contents/Resources/codex','/opt/homebrew/bin/codex','/usr/local/bin/codex'):
            if Path(candidate).is_file() and os.access(candidate,os.X_OK):return candidate
    roots=Path(os.environ.get('LOCALAPPDATA',''))/'OpenAI/Codex/bin'
    found=sorted(roots.glob('*/codex.exe'),key=lambda p:p.stat().st_mtime,reverse=True)
    if found:return str(found[0])
    raise ValueError('未检测到Codex组件，请安装官方Codex CLI并重新打开软件。')

class RPC:
    def __init__(self):
        self.events=queue.Queue();self.serial=0
        # A separate home avoids altering desktop Codex settings or conversations.
        from data_location import active_data_directory
        home=active_data_directory()/'codex_connection';home.mkdir(parents=True,exist_ok=True)
        env=dict(os.environ,CODEX_HOME=str(home))
        for name in ('OPENAI_API_KEY','CODEX_API_KEY','CODEX_ACCESS_TOKEN'):env.pop(name,None)
        self.process=subprocess.Popen([executable(),'app-server'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,encoding='utf8',env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        threading.Thread(target=self.read,daemon=True).start()
        self.request('initialize',dict(clientInfo=dict(name='biomed_reader',title='Biomed Reader',version='2.2'),capabilities={}))
        self.send(dict(method='initialized'))
    def read(self):
        try:
            for line in self.process.stdout:
                try:self.events.put(json.loads(line))
                except ValueError:continue
        finally:self.events.put(dict(error=dict(message='Codex进程已结束。')))
    def send(self,value):self.process.stdin.write(json.dumps(value,ensure_ascii=False)+'\n');self.process.stdin.flush()
    def next(self,timeout=600):
        try:return self.events.get(timeout=timeout)
        except queue.Empty:raise ValueError('Codex等待超时，未自动重复提交任务。') from None
    def request(self,method,params):
        self.serial+=1;identifier=self.serial;self.send(dict(id=identifier,method=method,params=params))
        while True:
            event=self.next()
            if event.get('id')==identifier:
                if event.get('error'):
                    error=event['error'];detail=str(error.get('message') or '未知协议错误')[:600]
                    raise ValueError('Codex '+method+' 请求失败（'+str(error.get('code',''))+'）：'+detail)
                return event.get('result') or {}
            if event.get('error'):raise ValueError('Codex组件不可用，请检查安装与登录。')
            if 'id' in event:self.send(dict(id=event['id'],error=dict(code=-32601,message='Interactive tool requests are disabled in Biomed Reader.')))
    def close(self):
        self.process.terminate()
        try:self.process.wait(5)
        except subprocess.TimeoutExpired:self.process.kill();self.process.wait()

class CodexClient:
    def __init__(self,provider='codex',key=None):self.rpc=None
    def __enter__(self):self.rpc=RPC();return self
    def __exit__(self,*args):
        if self.rpc:self.rpc.close()
    def models(self):
        if not self.rpc.request('account/read',{}).get('account'):raise ValueError('尚未登录，请点击“使用 ChatGPT 登录”。')
        rows=[];cursor=None;seen=set()
        while True:
            page=self.rpc.request('model/list',dict(cursor=cursor) if cursor else {});rows.extend(page.get('data',[]));cursor=page.get('nextCursor')
            if not cursor or cursor in seen:break
            seen.add(cursor)
        return [dict(id=r.get('model') or r['id'],name=r.get('displayName') or r.get('model') or r['id'],supportedReasoningEfforts=r.get('supportedReasoningEfforts') or [],defaultReasoningEffort=r.get('defaultReasoningEffort')) for r in rows if not r.get('hidden')]
    def balance(self):return None
    def login(self):
        import webbrowser
        result=self.rpc.request('account/login/start',dict(type='chatgpt'))
        url=result.get('authUrl')
        if not url or not url.startswith('https://auth.openai.com/'):raise ValueError('Codex未返回可识别的官方登录地址。')
        webbrowser.open(url)
        while True:
            event=self.rpc.next()
            if event.get('method')=='account/login/completed':
                if not event.get('params',{}).get('success'):raise ValueError('Codex登录未完成。')
                return self.models()
    def complete(self,model,instructions,text,max_output_tokens=None,reference=None):
        if reference is None:return self.turn(model,instructions,[dict(type='text',text=text)])
        with tempfile.TemporaryDirectory(prefix='biomed-codex-reference-') as folder:
            items=[dict(type='text',text=text)]
            for index,value in enumerate(reference['images']):
                path=Path(folder)/f'Model20250528-page-{index+1}.png';path.write_bytes(base64.b64decode(value.split(',',1)[1],validate=True));items.append(dict(type='localImage',path=str(path)))
            return self.turn(model,instructions,items)
    def recommend(self,model,topic,max_output_tokens=None):return self.complete(model,INSTRUCTIONS,topic)
    def describe_image(self,model,instructions,data_url):
        with tempfile.TemporaryDirectory(prefix='biomed-codex-image-') as folder:
            path=Path(folder)/'image.jpg';path.write_bytes(base64.b64decode(data_url.split(',',1)[1],validate=True))
            return self.turn(model,instructions,[dict(type='text',text='Read the research image.'),dict(type='localImage',path=str(path))])
    def generate_pdf(self,model,instructions,text,reference):
        from model_pdf import MAX_PDF_BYTES
        from model_artifact import FIGURE_DELIVERY,choose_file
        with tempfile.TemporaryDirectory(prefix='biomed-codex-pdf-') as folder:
            workspace=Path(folder).resolve();items=[]
            for index,value in enumerate(reference.get('images') or []):
                path=workspace/f'reference-page-{index+1}.png';path.write_bytes(base64.b64decode(value.split(',',1)[1],validate=True));items.append(dict(type='localImage',path=str(path)))
            from model_reference import reference_pdf
            (workspace/'Model20250528.pdf').write_bytes(reference_pdf())
            items.insert(0,dict(type='text',text='必须使用内置 image_gen.imagegen 图像工具，不能用代码画图、不能返回文字结构图。若工具不可用，回复IMAGE_TOOL_UNAVAILABLE并停止。参考页面图已提供。生成当前论文的模型图，优先保存原始图片；无需自行转PDF，客户端将保留像素按300 dpi物理尺寸封装。\n'+instructions+'\n'+text))
            self.image_workspace=workspace;self.generated_images=[]
            self.pdf_started=True;_,usage,incomplete=self.turn(model,instructions,items,workspace=workspace);self.pdf_usage=usage
            if self.generated_images:
                from image_routes import pdf_from_pixels
                return pdf_from_pixels(self.generated_images[-1]),usage,incomplete
            from image_routes import ImageUnavailable
            raise ImageUnavailable('当前Codex会话未返回图像工具结果，已跳过模型图。请更新CLI并检查ChatGPT账户图像权限。')
    def turn(self,model,instructions,items,workspace=None):
        options=dict(model=model,developerInstructions=instructions,approvalPolicy='never',sandbox='workspace-write' if workspace else 'read-only',ephemeral=True)
        if workspace:options.update(cwd=str(workspace),config={'features.image_generation':True})
        thread=self.rpc.request('thread/start',options)['thread']['id']
        params=dict(threadId=thread,input=items);effort=getattr(self,'reasoning_effort','default')
        if workspace:params.update(cwd=str(workspace),sandboxPolicy=dict(type='workspaceWrite',writableRoots=[str(workspace)],networkAccess=False))
        if effort!='default':params['effort']=effort
        turn=self.rpc.request('turn/start',params);parts=[];final=[];usage={}
        while True:
            event=self.rpc.next();method=event.get('method');params=event.get('params') or {}
            if 'id' in event:self.rpc.send(dict(id=event['id'],error=dict(code=-32601,message='Tool approval unavailable.')));continue
            if method=='item/agentMessage/delta':parts.append(params.get('delta',''))
            if method=='item/completed':
                item=params.get('item') or {}
                if item.get('type')=='agentMessage':final.append(item.get('text',''))
                if workspace and item.get('type')=='imageGeneration' and item.get('status')=='completed':
                    self.accept_image(item,workspace)
            if method=='thread/tokenUsage/updated':usage=(params.get('tokenUsage') or {}).get('last') or {}
            if method=='turn/completed':
                if (params.get('turn') or {}).get('status')!='completed':raise ValueError('Codex任务失败或被中断；未自动重试。')
                text='\n'.join(final) or ''.join(parts)
                if not text.strip() and not (workspace and getattr(self,'generated_images',[])):raise ValueError('Codex未返回最终回答。')
                return text,normal_usage(dict(input_tokens=usage.get('inputTokens'),output_tokens=usage.get('outputTokens'),total_tokens=usage.get('totalTokens'))),False

    def accept_image(self,item,workspace):
        encoded=item.get('result')
        if encoded:
            if len(encoded)>34*1024*1024:raise ValueError('Codex模型图超过大小限制。')
            self.generated_images.append(base64.b64decode(encoded,validate=True));return
        path=item.get('savedPath')
        if path:
            target=Path(path)
            # Only accept a file referenced by this completed tool event, within
            # this task directory or the private Codex generated_images store.
            from data_location import active_data_directory
            roots=[Path(workspace).resolve(),(active_data_directory()/'codex_connection/generated_images').resolve()]
            if target.is_symlink() or not target.is_file() or not any(target.resolve().is_relative_to(r) for r in roots):raise ValueError('Codex图像输出路径不可验证。')
            if target.stat().st_size>24*1024*1024:raise ValueError('Codex模型图超过大小限制。')
            self.generated_images.append(target.read_bytes())

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame,QVBoxLayout,QHBoxLayout,QPushButton,QComboBox,QMessageBox,QDialog,QFormLayout,QToolButton,QSizePolicy,QWidget
from ai_ui import plain_label

class CodexSettingsDialog(QDialog):
    def __init__(self,manager,parent=None):
        super().__init__(parent);self.manager=manager;self.setWindowTitle('ChatGPT Codex · 模型设置');self.resize(630,330)
        config=manager.store.config('codex');self.rows=json.loads(config['models_json']);layout=QVBoxLayout(self)
        layout.addWidget(plain_label('使用本机Codex返回的模型与推理档位。需更新列表时，先关闭此窗口并点击“检测 / 刷新模型”。',True))
        form=QFormLayout();self.model=QComboBox()
        for row in self.rows:self.model.addItem(row['name'],row['id'])
        self.model.setCurrentIndex(max(0,self.model.findData(config['model'])))
        from reasoning import ReasoningPicker
        self.reasoning=ReasoningPicker(manager.store,'codex',self);form.addRow('选择模型',self.model);form.addRow('推理强度',self.reasoning);layout.addLayout(form)
        image_model=QComboBox();image_model.addItem('gpt-image-2 · 本机工具管理（以CLI版本为准）');form.addRow('图像工具模型',image_model)
        image_effort=QComboBox();image_effort.addItem('服务商默认（图像工具不提供推理强度）');form.addRow('图像推理强度',image_effort)
        layout.addWidget(plain_label('Codex图像工具的模型由官方CLI固定管理，不能把gpt-image作为文本模型提交。上方推理强度适用于负责分析和调用工具的文本模型。',True))
        from settings_wheel import ClickOnlyComboGuard
        self.wheel_guards=[ClickOnlyComboGuard(self.model),ClickOnlyComboGuard(self.reasoning.combo)]
        self.model.currentIndexChanged.connect(self.update_reasoning);self.update_reasoning();layout.addStretch()
        buttons=QHBoxLayout();save=QPushButton('保存设置');save.clicked.connect(self.save);buttons.addWidget(save);close=QPushButton('关闭');close.clicked.connect(self.reject);buttons.addWidget(close);layout.addLayout(buttons)
    def update_reasoning(self):self.reasoning.load(self.model.currentData() or '',self.rows)
    def save(self):
        try:
            self.manager.store.save('codex','codex-local-session',self.model.currentData(),self.rows);self.reasoning.save();self.manager.changed.emit();self.accept()
        except ValueError as error:QMessageBox.warning(self,'无法保存',str(error))

class CodexCard(QFrame):
    def __init__(self,manager):
        super().__init__();self.manager=manager;self.task=None;self.setObjectName('AIModelCard');layout=QVBoxLayout(self)
        header=QHBoxLayout();self.heading=QToolButton();self.heading.setText('ChatGPT Codex · 本机连接');self.heading.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed);self.heading.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon);self.heading.setArrowType(Qt.ArrowType.RightArrow);self.heading.setCheckable(True);self.heading.toggled.connect(self.expand);header.addWidget(self.heading,1)
        self.settings_button=QPushButton('设置');self.settings_button.setSizePolicy(QSizePolicy.Policy.Fixed,QSizePolicy.Policy.Fixed);self.settings_button.setProperty('secondary',True);self.settings_button.clicked.connect(self.settings);header.addWidget(self.settings_button);layout.addLayout(header)
        self.model_name=plain_label();layout.addWidget(self.model_name);self.state=plain_label();layout.addWidget(self.state)
        toggle_row=QHBoxLayout();self.toggle_button=QPushButton('开启模型');self.toggle_button.clicked.connect(self.toggle);toggle_row.addWidget(self.toggle_button);toggle_row.addStretch();layout.addLayout(toggle_row)
        self.details=QWidget();details=QVBoxLayout(self.details);details.setContentsMargins(0,10,0,0)
        self.status=plain_label('官方本机App Server；使用个人ChatGPT登录，不参与课题组Key共享。',True);details.addWidget(self.status);self.models=QComboBox();details.addWidget(self.models);row=QHBoxLayout()
        for label,fn in [('检测 / 刷新模型',self.refresh),('使用 ChatGPT 登录',self.login)]:
            button=QPushButton(label);button.clicked.connect(fn);button.setProperty('secondary',True);row.addWidget(button)
        row.addStretch();details.addLayout(row);layout.addWidget(self.details);self.details.hide()
        from settings_wheel import ClickOnlyComboGuard
        self.wheel_guard=ClickOnlyComboGuard(self.models)
        self.reload();manager.changed.connect(self.reload)
    def reload(self):
        config=self.manager.store.config('codex');enabled=bool(config['enabled']);self.setStyleSheet('QFrame#AIModelCard {background:'+('#FFF1D6' if enabled else '#FFFFFF')+';border:2px solid '+('#F59E0B' if enabled else '#E8D5C4')+';border-radius:14px;padding:12px;}');self.models.clear()
        self.heading.setStyleSheet('font-size:16pt;font-weight:700;color:'+('#B45309' if enabled else '#30251D')+';background:transparent;border:none;')
        self.model_name.setText('当前模型：'+(config['model'] or '尚未设置')+' · 推理强度：'+__import__('reasoning').description(config.get('reasoning_effort')))
        self.state.setText('● 已开启 · 可用于 AI 功能' if enabled else '○ 未开启')
        for row in json.loads(config['models_json']):self.models.addItem(row['name'],row['id'])
        self.models.setCurrentIndex(max(0,self.models.findData(config['model'])))
        self.toggle_button.setText('关闭模型' if config['enabled'] else '开启模型')
        self.toggle_button.setToolTip('当前推理强度：'+__import__('reasoning').description(config.get('reasoning_effort')))
    def settings(self):CodexSettingsDialog(self.manager,self).exec()
    def expand(self,expanded):self.details.setVisible(expanded);self.heading.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
    def start(self,login=False):
        if self.task:return
        self.status.setText('请在官方浏览器页面完成授权；等待本机Codex返回……' if login else '正在检测Codex组件与模型列表……')
        def work():
            with CodexClient() as client:return client.login() if login else client.models()
        self.task=self.manager.submit(self,work,'loaded','failed')
    def refresh(self):self.start()
    def login(self):self.start(True)
    def loaded(self,rows):
        self.task=None
        if not rows:self.failed('未取得可用模型，请先登录。');return
        selected=self.manager.store.config('codex')['model'];selected=selected if any(r['id']==selected for r in rows) else rows[0]['id']
        self.manager.store.save('codex','codex-local-session',selected,rows);self.status.setText('已连接。可在设置中选择模型与推理强度，再点击开启；实际权限以任务执行为准。');self.manager.changed.emit()
    def failed(self,message):self.task=None;self.status.setText(message);self.heading.setChecked(True)
    def toggle(self):
        if not self.models.currentData():self.failed('请先登录并刷新模型。');return
        store=self.manager.store;config=store.config('codex');store.save('codex','codex-local-session',self.models.currentData(),json.loads(config['models_json']));store.toggle('codex');self.manager.changed.emit()
