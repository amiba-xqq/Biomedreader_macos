"""Read the official site manifest and download only verified GitHub release assets."""
import hashlib,json,os,platform,re,shutil,subprocess,tempfile,threading
from pathlib import Path
from urllib.parse import urlparse,urljoin
import requests
from PySide6.QtCore import QObject,QRunnable,Signal,Slot,QThreadPool,QStandardPaths,Qt
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QPushButton,QLabel,QProgressBar,QMessageBox
from path_dialog import QFileDialog
from network_transport import VerifiedHTTPSAdapter,refresh_error_message
from version import APP_VERSION

SITE_URL='https://amiba-xqq.github.io/Biomedreader/'
MANIFEST_URL=SITE_URL+'app-update.json'
ASSET_HOSTS={'github.com','release-assets.githubusercontent.com','objects.githubusercontent.com','github-releases.githubusercontent.com'}

class UpdateCancelled(Exception):pass

class PublicRequest(requests.auth.AuthBase):
    def __call__(self,request):
        request.headers.pop('Authorization',None)
        return request

def session_for_updates():
    session=requests.Session();session.mount('https://',VerifiedHTTPSAdapter())
    session.auth=PublicRequest();session.headers['User-Agent']='BiomedReader/'+APP_VERSION
    return session

def version_numbers(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d+(?:\.\d+){0,3}',value):raise ValueError('官网版本号无效。')
    numbers=tuple(int(part) for part in value.split('.'))
    return numbers+(0,)*(4-len(numbers))

def platform_package(system=None,machine=None):
    system=system or platform.system();machine=(machine or platform.machine()).lower()
    if system=='Windows':return 'windows-x64'
    if system=='Darwin':
        if machine in ('arm64','aarch64'):return 'macos-arm64'
        # A Python process under Rosetta reports x86_64 on an Apple Silicon Mac.
        if platform.system()=='Darwin':
            try:
                if subprocess.check_output(['/usr/sbin/sysctl','-n','hw.optional.arm64'],timeout=3,text=True).strip()=='1':return 'macos-arm64'
            except (OSError,subprocess.SubprocessError):pass
        if machine in ('x86_64','amd64'):return 'macos-intel'
    raise ValueError('目前仅提供 Windows x64 和 macOS Intel / Apple Silicon 安装包。')

def validate_artifact(artifact,version,key):
    if not isinstance(artifact,dict):raise ValueError('官网没有适合本机的安装包。')
    filename=artifact.get('filename');digest=artifact.get('sha256');size=artifact.get('bytes');url=artifact.get('url')
    if not isinstance(filename,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+',filename) or '..' in filename:raise ValueError('安装包文件名无效。')
    repo='Biomedreader' if key=='windows-x64' else 'Biomedreader_macos'
    expected=f'https://github.com/amiba-xqq/{repo}/releases/download/v{version}/{filename}'
    if url!=expected:raise ValueError('安装包地址不是指定的官方 GitHub Release。')
    suffix='.exe' if key=='windows-x64' else '.dmg'
    if not filename.endswith(suffix) or not filename.startswith('BiomedReader_'):raise ValueError('安装包类型与系统不符。')
    correct=(filename==f'BiomedReader_Setup_{version}_x64.exe') if key=='windows-x64' else bool(re.fullmatch(
        r'BiomedReader_'+re.escape(version)+r'_macOS\d+_'+('AppleSilicon' if key=='macos-arm64' else 'Intel')+r'\.dmg',filename))
    if not correct:raise ValueError('安装包文件名与版本或处理器架构不符。')
    if not isinstance(digest,str) or not re.fullmatch(r'[a-f0-9]{64}',digest):raise ValueError('官网安装包 SHA256 无效。')
    if type(size) is not int or not 0<size<=4*1024**3:raise ValueError('官网安装包大小无效。')
    return dict(artifact)

def check_release(current=APP_VERSION,system=None,machine=None,session=None):
    own=session is None;session=session or session_for_updates()
    try:
        response=session.get(MANIFEST_URL,timeout=(10,25),stream=True,allow_redirects=False,headers={'Cache-Control':'no-cache'})
        try:
            response.raise_for_status()
            if response.status_code!=200:raise ValueError('官网更新清单暂时不可用。')
            payload=bytearray()
            for block in response.iter_content(8192):
                payload.extend(block)
                if len(payload)>128*1024:raise ValueError('官网更新清单过大。')
            data=json.loads(payload.decode('utf-8-sig'))
        finally:response.close()
        if not isinstance(data,dict) or data.get('schema')!=1 or data.get('application')!='BiomedReader':raise ValueError('官网更新清单格式无效。')
        latest=data.get('version');newer=version_numbers(latest)>version_numbers(current)
        artifact=None
        if newer:
            key=platform_package(system,machine)
            if not isinstance(data.get('packages'),dict):raise ValueError('官网安装包清单无效。')
            artifact=validate_artifact(data['packages'].get(key),latest,key)
        return dict(current=current,latest=latest,newer=newer,artifact=artifact)
    finally:
        if own:session.close()

def asset_response(session,url):
    for _ in range(6):
        parsed=urlparse(url)
        if parsed.scheme!='https' or parsed.hostname not in ASSET_HOSTS or parsed.username or parsed.password or parsed.port not in (None,443):
            raise ValueError('安装包重定向地址不是 GitHub 官方下载域名。')
        response=session.get(url,timeout=(10,30),stream=True,allow_redirects=False)
        if response.status_code in (301,302,303,307,308):
            location=response.headers.get('Location');response.close()
            if not location:raise ValueError('安装包下载重定向缺少目标地址。')
            url=urljoin(url,location);continue
        try:response.raise_for_status()
        except Exception:response.close();raise
        return response
    raise ValueError('安装包下载重定向次数过多。')

def download_artifact(artifact,destination,progress=lambda percent,message:None,cancelled=lambda:False,session=None):
    target=Path(destination).resolve()
    if not target.parent.is_dir():raise ValueError('请选择已存在的保存目录。')
    if shutil.disk_usage(target.parent).free<artifact['bytes']+20*1024**2:raise OSError('保存位置的可用空间不足。')
    own=session is None;session=session or session_for_updates();temporary=None
    try:
        if cancelled():raise UpdateCancelled()
        response=asset_response(session,artifact['url'])
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent,prefix='.BiomedReader-update-',suffix='.part',delete=False) as stream:
                temporary=Path(stream.name);digest=hashlib.sha256();count=0
                for block in response.iter_content(256*1024):
                    if cancelled():raise UpdateCancelled()
                    if not block:continue
                    count+=len(block)
                    if count>artifact['bytes']:raise ValueError('下载文件大于官网声明大小，已停止。')
                    stream.write(block);digest.update(block)
                    progress(min(99,count*100//artifact['bytes']),f"正在下载 {count/1_000_000:.1f} / {artifact['bytes']/1_000_000:.1f} MB")
        finally:response.close()
        if cancelled():raise UpdateCancelled()
        if count!=artifact['bytes'] or digest.hexdigest()!=artifact['sha256']:raise ValueError('安装包大小或 SHA256 校验失败，未保留不完整安装包。')
        os.replace(temporary,target);temporary=None;progress(100,'下载完成，SHA256 校验通过。')
        return str(target)
    finally:
        if temporary:temporary.unlink(missing_ok=True)
        if own:session.close()

class UpdateSignals(QObject):
    progress=Signal(int,str);finished=Signal(object);failed=Signal(str);cancelled=Signal()

class UpdateTask(QRunnable):
    def __init__(self,kind,artifact=None,path=None):
        super().__init__();self.kind=kind;self.artifact=artifact;self.path=path;self.stop=threading.Event();self.signals=UpdateSignals()
    def run(self):
        try:
            if self.kind=='check':
                result=check_release()
                if self.stop.is_set():raise UpdateCancelled()
            else:result=download_artifact(self.artifact,self.path,self.signals.progress.emit,self.stop.is_set)
            self.signals.finished.emit((self.kind,result))
        except UpdateCancelled:self.signals.cancelled.emit()
        except Exception as error:self.signals.failed.emit(refresh_error_message(error))

class SoftwareUpdateWidget(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent);self.task=None;layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        row=QHBoxLayout();self.check_button=QPushButton('检查软件更新');self.cancel_button=QPushButton('取消下载');self.cancel_button.hide()
        self.check_button.clicked.connect(self.check);self.cancel_button.clicked.connect(self.cancel)
        from release_notes import show_release_notes
        self.notes_button=QPushButton('展示更新内容');self.notes_button.clicked.connect(lambda:show_release_notes(self))
        row.addWidget(self.check_button);row.addWidget(self.notes_button);row.addWidget(self.cancel_button);row.addStretch();layout.addLayout(row)
        self.status=QLabel(f'当前版本 {APP_VERSION}。检查官网版本，发现新版本后下载适配本机的 GitHub 安装包。');self.status.setWordWrap(True);self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status);self.progress=QProgressBar();self.progress.hide();layout.addWidget(self.progress)
        link=QLabel(f'官网：<a href="{SITE_URL}">{SITE_URL}</a>');link.setOpenExternalLinks(True);link.setWordWrap(True);layout.addWidget(link)
    def launch(self,task):
        self.task=task;self.check_button.setEnabled(False);self.cancel_button.setEnabled(True);self.cancel_button.setVisible(task.kind=='download')
        self.progress.setRange(0,0 if task.kind=='check' else 100);self.progress.setValue(0);self.progress.show()
        task.signals.progress.connect(self.on_progress);task.signals.finished.connect(self.finished)
        task.signals.failed.connect(self.failed);task.signals.cancelled.connect(self.cancelled)
        QThreadPool.globalInstance().start(task)
    def check(self):
        if self.task:return
        self.status.setText('正在检查官网软件版本……');self.launch(UpdateTask('check'))
    def cancel(self):
        if self.task:self.task.stop.set();self.cancel_button.setEnabled(False);self.status.setText('正在取消，等待当前网络请求结束……')
    def reset(self):
        self.task=None;self.check_button.setEnabled(True);self.cancel_button.hide();self.progress.hide()
    @Slot(int,str)
    def on_progress(self,percent,message):self.progress.setValue(percent);self.status.setText(message)
    @Slot(object)
    def finished(self,result):
        kind,data=result;self.reset()
        if kind=='download':
            self.status.setText('下载完成：'+data)
            QMessageBox.information(self,'安装包下载完成',data+'\n\nSHA256 校验通过。请关闭 Biomed Reader 和 Word 后，手动运行安装包。');return
        if not data['newer']:
            text=f"当前版本 {APP_VERSION} 已是最新版本，无需更新。" if version_numbers(APP_VERSION)==version_numbers(data['latest']) else f"当前版本 {APP_VERSION} 高于官网发布版本 {data['latest']}，无需下载旧版。"
            self.status.setText(text);QMessageBox.information(self,'软件更新',text);return
        artifact=data['artifact'];self.status.setText(f"发现新版本 {data['latest']}，请选择安装包保存位置。")
        directory=QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) or str(Path.home())
        suffix=Path(artifact['filename']).suffix
        path,_=QFileDialog.getSaveFileName(self,'保存新版本安装包',str(Path(directory)/artifact['filename']),f'安装包 (*{suffix})')
        if not path:self.status.setText('已取消下载，可稍后重新检查。');return
        path=Path(path)
        if path.suffix.lower()!=suffix:path=path.with_suffix(suffix)
        self.launch(UpdateTask('download',artifact,str(path)))
    @Slot(str)
    def failed(self,message):self.reset();self.status.setText('更新未完成：'+message);QMessageBox.warning(self,'软件更新未完成',message)
    @Slot()
    def cancelled(self):self.reset();self.status.setText('已取消，未覆盖原有文件。')
