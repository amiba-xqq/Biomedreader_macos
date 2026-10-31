"""Native Mac Word template transport. No HTTP listener, COM or shell payloads."""
import json,os,subprocess,sys,time,uuid
from pathlib import Path

def pack(parts):
    # VBA Len counts UTF-16 code units, including surrogate pairs.
    return ''.join(str(len(str(p).encode('utf-16-le'))//2)+'\n'+str(p) for p in parts)

def unpack(text):
    data=text.encode('utf-16-le');parts=[]
    while data:
        split=data.find(b'\n\x00')
        if split<0 or split%2:raise ValueError('Invalid Word transport frame')
        count=int(data[:split].decode('utf-16-le'));data=data[split+2:]
        if count<0 or count*2>len(data):raise ValueError('Truncated Word transport frame')
        parts.append(data[:count*2].decode('utf-16-le'));data=data[count*2:]
    return parts

def directory():return Path.home()/'Library/Application Support/OrangeReader/WordBridge'

def request(action,values=None):
    folder=directory();folder.mkdir(parents=True,exist_ok=True)
    if sys.platform=='darwin':
        app=Path(sys.executable).resolve().parents[2]
        subprocess.run(['/usr/bin/open','-a',str(app)],check=True,capture_output=True)
    identifier=uuid.uuid4().hex;path=folder/(identifier+'.request.json');response=folder/(identifier+'.response.json')
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(dict(action=action,**(values or {})),ensure_ascii=False),encoding='utf8');temp.replace(path)
    try:
        deadline=time.monotonic()+125
        while time.monotonic()<deadline:
            if response.is_file():
                result=json.loads(response.read_text(encoding='utf8'))
                if not result.get('ok'):raise ValueError(result.get('error') or 'Biomed Reader request failed')
                return result['data']
            time.sleep(.1)
        raise ValueError('Biomed Reader未响应。请先启动软件并选中收藏文章，再重试。')
    finally:
        path.unlink(missing_ok=True);response.unlink(missing_ok=True)

def dispatch(action,parts,call=request):
    if action=='show':call('show');return ['OK']
    if action=='styles':
        root=Path(getattr(sys,'_MEIPASS',Path(__file__).parent))
        return json.loads((root/'assets/csl/word_catalogue.json').read_text(encoding='utf8'))
    if action=='selected':return [json.dumps(call('selected'),ensure_ascii=False)]
    if action=='render':
        journal=parts[0];records=[json.loads(x) for x in parts[1:]];result=call('render',dict(journal=journal,records=records))
        from word_document import endnote_citation
        output=[str(len(records))]
        for record,valid in zip(records,result['valid']):
            if not valid:output+=['0','','0','',''];continue
            index=result['keys'].index(record['article']['article_key']);article=result['articles'][index];record=dict(record,article=article)
            marker=result['markers'][index];sup='<sup>' in result['html']['markers'][index]
            output+=['1',marker,'1' if sup else '0',json.dumps(record,ensure_ascii=False),endnote_citation(article,index+1,marker,sup)]
        output.append('\n'.join(result['references']));return output
    raise ValueError('Unsupported native Word action')

def main(args):
    try:
        action,path=args;source=Path(path)
        if source.stat().st_size>4_000_000:raise ValueError('Word请求过大。')
        print(pack(['OK',*dispatch(action,unpack(source.read_text(encoding='utf-8-sig')))]),end='')
    except Exception as error:print(pack(['ERROR',str(error)]),end='')
    return 0

def validate_template(path):
    """Validate compatibility-prefix values as well as ordinary XML syntax."""
    import zipfile
    from xml.dom import minidom
    mc='http://schemas.openxmlformats.org/markup-compatibility/2006'
    def declared(node,prefix):
        if prefix=='xml':return True
        while node is not None:
            if node.nodeType==node.ELEMENT_NODE and node.hasAttribute('xmlns:'+prefix):return True
            node=node.parentNode
        return False
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:raise ValueError('Word 模板文件损坏。')
        if not archive.read('word/vbaProject.bin').startswith(bytes.fromhex('D0CF11E0A1B11AE1')):raise ValueError('Word 模板缺少 VBA 工程。')
        for name in archive.namelist():
            if not name.endswith(('.xml','.rels')):continue
            doc=minidom.parseString(archive.read(name))
            for element in doc.getElementsByTagName('*'):
                for attribute in element.attributes.values():
                    if (attribute.namespaceURI==mc and attribute.localName in ('Ignorable','MustUnderstand','ProcessContent','PreserveElements','PreserveAttributes')) or (element.namespaceURI==mc and element.localName=='Choice' and attribute.name=='Requires') or (attribute.namespaceURI=='http://www.w3.org/2001/XMLSchema-instance' and attribute.localName=='type'):
                        for token in attribute.value.split():
                            if not declared(element,token.split(':')[0]):raise ValueError('Word 模板存在未定义的兼容性前缀：'+name+' / '+token)
            doc.unlink()
    return True

def install_plugin(parent=None):
    from PySide6.QtWidgets import QMessageBox
    import shutil
    executable=Path(sys.executable).resolve()
    if 'AppTranslocation' in executable.parts or str(executable).startswith('/Volumes/'):
        QMessageBox.information(parent,'请先安装应用','请先将 Biomed Reader 拖入“应用程序”，退出磁盘镜像中的软件，再从应用程序打开并安装 Word 插件。');return
    root=Path(getattr(sys,'_MEIPASS',Path(__file__).parent))/'assets/mac_word'
    startup=Path.home()/'Library/Group Containers/UBF8T346G9.Office/User Content.localized/Startup.localized/Word'
    scripts=Path.home()/'Library/Application Scripts/com.microsoft.Word'
    if QMessageBox.question(parent,'安装 Mac Word 插件','将为当前用户安装 Biomed Reader Word 模板和通信脚本。请先退出 Word。首次加载请只信任本插件，不需要启用所有宏。\n是否继续？')!=QMessageBox.StandardButton.Yes:return
    try:
        validate_template(root/'BiomedReader.dotm')
        if subprocess.run(['/usr/bin/pgrep','-x','Microsoft Word'],capture_output=True).returncode==0:
            QMessageBox.information(parent,'请先退出 Word','请使用 Word → 退出 Word 后，再安装或修复插件。');return
        startup.mkdir(parents=True,exist_ok=True);scripts.mkdir(parents=True,exist_ok=True);directory().mkdir(parents=True,exist_ok=True)
        compiled=directory()/'BiomedReader.scpt'
        compile_bridge(root/'BiomedReader.applescript',compiled,executable,Path.home())
        probe=run_bridge(compiled,'ping',pack(['']))
        if probe!=['OK','PONG']:raise ValueError('Word 通信脚本自检未通过。')
        probe=run_bridge(compiled,'styles',pack(['']))
        if probe[:1]!=['OK']:raise ValueError('无法通过脚本调用本机应用：'+' | '.join(probe[1:]))
        for source,target in [(root/'BiomedReader.dotm',startup/'BiomedReader.dotm'),(compiled,scripts/'BiomedReader.scpt')]:
            if target.exists():shutil.copy2(target,target.with_suffix(target.suffix+'.previous'))
            shutil.copy2(source,target)
        validate_template(startup/'BiomedReader.dotm')
        (directory()/'app-path.txt').write_text(str(Path(sys.executable).resolve()),encoding='utf8')
        QMessageBox.information(parent,'Word 插件安装完成','模板已复制并通过结构校验。这是安装完成提示，不是错误。\n请重新打开 Word，按提示信任本插件后使用“Biomed Reader”选项卡。若使用自定义启动目录，请在该目录加载模板。\nMac Word/EndNote 实机联调仍待验证，请先使用文档副本。')
        return True
    except (OSError,ValueError,subprocess.SubprocessError) as error:QMessageBox.warning(parent,'安装未完成',str(error))

def compile_bridge(source,target,executable,user_home):
    import tempfile
    def literal(value):return '"'+str(value).replace('\\','\\\\').replace('"','\\"').replace('\r','\\r').replace('\n','\\n')+'"'
    text=Path(source).read_text(encoding='utf8').replace('__BIOMED_EXECUTABLE__',literal(executable)).replace('__BIOMED_HOME__',literal(user_home))
    with tempfile.NamedTemporaryFile(suffix='.applescript',delete=False) as file:
        temp=Path(file.name);file.write(text.encode('utf8'))
    try:subprocess.run(['/usr/bin/osacompile','-o',str(target),str(temp)],check=True,capture_output=True,timeout=30)
    finally:temp.unlink(missing_ok=True)

def run_bridge(script,action,payload):
    # Script path and request are argv, never interpolated as AppleScript code.
    wrapper='on run argv\nset bridge to load script POSIX file (item 1 of argv)\nreturn bridge\'s bridgeCall(item 2 of argv)\nend run'
    result=subprocess.run(['/usr/bin/osascript','-e',wrapper,str(script),action+'\n'+payload],check=True,capture_output=True,text=True,timeout=60)
    return unpack(result.stdout[:-1] if result.stdout.endswith('\n') else result.stdout)
