"""Offline directory exchange: one bounded JSON manifest, no extracted files or code."""
import json
import math
import os
import re
import stat
import tempfile
import uuid
import zipfile
from pathlib import Path
from database import utc_now

FORMAT = 'biomed-reader-journal-scopes'
MAX_MANIFEST = 20*1024**2
ISSN = re.compile(r'\d{4}-\d{3}[\dX]')


def export_journal_bundle(database, destination, scope_ids, progress=lambda text:None):
    """Take a consistent snapshot, include selected memberships and matching journal metadata."""
    chosen = set(scope_ids)
    destination = Path(destination)
    if destination.suffix.casefold() != '.zip':
        raise ValueError('期刊目录分享包请保存为 .zip 文件。')
    with database.connect() as connection:
        connection.execute('BEGIN')
        scopes = [dict(row) for row in connection.execute('SELECT * FROM journal_scopes') if row['id'] in chosen]
        if not scopes or len(scopes)!=len(chosen):
            raise ValueError('请至少选择一个存在的期刊目录卡片。')
        required = {issn for scope in scopes for issn in json.loads(scope['issns_json'])}
        journals, represented = [],set()
        for row in connection.execute('SELECT * FROM journals ORDER BY issn'):
            aliases = {row['issn'],*filter(None,(row['aliases'] or '').split(';'))}
            if not aliases.intersection(required):
                continue
            journals.append(dict(issn=row['issn'],title=row['title'],aliases=sorted(aliases),
                jif=row['jif'],jif_year=row['jif_year'],source=row['source'] or '目录分享'))
            represented.update(aliases)
        for missing in sorted(required-represented):
            journals.append(dict(issn=missing,title=missing,aliases=[missing],jif=None,jif_year=None,source='目录分享'))
    manifest = dict(format=FORMAT,version=1,created_at=utc_now(),
        scopes=[dict(name=scope['name'],issns=json.loads(scope['issns_json'])) for scope in scopes],journals=journals)
    payload = json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf8')
    validate_manifest(manifest)
    if len(payload)>MAX_MANIFEST:
        raise ValueError('期刊目录分享清单超过 20MB，请减少选择的目录数量。')
    progress(f'正在导出 {len(scopes)} 个目录、{len(required)} 个成员 ISSN……')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.biomed-journals-',suffix='.zip',dir=destination.parent,delete=False) as handle:
            temporary = Path(handle.name)
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json',payload)
        os.replace(temporary,destination)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return dict(scopes=len(scopes),issns=len(required),journals=len(journals))


def validate_manifest(manifest):
    """Validate every value before opening an import transaction."""
    if not isinstance(manifest,dict) or manifest.get('format')!=FORMAT or manifest.get('version')!=1:
        raise ValueError('不是受支持的 Biomed Reader 期刊目录分享包。')
    scopes,journals = manifest.get('scopes'),manifest.get('journals')
    if not isinstance(scopes,list) or not 1<=len(scopes)<=10000 or not isinstance(journals,list) or len(journals)>100000:
        raise ValueError('期刊目录或期刊清单数量无效。')
    required,represented,primary = set(),set(),set()
    for scope in scopes:
        if not isinstance(scope,dict) or not isinstance(scope.get('name'),str) or not scope['name'].strip() or len(scope['name'])>500 or '\x00' in scope['name']:
            raise ValueError('目录名称无效。')
        members = scope.get('issns')
        if not isinstance(members,list) or not 1<=len(members)<=100000:
            raise ValueError('目录成员 ISSN 清单无效。')
        if any(not isinstance(issn,str) or not ISSN.fullmatch(issn) for issn in members) or len(set(members))!=len(members):
            raise ValueError('目录包含无效或重复的 ISSN。')
        required.update(members)
    for row in journals:
        if not isinstance(row,dict) or not isinstance(row.get('issn'),str) or not ISSN.fullmatch(row['issn']) or row['issn'] in primary:
            raise ValueError('期刊主 ISSN 无效或重复。')
        primary.add(row['issn'])
        if not isinstance(row.get('title'),str) or not row['title'].strip() or len(row['title'])>10000:
            raise ValueError('期刊名称无效。')
        if not isinstance(row.get('source',''),str) or len(row.get('source',''))>10000:
            raise ValueError('期刊来源信息无效。')
        aliases = row.get('aliases',[])
        if not isinstance(aliases,list) or len(aliases)>100 or any(not isinstance(a,str) or not ISSN.fullmatch(a) for a in aliases):
            raise ValueError('期刊 ISSN 别名无效。')
        represented.update([row['issn'],*aliases])
        if not {row['issn'],*aliases}.intersection(required):
            raise ValueError('清单包含不属于所选目录的期刊。')
        value = row.get('jif')
        if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or value<0):
            raise ValueError('JIF 必须是非负有限数值或空值。')
        year = row.get('jif_year')
        if year is not None and (type(year)!=int or not 1000<=year<=9999):
            raise ValueError('JIF 年份无效。')
    if not required.issubset(represented):
        raise ValueError('目录成员缺少对应的期刊信息。')


