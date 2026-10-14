"""Direct bioRxiv search pages. No alternate index, CAPTCHA solving or silent API scan.

The published website is not a stable search API. Unknown layouts, missing pages,
repeated pages and access challenges are errors, never successful empty searches.
"""
import re
import time
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit, unquote, unquote_plus
from biorxiv import rolling_start,keyword_predicate

BASE = 'https://www.biorxiv.org'
DOI = re.compile(r'10\.\d{4,9}/[A-Za-z0-9.]+')


class Node:
    def __init__(self,tag='',attrs=()):
        self.tag,self.attrs,self.children = tag,dict(attrs),[]
    def text(self):
        if self.tag in ('script','style'):
            return ''
        return ' '.join(child.text() if isinstance(child,Node) else child for child in self.children)
    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child,Node):
                yield from child.walk()
    def has_class(self,*names):
        return bool(set(self.attrs.get('class','').split()).intersection(names))


class Document(HTMLParser):
    def __init__(self,text):
        super().__init__(convert_charrefs=True)
        self.root = Node();self.stack = [self.root]
        self.feed(text)
    def handle_starttag(self,tag,attrs):
        node=Node(tag,attrs);self.stack[-1].children.append(node)
        if tag not in ('meta','link','input','img','br','hr','source','wbr','area','base','embed'):
            self.stack.append(node)
    def handle_startendtag(self,tag,attrs):
        self.stack[-1].children.append(Node(tag,attrs))
    def handle_endtag(self,tag):
        for index in range(len(self.stack)-1,0,-1):
            if self.stack[index].tag==tag:
                del self.stack[index:];break
    def handle_data(self,text):
        self.stack[-1].children.append(text)


def compact(text):
    return ' '.join(str(text or '').split())


def search_url(expression,start,end,scope='fulltext',servers='biorxiv',ordering='newest'):
    expression=expression.strip()
    if not expression or len(expression)>8000:
        raise ValueError('bioRxiv 官网搜索关键词为空或过长。')
    if re.search(r'\b(?:jcode|limit_from|limit_to|numresults|sort|format_result|direction)\s*:',expression,re.I):
        raise ValueError('请在方案中填写关键词表达式，勿自行加入官网日期、分页等控制参数。')
    # Reject malformed Boolean queries / PubMed field tags before the website can
    # silently interpret them differently. This does not filter full-text hits locally.
    try:keyword_predicate(expression)
    except ValueError as error:
        raise ValueError('bioRxiv 官网关键词格式无效：请使用关键词、AND/OR/NOT、括号、英文引号或词尾 *，不要使用 PubMed 字段标签。') from error
    if scope not in ('default','fulltext','title_abstract','fulltext_title_abstract'):raise ValueError('未识别的官网关键词匹配范围。')
    if servers not in ('biorxiv','medrxiv'):raise ValueError('单次官网搜索必须选择一个来源；双来源由联合检索合并。')
    if ordering not in ('newest','relevance'):raise ValueError('官网排序方式无效。')
    # Highwire's advanced-search URL joins words inside one field using '+'.
    # This uses the official Abstract or Title field, without fetching every abstract.
    terms=expression
    if scope=='title_abstract':terms='abstract_title:'+expression.replace(' ','+')
    elif scope=='fulltext_title_abstract':terms='text_abstract_title:'+expression.replace(' ','+')
    server_clause=f'jcode:{servers} '
    sort='relevance-rank' if ordering=='relevance' else 'publication-date'
    query=f'{terms} {server_clause}limit_from:{start} limit_to:{end} numresults:75 sort:{sort} direction:descending format_result:standard'
    base='https://www.medrxiv.org' if servers=='medrxiv' else BASE
    return base+'/search/'+quote(query,safe='')


