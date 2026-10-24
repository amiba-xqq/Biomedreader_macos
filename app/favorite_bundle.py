"""Versioned, offline favorite exchange: JSON metadata plus explicit PDF members.

We never extract an arbitrary archive path. Validate the whole manifest and every
PDF first, then commit a single database transaction, removing new copies on failure.
"""
import hashlib
import json
import math
import os
import sqlite3
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from database import utc_now
from pdf_library import copy_pdf

FORMAT = 'biomed-reader-favorites'
FIELDS = ('article_key','doi','pmid','pmcid','title','abstract','authors','journal_title','issn',
          'publication_date','publication_year','cited_by_count','is_open_access','landing_page_url',
          'graphical_abstract_url','source_name','publication_types_json','volume','issue','pages')


def export_bundle(database, destination, group_names=(), article_keys=(), include_pdf=True, progress=lambda s:None):
    """Group and article selections form a union; include all memberships of that union."""
    grouped = database.grouped_favorites()
    selected = set(article_keys)
    for name in group_names:
        selected.update(a['article_key'] for a in grouped.get(name, []))
    articles = [a for a in database.favorite_articles() if a['article_key'] in selected]
    if not articles:
        raise ValueError('请至少选择一个有收藏文章的分组或一篇收藏文章。')
    groups = {g['name']:g for g in database.list_custom_groups()}
    memberships = {a['article_key']:[] for a in articles}
    for name,rows in grouped.items():
        if name != '未分组':
            for article in rows:
                if article['article_key'] in memberships:
                    memberships[article['article_key']].append(name)
    used = {name for names in memberships.values() for name in names}
    manifest = dict(format=FORMAT, version=1, created_at=utc_now(),
                    groups=[dict(name=name,keywords=groups[name]['keywords']) for name in sorted(used)],articles=[])
    destination=Path(destination)
    temporary=None
    pdf_count=0
    try:
        with tempfile.NamedTemporaryFile(prefix='.biomed-share-',suffix='.zip',dir=destination.parent,delete=False) as handle:
            temporary=Path(handle.name)
        with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3,allowZip64=True) as archive:
            for index, article in enumerate(articles):
                progress(f'正在打包 {index+1}/{len(articles)}：{article["title"][:70]}')
                record={key:article.get(key) for key in FIELDS}
                record.update(groups=memberships[article['article_key']],tags=article.get('tags') or '',
                              personal_note=article.get('personal_note') or '',favorite_saved_at=article.get('favorite_saved_at'),
                              jif=article.get('jif'),jif_year=article.get('jif_year'),jif_source=article.get('jif_source') or '')
                pdf=database.linked_pdf(article['article_key']) if include_pdf else None
                if pdf:
                    name='paper/'+hashlib.sha256(article['article_key'].encode()).hexdigest()+'.pdf'
                    digest=hashlib.sha256()
                    with pdf.open('rb') as source,archive.open(name,'w',force_zip64=True) as target:
                        while block:=source.read(1024*1024):
                            target.write(block);digest.update(block)
                    record['pdf']=dict(member=name,sha256=digest.hexdigest())
                    pdf_count+=1
                manifest['articles'].append(record)
            archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        os.replace(temporary,destination)
        return dict(articles=len(articles),groups=len(used),pdfs=pdf_count)
    finally:
        if temporary and temporary.exists():temporary.unlink()