def import_journal_bundle(database, source, progress=lambda text:None):
    """Create custom copies atomically; retain local nonempty JIF and add missing metadata."""
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        if len(infos)!=1 or infos[0].filename!='manifest.json' or infos[0].file_size>MAX_MANIFEST:
            raise ValueError('目录分享包应仅包含不超过 20MB 的 manifest.json。')
        if infos[0].flag_bits & 1 or stat.S_ISLNK(infos[0].external_attr >> 16):
            raise ValueError('不支持加密或符号链接分享包。')
        with archive.open(infos[0]) as handle:
            payload = handle.read(MAX_MANIFEST+1)
        if len(payload)>MAX_MANIFEST:
            raise ValueError('目录分享清单过大。')
        manifest = json.loads(payload.decode('utf-8-sig'))
    validate_manifest(manifest)
    progress(f"正在导入 {len(manifest['scopes'])} 个期刊目录……")
    now = utc_now()
    names,renamed,new_journals = [],0,0
    with database.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        occupied = {row[0] for row in connection.execute('SELECT name FROM journal_scopes')}
        journal_index = {}
        for row in connection.execute('SELECT * FROM journals ORDER BY issn'):
            for alias in {row['issn'],*filter(None,(row['aliases'] or '').split(';'))}:
                journal_index.setdefault(alias,dict(row))
        for row in manifest['journals']:
            existing = journal_index.get(row['issn']) or next((journal_index[alias] for alias in row.get('aliases',[]) if alias in journal_index),None)
            aliases = sorted({row['issn'],*row.get('aliases',[]),*filter(None,((existing['aliases'] or '') if existing else '').split(';')),
                              *([existing['issn']] if existing else [])})
            if existing:
                historical=connection.execute('SELECT jif FROM journal_metrics WHERE issn=? AND year=?',
                    (existing['issn'],row.get('jif_year'))).fetchone()
                incoming_metric = existing['jif'] is None and row.get('jif') is not None and historical is None
                title = existing['title'] if existing['title'] and existing['title']!=existing['issn'] else row['title']
                jif = row.get('jif') if incoming_metric else existing['jif']
                year = row.get('jif_year') if incoming_metric else existing['jif_year']
                if year is None and jif is not None and jif==row.get('jif'):
                    year = row.get('jif_year')
                provenance = (row.get('source') or '目录分享') if incoming_metric else existing['source']
                connection.execute('UPDATE journals SET title=?,aliases=?,jif=?,jif_year=?,source=?,updated_at=? WHERE issn=?',
                    (title,';'.join(aliases),jif,year,provenance,now,existing['issn']))
            else:
                connection.execute('INSERT INTO journals(issn,title,category,jif,jif_year,enabled,updated_at,aliases,source) VALUES(?,?,?,?,?,0,?,?,?)',
                    (row['issn'],row['title'],'',row.get('jif'),row.get('jif_year'),now,';'.join(aliases),row.get('source') or '目录分享'))
                new_journals += 1
            stored = dict(connection.execute('SELECT * FROM journals WHERE issn=?',(existing['issn'] if existing else row['issn'],)).fetchone())
            if row.get('jif') is not None and row.get('jif_year'):
                connection.execute('INSERT INTO journal_metrics VALUES(?,?,?,?,?) ON CONFLICT(issn,year) DO NOTHING',
                    (stored['issn'],row['jif_year'],row['jif'],row.get('source') or '目录分享',now))
            for alias in aliases:
                journal_index[alias] = stored
        database._snapshot_metrics(connection)
        selected_year=int(connection.execute("SELECT value FROM settings WHERE key='jif_year'").fetchone()[0])
        database._activate_jif_year(connection,selected_year)
        for scope in manifest['scopes']:
            base = scope['name'].strip()
            name = base
            if name in occupied:
                renamed += 1
                name = base+'_已重复'
                index = 2
                while name in occupied:
                    name = base+f'_已重复_{index}'
                    index += 1
            occupied.add(name)
            connection.execute('INSERT INTO journal_scopes VALUES(?,?,?,0,?,?)',
                (uuid.uuid4().hex,name,json.dumps(scope['issns']),now,now))
            names.append(name)
    database._metrics = None
    return dict(scopes=len(names),names=names,renamed=renamed,journals=new_journals)