def validate_search_context(text,url,expression=None):
    """Require the website's own search form to echo the requested keywords.

    A matching URL alone is insufficient: a restored session, redirect or stale DOM
    can show a generic listing underneath that address. Never cache such a page.
    """
    decoded=unquote(urlsplit(url).path)[len('/search/'):]
    terms=re.split(r'\s+(?:jcode|limit_from):',decoded,maxsplit=1,flags=re.I)[0]
    field='txtsimple'
    for name in ('abstract_title','text_abstract_title'):
        if terms.startswith(name+':'):
            terms=terms[len(name)+1:].split(' '+name+'_flags:',1)[0].replace('+',' ')
            field=name;break
    expected=compact(expression if expression is not None else terms).casefold()
    nodes=list(Document(text).root.walk())
    echoes=[compact(node.attrs.get('value','')).casefold() for node in nodes
            if node.tag=='input' and node.attrs.get('name')==field]
    if not expected or not echoes or any(value!=expected for value in echoes):
        raise ValueError('官网回显的搜索词与当前关键词组不一致或缺失，未读取该页面。请重新加载搜索页；旧缓存保留。')
    server_match=re.search(r'\bjcode:(biorxiv|medrxiv)\b',decoded)
    selectors=[node for node in nodes if node.tag=='select' and node.attrs.get('name')=='jcode[]']
    if server_match and selectors:
        selected=[node.attrs.get('value','') for selector in selectors for node in selector.walk() if node.tag=='option' and 'selected' in node.attrs]
        # The host is authoritative; some mirrored forms carry an old selected
        # attribute even when their actual result list belongs to the correct host.
        host_server='medrxiv' if urlsplit(url).hostname=='www.medrxiv.org' else 'biorxiv'
        if host_server!=server_match.group(1):
            raise ValueError('官网回显的预印本来源与选择不一致，已停止，旧缓存保留。')
    for token,field in (('limit_from','limit_from[date]_replacement'),('limit_to','limit_to[date]_replacement')):
        match=re.search(r'\b'+token+r':(\d{4}-\d{2}-\d{2})\b',decoded)
        values=[node.attrs.get('value','') for node in nodes if node.tag=='input' and node.attrs.get('name')==field]
        if match and values and any(value!=match.group(1) for value in values):
            raise ValueError('官网回显的日期范围与当前检索不一致，已停止，旧缓存保留。')
    return terms


def safe_page(url):
    try:
        parsed=urlsplit(url)
        return parsed.scheme=='https' and parsed.hostname in ('www.biorxiv.org','www.medrxiv.org') and parsed.port in (None,443) and parsed.path.startswith('/search/') and not parsed.username
    except ValueError:
        return False


def challenge_page(text):
    # Normal result pages can contain CAPTCHA-related scripts/forms in their footer.
    if any(node.has_class('search-result','highwire-search-result') for node in Document(text).root.walk()):
        return False
    return any(marker in text.casefold() for marker in
        ('<title>just a moment','cf-chl-','_cf_chl_opt','verify you are human','checking your browser',
         '<title>access denied','captcha verification','please complete the captcha'))


def rate_limit_page(text):
    """Recognize a waiting interstitial, including the one in the user's report.

    Result pages may include security scripts in their footer, so only treat a
    page without result entries as a waiting screen. Do not reload it ourselves:
    the website owns the countdown and its automatic navigation.
    """
    if any(node.has_class('search-result','highwire-search-result') for node in Document(text).root.walk()):
        return False
    plain=compact(Document(text).root.text()).casefold()
    return any(marker in plain for marker in (
        'high number of requests from this session', 'too many requests',
        'you are being rate limited', 'rate limit exceeded',
        'this page will reload automatically'))


