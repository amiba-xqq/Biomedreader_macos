"""Per-user file IPC. Word requests carry document-local references, not arbitrary commands."""
import json
import os
import sys
import uuid
import html
from html.parser import HTMLParser
from pathlib import Path
from PySide6.QtCore import QObject, QTimer, QRunnable, QThreadPool
from citations import render, journal_styles
from bibliography import enrich_articles


class BridgeJob(QRunnable):
    def __init__(self,bridge,request,response_path,claimed):
        super().__init__();self.bridge=bridge;self.request=request;self.response_path=response_path;self.claimed=claimed
    def run(self):self.bridge.respond(self.request,self.response_path,self.claimed)


def safe_reference_html(value):
    class Safe(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.output = []
        def handle_starttag(self, tag, attrs):
            if tag in ('i', 'b', 'em', 'strong', 'sup', 'sub'):
                self.output.append('<' + tag + '>')
        def handle_endtag(self, tag):
            if tag in ('i', 'b', 'em', 'strong', 'sup', 'sub'):
                self.output.append('</' + tag + '>')
        def handle_data(self, text):
            self.output.append(html.escape(text))
    parser = Safe()
    parser.feed(value)
    return ''.join(parser.output)


class WordBridge(QObject):
    def __init__(self, window, directory=None):
        super().__init__(window)
        self.window = window
        testing = '--self-test' in sys.argv or '--browser-self-test' in sys.argv
        local_root=(Path.home()/'Library'/'Application Support') if sys.platform=='darwin' else Path(os.environ.get('LOCALAPPDATA') or Path.home()/'AppData'/'Local')
        default = window.database.path.parent / 'word_bridge_test' if testing else local_root / 'OrangeReader' / 'WordBridge'
        self.directory = Path(directory or default)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.selected = None
        self.library_id = window.database.get_settings().get('citation_library_id') or uuid.uuid4().hex
        window.database.save_settings({'citation_library_id': self.library_id})
        self.timer = QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    def select(self, article):
        if not article:
            return
        database = self.window.database
        group_name = self.window.favorites.current_group
        groups = database.list_custom_groups()
        with database.connect() as connection:
            ids = {row[0] for row in connection.execute('SELECT group_id FROM favorite_group_articles WHERE article_key=?', (article['article_key'],))}
        group = next((group for group in groups if group['id'] in ids and group['name'] == group_name), None)
        if group is None:
            group = next((group for group in groups if group['id'] in ids), None)
        fields = ('article_key', 'title', 'authors', 'journal_title', 'publication_date', 'doi', 'volume', 'issue', 'pages', 'landing_page_url', 'source_name')
        self.selected = {'article': {key: article.get(key) for key in fields}, 'group_id': group['id'] if group else None, 'library_id': self.library_id}

    def member(self, record):
        key = record['article']['article_key']
        if record.get('library_id') and record['library_id'] != self.library_id:
            raise ValueError('当前连接的本地文献库与引文原始文献库不同；请连接原文献库后更新。为避免误删，本次不修改文档。')
        with self.window.database.connect() as connection:
            if record.get('group_id') is None:
                return bool(connection.execute('SELECT 1 FROM favorites WHERE article_key=?', (key,)).fetchone())
            return bool(connection.execute('SELECT 1 FROM favorite_group_articles WHERE article_key=? AND group_id=?', (key, record['group_id'])).fetchone())

    def execute(self, request):
        action = request.get('action')
        if action == 'show':
            self.window.showNormal()
            self.window.raise_()
            self.window.activateWindow()
            return True
        if action == 'selected':
            if not self.selected or not self.member(self.selected):
                raise ValueError('请先在 Biomed Reader 收藏文章中选中一篇仍在原分组中的文章。')
            selected=dict(self.selected)
            selected['article']=enrich_articles(self.window.database,[dict(selected['article'])])[0][0]
            return selected
        if action == 'styles':
            from citations import ROOT
            path = ROOT / 'word_catalogue.json'
            if not path.exists():
                raise ValueError('Word 引用样式目录尚未配置。')
            return json.loads(path.read_text(encoding='utf8'))
        if action == 'render':
            records = request.get('records', [])
            if len(records) > 5000:
                raise ValueError('单个文档引用记录超过当前处理上限。')
            validity = [self.member(record) for record in records]
            articles = []
            keys = []
            for record, valid in zip(records, validity):
                if valid and record['article']['article_key'] not in keys:
                    keys.append(record['article']['article_key'])
                    articles.append(record['article'])
            articles,_=enrich_articles(self.window.database,articles) if articles else ([],[])
            formatted = render(articles, request.get('journal') or 'Cell') if articles else {'markers': [], 'references': []}
            formatted['html'] = render(articles, request.get('journal') or 'Cell', rich=True) if articles else {'markers': [], 'references': []}
            for key in ('markers', 'references'):
                formatted['html'][key] = [safe_reference_html(value) for value in formatted['html'][key]]
            formatted.update(valid=validity, keys=keys, articles=articles)
            return formatted
        raise ValueError('不支持的 Word 操作。')

    def poll(self):
        (self.directory / 'alive').touch()
        for path in list(self.directory.glob('*.request.json'))[:8]:
            response_path = path.with_name(path.name.replace('.request.json', '.response.json'))
            claimed = path.with_suffix('.processing.' + str(os.getpid()))
            try:
                path.rename(claimed)
            except FileNotFoundError:
                continue
            try:
                if claimed.stat().st_size > 4_000_000:raise ValueError('请求文件过大。')
                request=json.loads(claimed.read_text(encoding='utf8'))
                if request.get('action') in ('selected','render'):
                    QThreadPool.globalInstance().start(BridgeJob(self,request,response_path,claimed))
                else:self.respond(request,response_path,claimed)
            except Exception as error:self.write_response(response_path,{'ok':False,'error':str(error)},claimed)

    def respond(self,request,response_path,claimed):
        try:response={'ok':True,'data':self.execute(request)}
        except Exception as error:response={'ok':False,'error':str(error)}
        self.write_response(response_path,response,claimed)

    def write_response(self,response_path,response,claimed):
        try:
            temporary=response_path.with_suffix('.tmp.'+uuid.uuid4().hex)
            temporary.write_text(json.dumps(response,ensure_ascii=False),encoding='utf8');temporary.replace(response_path)
        finally:claimed.unlink(missing_ok=True)
