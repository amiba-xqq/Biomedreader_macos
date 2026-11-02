"""Resolve the native Codex component without depending on Finder's shell PATH."""
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit


def candidates():
    if sys.platform=='darwin':
        root=Path(getattr(sys,'_MEIPASS',Path(__file__).parent))
        # The release includes an architecture-matched, checksum-pinned CLI.
        yield root/'codex-runtime/codex'
        home=Path.home()
        for apps in (Path('/Applications'),home/'Applications'):
            for name in ('Codex.app','ChatGPT.app'):
                yield apps/name/'Contents/Resources/codex'
        for folder in (home/'.local/bin',Path('/opt/homebrew/bin'),Path('/usr/local/bin'),home/'.npm-global/bin',home/'.volta/bin'):
            yield folder/'codex'
        for pattern in ('.nvm/versions/node/*/bin/codex','.local/share/mise/installs/node/*/bin/codex'):
            yield from sorted(home.glob(pattern),reverse=True)
    found=shutil.which('codex')
    if found:yield Path(found)
    if sys.platform=='win32':
        roots=Path(os.environ.get('LOCALAPPDATA',''))/'OpenAI/Codex/bin'
        yield from sorted(roots.glob('*/codex.exe'),key=lambda p:p.stat().st_mtime,reverse=True)


def executable():
    for path in candidates():
        if path.is_file() and os.access(path,os.X_OK):return str(path)
    raise ValueError('未检测到可执行的Codex组件。请重新安装本次Mac完整DMG，或安装官方Codex CLI后点击“检测 / 刷新模型”。')


def process_environment(binary,home):
    env=dict(os.environ,CODEX_HOME=str(home))
    for name in ('OPENAI_API_KEY','CODEX_API_KEY','CODEX_ACCESS_TOKEN'):env.pop(name,None)
    if sys.platform=='darwin':
        folders=[str(Path(binary).parent),str(Path(binary).resolve().parent),str(Path.home()/'.local/bin'),'/opt/homebrew/bin','/usr/local/bin','/usr/bin','/bin']
        env['PATH']=os.pathsep.join(dict.fromkeys(folders+env.get('PATH','').split(os.pathsep)))
    return env


def login_url(value):
    """Accept both official login hosts, never lookalike domains or userinfo."""
    try:
        url=urlsplit(value)
        valid=(url.scheme=='https' and url.hostname in ('auth.openai.com','chatgpt.com') and not url.username and not url.password and url.port in (None,443))
    except (ValueError,TypeError):valid=False
    if not valid:raise ValueError('Codex未返回可识别的官方登录地址，请更新Codex组件后重试。')
    return value