def parse_page(text,url):
    if len(text)>8*1024**2:
        raise ValueError('bioRxiv 官网搜索页面过大，已停止。')
    if rate_limit_page(text):
        raise ValueError('官网正在限制请求频率，请等待网页倒计时结束后再继续；已读取进度保留。')
    if challenge_page(text):
        raise ValueError('bioRxiv 官网要求浏览器验证或拒绝访问；请在搜索窗口中手动完成验证。旧缓存保留。')
    root=Document(text).root
    nodes=list(root.walk())
    # On the real site only read the main result list, excluding sidebars and
    # suggested papers. Small layout fixtures may omit the containing element.
    lists=[node for node in nodes if node.has_class('highwire-search-results-list')]
    result_nodes=[child for node in lists for child in node.walk()] if lists else nodes
    entries=[node for node in result_nodes if node.has_class('search-result','highwire-search-result')]
    rows=[]
    for entry in entries:
        descendants=list(entry.walk())
        title_node=next((n for n in descendants if n.has_class('highwire-cite-title')),None)
        links=[n for n in descendants if n.tag=='a' and '/content/' in n.attrs.get('href','')]
        link=next((n for n in links if title_node and title_node in list(n.walk())),links[0] if links else None)
        title=compact(title_node.text() if title_node else link.text() if link else '')
        if entry.has_class('result-jcode-medrxiv') or any('medrxiv;' in n.attrs.get('data-pisa','') for n in descendants):server='medrxiv'
        elif entry.has_class('result-jcode-biorxiv') or any('biorxiv;' in n.attrs.get('data-pisa','') for n in descendants):server='biorxiv'
        else:server='medrxiv' if urlsplit(url).hostname in ('www.medrxiv.org','medrxiv.org') else 'biorxiv'
        landing=urljoin('https://www.medrxiv.org' if server=='medrxiv' else BASE,link.attrs['href']) if link else ''
        if urlsplit(landing).hostname not in ('www.biorxiv.org','biorxiv.org','www.medrxiv.org','medrxiv.org'):
            raise ValueError('官网结果包含未识别的文章来源，已停止。')
        if urlsplit(landing).hostname in ('www.medrxiv.org','medrxiv.org'):server='medrxiv'
        doi_node=next((n for n in descendants if n.has_class('highwire-cite-metadata-doi')),None)
        match=DOI.search(doi_node.text() if doi_node else unquote(landing))
        if not title or not match:
            raise ValueError('官网文章条目缺少标题或 DOI，不能将部分结果当作完整结果。')
        doi=re.sub(r'v\d+(?:\.(?:full|abstract))?$','',match.group()).rstrip('.').lower()
        version=re.search(r'v(\d+)(?:[./?#]|$)',landing)
        authors=[compact(n.text()) for n in descendants if n.has_class('highwire-citation-author')]
        date_node=next((n for n in descendants if n.tag=='time' or n.has_class('highwire-cite-metadata-date','highwire-cite-metadata-publication-date')),None)
        posted=''
        if date_node:
            raw=date_node.attrs.get('datetime') or compact(date_node.text())
            iso=re.search(r'\d{4}-\d{2}-\d{2}',raw)
            if iso:
                posted=iso.group()
            else:
                raw=re.sub(r'^Posted\s+','',raw,flags=re.I).rstrip('.')
                for fmt in ('%B %d, %Y','%b %d, %Y','%d %B %Y'):
                    try:posted=datetime.strptime(raw,fmt).date().isoformat();break
                    except ValueError:pass
        if not posted:
            for node in descendants:
                match_date=re.search(r'/early/(\d{4})/(\d{2})/(\d{2})/',node.attrs.get('data-apath',''))
                if match_date:
                    try:posted=date(*map(int,match_date.groups())).isoformat()
                    except ValueError:pass
                    break
        rows.append(dict(doi=doi,title=title,abstract='',authors='; '.join(authors),date=posted,
            version=version.group(1) if version else '',website_url=landing,
            biorxiv_search_method='website',website_date_filtered=True,preprint_server=server))
    plain=compact(root.text())
    total=None
    for node in nodes:
        if node.has_class('highwire-search-summary','search-results-count','highwire-search-summary-count'):
            match=re.search(r'(?:of\s+)?([\d,]+)\s+(?:results?|matches?|articles?)',compact(node.text()),re.I)
            if match:
                total=int(match.group(1).replace(',',''));break
            match=re.search(r'\bof\s+([\d,]+)\b',compact(node.text()),re.I)
            if match:
                total=int(match.group(1).replace(',',''));break
    no_results=any(n.has_class('view-empty','search-no-results') for n in nodes) or bool(re.search(r'\b(?:no results found|no results were found|your search yielded no results|0 results)\b',plain,re.I))
    recognized=bool(entries) or total==0 or no_results
    if no_results and not entries and total is None:
        total=0
    if not recognized:
        raise ValueError('未识别到 bioRxiv 官网搜索结果结构；可能页面已改版或尚未加载完成。旧缓存保留。')
    next_links=[]
    for node in nodes:
        if node.tag!='a' or not node.attrs.get('href'):
            continue
        rel=node.attrs.get('rel','').split()
        label=compact(node.text()).casefold()
        title=node.attrs.get('title','').casefold()
        parent_next=node.has_class('pager-next') or any(n.has_class('pager-next') and node in list(n.walk()) for n in nodes if n.tag=='li')
        if 'next' in rel or parent_next or title in ('next','next page','go to next page') or label in ('next','next page','next ›','next »','›','»'):
            candidate=urljoin(url,node.attrs['href'])
            if not safe_page(candidate):
                raise ValueError('官网分页链接指向未识别地址，已停止。')
            next_links.append(candidate)
    next_url=next_links[0] if next_links else None
    if total and not rows:
        raise ValueError('官网报告存在结果，但文章条目未成功读取。')
    return rows,next_url,total