def _validate_manifest(archive):
    members={}
    total=0
    for info in archive.infolist():
        name=info.filename
        parts=PurePosixPath(name)
        if name in members or parts.is_absolute() or '..' in parts.parts or '\\' in name or ':' in name:
            raise ValueError('分享包包含不安全或重复的文件名。')
        if stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
            raise ValueError('不支持符号链接或加密的分享包。')
        total+=info.file_size
        if info.file_size>2*1024**3 or total>10*1024**3:
            raise ValueError('分享包超过允许大小（单个文件 2GB，总量 10GB）。')
        members[name]=info
    if 'manifest.json' not in members or members['manifest.json'].file_size>20*1024**2:
        raise ValueError('分享包缺少有效的文章清单。')
    manifest=json.loads(archive.read('manifest.json').decode('utf-8-sig'))
    if not isinstance(manifest,dict) or manifest.get('format')!=FORMAT or manifest.get('version')!=1:
        raise ValueError('不是受支持的 Biomed Reader 收藏分享包。')
    groups,articles=manifest.get('groups'),manifest.get('articles')
    if not isinstance(groups,list) or not isinstance(articles,list) or len(articles)>100000:
        raise ValueError('分享包文章或分组格式无效。')
    group_names=set()
    for group in groups:
        if not isinstance(group,dict) or not isinstance(group.get('name'),str) or not group['name'].strip() or len(group['name'])>200 or group['name']=='未分组':
            raise ValueError('分享包分组名称无效。')
        if group['name'] in group_names or not isinstance(group.get('keywords',''),str):
            raise ValueError('分享包分组重复或关键词无效。')
        group_names.add(group['name'])
    keys=set()
    for article in articles:
        if not isinstance(article,dict) or not isinstance(article.get('article_key'),str) or not article['article_key'] or article['article_key'] in keys:
            raise ValueError('分享包文章标识缺失或重复。')
        keys.add(article['article_key'])
        if not isinstance(article.get('title'),str) or not article['title'].strip():raise ValueError('分享包缺少文章标题。')
        for key in FIELDS+('tags','personal_note','favorite_saved_at','jif_source'):
            if key not in ('publication_year','cited_by_count','is_open_access') and article.get(key) is not None and not isinstance(article[key],str):
                raise ValueError('分享包文章字段类型无效。')
        for key in ('publication_year','cited_by_count','is_open_access','jif','jif_year'):
            if article.get(key) is not None and (not isinstance(article[key],(int,float)) or not math.isfinite(article[key]) or article[key]<0):
                raise ValueError('分享包数值字段无效。')
        kinds=json.loads(article.get('publication_types_json') or '[]')
        if not isinstance(kinds,list) or any(not isinstance(k,str) for k in kinds):raise ValueError('文章类型格式无效。')
        if not isinstance(article.get('groups'),list) or any(not isinstance(g,str) or g not in group_names for g in article['groups']):
            raise ValueError('文章引用了不存在的分组。')
        pdf=article.get('pdf')
        if pdf is not None:
            if not isinstance(pdf,dict) or not isinstance(pdf.get('member'),str) or not pdf['member'].startswith('paper/') or not pdf['member'].endswith('.pdf') or pdf['member'] not in members:
                raise ValueError('文章 PDF 关联无效或文件缺失。')
            if not isinstance(pdf.get('sha256'),str) or len(pdf['sha256'])!=64:raise ValueError('PDF 校验信息无效。')
    return manifest


