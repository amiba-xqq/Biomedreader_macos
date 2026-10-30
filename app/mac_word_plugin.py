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
        if subprocess.run(['/usr/bin/pgrep','-x','Microsoft Word'],capture_output=True).returncode==0:
            QMessageBox.information(parent,'请先退出 Word','请使用 Word → 退出 Word 后，再安装或修复插件。');return
        startup.mkdir(parents=True,exist_ok=True);scripts.mkdir(parents=True,exist_ok=True);directory().mkdir(parents=True,exist_ok=True)
        for source,target in [(root/'BiomedReader.dotm',startup/'BiomedReader.dotm'),(root/'BiomedReader.applescript',scripts/'BiomedReader.applescript')]:
            if target.exists():shutil.copy2(target,target.with_suffix(target.suffix+'.previous'))
            shutil.copy2(source,target)
        (directory()/'app-path.txt').write_text(str(Path(sys.executable).resolve()),encoding='utf8')
        QMessageBox.information(parent,'插件已安装','重新打开 Word，确认信任本地 BiomedReader.dotm 后，可在“Biomed Reader”选项卡使用。\n若配置了自定义启动目录，请将模板复制到 Word 当前的启动目录。\n当前发行尚无实际 Mac Word/EndNote 联调记录，请先使用文档副本。')
        return True
    except OSError as error:QMessageBox.warning(parent,'安装未完成',str(error))