def search_website(session,expression,year_from,year_to,recent_years,notify,requested=None,fetch=None,start_date=None,limit=0,scope='fulltext',servers='biorxiv',ordering='newest',cache_path=None,cancelled=None,progress_jobs=None,rows_callback=None):
    """Read matching pages within exact date bounds; zero limit reads the entire window."""
    if servers=='both':
        # The site silently defaults to bioRxiv when jcode is omitted, and comma
        # lists can yield false empty results. Enumerate both verified single-server
        # searches; do not use an undocumented aggregate parameter.
        combined={}
        for server in ('biorxiv','medrxiv'):
            if server=='medrxiv':
                for _ in range(20):
                    if cancelled and cancelled():raise InterruptedError('检索已取消，保留上次缓存。')
                    time.sleep(.2)
            notify('联合官网检索：'+('bioRxiv' if server=='biorxiv' else 'medRxiv'))
            for row in search_website(session,expression,year_from,year_to,recent_years,notify,requested,fetch,start_date,limit,scope,server,ordering,cache_path,cancelled,progress_jobs,rows_callback):
                combined[row['doi']]=row
        rows=list(combined.values())
        if ordering=='relevance':rows.sort(key=lambda row:(int(row.get('website_rank',0)),-date.fromisoformat(row['date']).toordinal() if row.get('date') else 0))
        else:rows.sort(key=lambda row:(row.get('date') or '',-int(row.get('website_rank',0))),reverse=True)
        # Each server has its own full candidate budget; N+N may yield up to 2N.
        for rank,row in enumerate(rows):row['website_rank']=rank
        return rows
    today=date.today()
    start=max(rolling_start(recent_years,today),date(year_from or 2013,1,1))
    if start_date:start=max(start,start_date)
    end=min(today,date(year_to or today.year,12,31))
    if start>end:
        return []
    url=search_url(expression,start,end,scope,servers,ordering)
    from website_progress import WebsiteProgress
    progress=WebsiteProgress(cache_path,url) if cache_path else None
    visited,fingerprints,collected=set(),set(),{}
    expected=None;seen_entries=0;network_pages=0
    while url:
        if cancelled and cancelled():raise InterruptedError('检索已取消，保留上次缓存。')
        notify(f'bioRxiv 官网：关键词“{expression}” · 第 {len(visited)+1} 页，已收集 {len(collected)} 篇；日期 {start} 至 {end}。')
        if url in visited:
            raise ValueError('bioRxiv 官网分页发生循环，未完成全部结果读取。')
        visited.add(url)
        text=progress.read(url) if progress and len(visited)>1 else None
        if text is not None:
            try:
                parse_page(text,url);validate_search_context(text,url,expression)
            except ValueError:
                progress.discard();text=None
        if text is not None:
            notify(f'复用已读取的官网第 {len(visited)} 页；继续上次未完成的检索。')
        else:
            # Serial requests with a pause also between the two preprint hosts.
            # Short slices keep cancellation responsive during longer pauses.
            if network_pages:
                delay=15 if network_pages%5==0 else 4
                notify(f'已保存读取进度，等待 {delay} 秒后继续官网下一页，降低请求频率……')
                for _ in range(delay*5):
                    if cancelled and cancelled():raise InterruptedError('检索已取消，保留上次缓存。')
                    time.sleep(.2)
            if requested:requested()
            if fetch:
                text=fetch(url)
            else:
                response=session.get(url,timeout=(10,45))
                if response.status_code in (401,403,429):
                    raise ValueError(f'官网拒绝访问或限流（HTTP {response.status_code}），已读取进度及旧缓存保留；请稍后再刷新。')
                response.raise_for_status();text=response.text
            network_pages+=1
        rows,next_url,total=parse_page(text,url)
        validate_search_context(text,url,expression)
        if progress and len(visited)==1:progress.check_head(rows,next_url,total)
        if next_url and unquote(urlsplit(next_url).path).rstrip('/')!=unquote(urlsplit(url).path).rstrip('/'):
            raise ValueError('官网分页改变了检索条件，已停止，旧缓存保留。')
        if total is not None:
            if expected is not None and expected!=total:
                if progress:progress.discard()
                raise ValueError('官网检索期间总结果数发生变化，请重试以获取完整结果。')
            expected=total
        fingerprint=tuple(row['doi'] for row in rows)
        if rows and fingerprint in fingerprints:
            raise ValueError('官网重复返回同一页，已停止，避免将不完整结果当作成功。')
        fingerprints.add(fingerprint)
        seen_entries+=len(rows)
        for row_index,row in enumerate(rows):
            if servers!='both' and row['preprint_server']!=servers:
                raise ValueError('官网返回了未选择的预印本来源，旧缓存保留。')
            row['website_rank']=seen_entries-len(rows)+row_index
            old=collected.get(row['doi'])
            if old is None or int(row['version'] or 0)>int(old['version'] or 0):
                collected[row['doi']]=row
        if progress:progress.save(url,text)
        if rows_callback:
            # Only publish rows after query, host, pagination and duplicate checks.
            allowed=set(list(collected)[:limit]) if limit else set(collected)
            # Repeated DOI versions must not exceed the candidate budget or
            # replace a newer version with an older row during a partial run.
            keys=dict.fromkeys(row['doi'] for row in rows if row['doi'] in allowed)
            rows_callback([collected[key] for key in keys])
        if limit and len(collected)>=limit:
            if progress:
                if progress_jobs is not None:progress_jobs.append(progress)
                else:progress.discard()
            return list(collected.values())[:limit]
        if not next_url and expected is not None and seen_entries<expected:
            raise ValueError(f'官网列出 {expected} 条，但仅读取 {seen_entries} 条且缺少下一页，旧缓存保留。')
        if not next_url and expected is None:
            raise ValueError('官网未提供可识别的总结果数，无法确认已读取全部结果。旧缓存保留。')
        if expected is not None and seen_entries>expected:
            raise ValueError('官网结果数与已读取条目不一致，请重试。旧缓存保留。')
        url=next_url
    if progress:
        if progress_jobs is not None:progress_jobs.append(progress)
        else:progress.discard()
    return list(collected.values())


def fetch_detail(session,doi,version='',server='biorxiv'):
    if not DOI.fullmatch(doi):
        raise ValueError('预印本 DOI 格式无效。')
    if server not in ('biorxiv','medrxiv'):raise ValueError('预印本来源无效。')
    response=session.get('https://api.biorxiv.org/details/'+server+'/'+quote(doi,safe='/')+'/na/json',timeout=(10,35))
    response.raise_for_status()
    payload=response.json()
    rows=[r for r in payload.get('collection',[]) if str(r.get('doi') or '').lower()==doi.lower()]
    if version:
        rows=[r for r in rows if str(r.get('version'))==str(version)]
    if not rows:
        raise ValueError('暂未取得该文章版本的摘要，仍可通过 DOI 查看官网原文。')
    return {**max(rows,key=lambda r:int(r.get('version') or 0)),'preprint_server':server}