def import_bundle(database, source, progress=lambda s:None):
    created=[]
    report=dict(articles=0,added=0,merged=0,groups=0,pdfs=0)
    try:
        with zipfile.ZipFile(source) as archive, tempfile.TemporaryDirectory(prefix='.share-import-',dir=database.path.parent) as temporary:
            manifest=_validate_manifest(archive)
            pdf_paths={}
            for index,article in enumerate(manifest['articles']):
                progress(f'正在核验分享包 {index+1}/{len(manifest["articles"])}……')
                pdf=article.get('pdf')
                if pdf:
                    path=Path(temporary)/f'{index}.pdf'
                    digest=hashlib.sha256()
                    with archive.open(pdf['member']) as src,path.open('xb') as target:
                        while block:=src.read(1024*1024):target.write(block);digest.update(block)
                    with path.open('rb') as handle:valid_pdf=b'%PDF-' in handle.read(1024)
                    if digest.hexdigest()!=pdf['sha256'] or not valid_pdf:raise ValueError('分享包 PDF 校验失败，未导入任何文章。')
                    pdf_paths[article['article_key']]=path
            now=utc_now()
            with database.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                group_ids={}
                for group in manifest['groups']:
                    old=connection.execute('SELECT id FROM favorite_groups WHERE name=?',(group['name'],)).fetchone()
                    if old:group_ids[group['name']]=old[0]
                    else:
                        group_ids[group['name']]=connection.execute('INSERT INTO favorite_groups(name,keywords,created_at,updated_at) VALUES(?,?,?,?)',
                            (group['name'],group.get('keywords') or group['name'],now,now)).lastrowid
                        report['groups']+=1
                for article in manifest['articles']:
                    progress(f"正在导入 {report['articles']+1}/{len(manifest['articles'])}：{article['title'][:70]}")
                    incoming=article['article_key']
                    doi=(article.get('doi') or '').strip().lower().removeprefix('https://doi.org/')
                    pmid=(article.get('pmid') or '').strip()
                    old=connection.execute('SELECT article_key FROM articles WHERE article_key=? OR (?<>\'\' AND lower(doi)=?) OR (?<>\'\' AND pmid=?) ORDER BY article_key=? DESC LIMIT 1',
                        (incoming,doi,doi,pmid,pmid,incoming)).fetchone()
                    key=old[0] if old else 'doi:'+doi if doi else 'pmid:'+pmid if pmid else incoming
                    values={field:article.get(field) for field in FIELDS}
                    for field in ('volume','issue','pages'):
                        values[field]=str(values.get(field) or '')
                    values.update(article_key=key,doi=doi,pmid=pmid,source_name=article.get('source_name') or '分享导入',
                                  cited_by_count=article.get('cited_by_count') or 0,is_open_access=article.get('is_open_access') or 0,
                                  publication_types_json=article.get('publication_types_json') or '[]')
                    if old:
                        for field in FIELDS:
                            if field!='article_key' and values[field] is not None:
                                connection.execute(f'UPDATE articles SET {field}=? WHERE article_key=? AND ({field} IS NULL OR {field}=\'\' OR {field}=\'[]\')',(values[field],key))
                    else:
                        connection.execute('INSERT INTO articles('+','.join(FIELDS)+',first_seen_at,updated_at) VALUES('+','.join('?' for _ in range(len(FIELDS)+2))+')',
                            [values[field] for field in FIELDS]+[now,now])
                    favorite=connection.execute('SELECT is_favorite FROM favorites WHERE article_key=?',(key,)).fetchone()
                    report['merged' if favorite and favorite[0] else 'added']+=1
                    connection.execute("INSERT INTO favorites(article_key,is_favorite,tags,personal_note,saved_at,updated_at) VALUES(?,1,?,?,?,?) ON CONFLICT(article_key) DO UPDATE SET is_favorite=1,updated_at=excluded.updated_at,tags=CASE WHEN favorites.tags='' THEN excluded.tags ELSE favorites.tags END,personal_note=CASE WHEN favorites.personal_note='' THEN excluded.personal_note ELSE favorites.personal_note END",
                        (key,article.get('tags') or '',article.get('personal_note') or '',article.get('favorite_saved_at') or now,now))
                    for group in article['groups']:
                        connection.execute('INSERT OR IGNORE INTO favorite_group_articles VALUES(?,?)',(group_ids[group],key))
                    if article.get('jif') is not None:
                        connection.execute('INSERT INTO shared_article_metrics VALUES(?,?,?,?) ON CONFLICT(article_key) DO NOTHING',
                            (key,article['jif'],article.get('jif_year'),article.get('jif_source') or '分享包参考值'))
                    linked=connection.execute('SELECT filename FROM article_pdfs WHERE article_key=?',(key,)).fetchone()
                    available=bool(linked and Path(linked[0]).name==linked[0] and '\\' not in linked[0] and (database.paper_directory/linked[0]).is_file())
                    if incoming in pdf_paths and not available:
                        target=copy_pdf(pdf_paths[incoming],article['title'],key,database.paper_directory)
                        created.append(target)
                        connection.execute("INSERT INTO article_pdfs(article_key,filename,linked_at,storage_location) VALUES(?,?,?,'data') ON CONFLICT(article_key) DO UPDATE SET filename=excluded.filename,linked_at=excluded.linked_at,storage_location='data'",(key,target.name,now))
                        report['pdfs']+=1
                    report['articles']+=1
        return report
    except Exception:
        for path in created:path.unlink(missing_ok=True)
        raise
