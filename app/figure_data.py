"""Local figure projects and auditable calculations."""
import csv
import io
import itertools
import json
import math
import os
import re
import statistics
import unicodedata
import uuid
from collections import defaultdict
from pathlib import Path

MODES = {'wb': 'WB / gel 作图', 'if': 'IF 作图', 'qpcr': 'qPCR 作图', 'table': 'Excel / CSV 作图'}
IMAGES = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp'}
TABLES = {'.xls', '.xlsx', '.csv', '.tsv', '.txt'}
CHART_TYPES = {
    'bar':'柱形图', 'line':'分类折线图', 'scatter':'分组散点图',
    'box':'箱线图（Tukey）', 'violin':'小提琴图', 'histogram':'频数直方图',
    'ecdf':'累积分布图', 'xy':'XY 散点图', 'xyline':'XY 折线图',
    'heatmap':'热图', 'stacked':'堆叠柱图',
}
GROUPED_CHARTS = {
    'grouped_bar':('分组并列柱形图','bar'),
    'grouped_separate':('分组柱形图（按处理条件排列）','bar'),
    'grouped_stacked':('分组堆叠柱形图','stacked'),
    'grouped_points':('分组柱形图＋重复点','bar'),
    'grouped_scatter':('分组散点图','scatter'),
    'grouped_aligned':('分组对齐散点图','scatter'),
    'grouped_box':('分组箱线图','box'),
    'grouped_violin':('分组小提琴图','violin'),
    'grouped_line':('分组均值折线图','line'),
    'grouped_range':('分组浮动柱（最小–最大）','range'),
}
CHART_TYPES.update({key:title for key,(title,_) in GROUPED_CHARTS.items()})
MATRIX_ORIENTATIONS = {'matrix_columns','matrix_rows'}
COLUMN_CHARTS = ('bar','scatter','box','violin','line','histogram','ecdf','range')
CHART_TYPES['range'] = '浮动柱（最小–最大）'
STAT_TESTS = {
    'none':('不检验','None'),
    'pooled':('双尾非配对等方差 t 检验','two-sided unpaired equal-variance t-test'),
    'welch':('双尾 Welch t 检验','two-sided Welch t-test'),
    'paired':('双尾配对 t 检验','two-sided paired t-test'),
    'ranksum':('Mann–Whitney U（Wilcoxon 秩和）','two-sided Mann–Whitney U (Wilcoxon rank-sum)'),
    'signedrank':('Wilcoxon 符号秩（配对）','two-sided Wilcoxon signed-rank'),
    'anova':('单因素 ANOVA（独立样本）','one-way ANOVA (independent samples)'),
    'kruskal':('Kruskal–Wallis（独立样本）','Kruskal–Wallis H'),
}
PAIRED_TESTS = {'paired','signedrank'}
OMNIBUS_TESTS = {'anova','kruskal'}


class FigureStore:
    def __init__(self, database):
        self.database = database
        with database.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS figure_projects (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, mode TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT '{}', updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS figure_files (
                    id INTEGER PRIMARY KEY, project TEXT NOT NULL REFERENCES figure_projects(id) ON DELETE CASCADE,
                    name TEXT NOT NULL, data BLOB NOT NULL DEFAULT X'', path TEXT NOT NULL DEFAULT '', position INTEGER
                );
                CREATE TABLE IF NOT EXISTS figure_exports (
                    project TEXT NOT NULL REFERENCES figure_projects(id) ON DELETE CASCADE,
                    extension TEXT NOT NULL, data BLOB NOT NULL, PRIMARY KEY(project, extension)
                );
            """)
            db.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(figure_files)')}
            if 'path' not in columns:
                db.execute("ALTER TABLE figure_files ADD COLUMN path TEXT NOT NULL DEFAULT ''")
            if 'position' not in columns:
                db.execute("ALTER TABLE figure_files ADD COLUMN position INTEGER")

    def projects(self):
        with self.database.connect() as db:
            return list(db.execute('SELECT id,title,mode,updated FROM figure_projects ORDER BY rowid DESC'))

    def create(self, title, mode):
        identifier = uuid.uuid4().hex
        with self.database.connect() as db:
            db.execute('INSERT INTO figure_projects(id,title,mode) VALUES(?,?,?)', (identifier, title, mode))
        return identifier

    def project(self, identifier):
        with self.database.connect() as db:
            return db.execute('SELECT * FROM figure_projects WHERE id=?', (identifier,)).fetchone()

    def attach(self, identifier, paths):
        paths = [path.resolve() for path in paths]
        with self.database.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            known = {os.path.normcase(str(Path(row['path']).resolve())) for row in db.execute(
                "SELECT path FROM figure_files WHERE project=? AND path!=''", (identifier,))}
            position = db.execute('SELECT COALESCE(MAX(COALESCE(position,id)),0) FROM figure_files WHERE project=?',
                                  (identifier,)).fetchone()[0]
            for path in paths:
                normalized = os.path.normcase(str(path))
                if normalized not in known:
                    position += 1
                    db.execute("INSERT INTO figure_files(project,name,data,path,position) VALUES(?,?,X'',?,?)",
                               (identifier,path.name,str(path),position))
                    known.add(normalized)
            db.execute('UPDATE figure_projects SET updated=CURRENT_TIMESTAMP WHERE id=?',(identifier,))

    def files(self, identifier, content=False):
        columns = 'id,name,path,length(data)>0 AS legacy'
        with self.database.connect() as db:
            rows = list(db.execute(f'SELECT {columns} FROM figure_files WHERE project=? ORDER BY COALESCE(position,id),id',
                                   (identifier,)))
        if content:
            return [dict(row, data=self.file(identifier, row['id'])) for row in rows]
        return rows

    def reorder_files(self,identifier,ordered_ids):
        with self.database.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            ids = {row[0] for row in db.execute('SELECT id FROM figure_files WHERE project=?',(identifier,))}
            if len(ordered_ids)!=len(ids) or set(ordered_ids)!=ids:
                raise ValueError('素材列表已改变，请重新加载后调整顺序。')
            db.executemany('UPDATE figure_files SET position=? WHERE project=? AND id=?',
                           [(index,identifier,file_id) for index,file_id in enumerate(ordered_ids,1)])
            db.execute('UPDATE figure_projects SET updated=CURRENT_TIMESTAMP WHERE id=?',(identifier,))

    def file(self, identifier, file_id):
        with self.database.connect() as db:
            row = db.execute("SELECT name,path,CASE WHEN path='' THEN data END AS legacy "
                             'FROM figure_files WHERE project=? AND id=?', (identifier, file_id)).fetchone()
        if row is None:
            raise ValueError('找不到所选素材。')
        if row['path']:
            try:
                return Path(row['path']).read_bytes()
            except OSError:
                raise ValueError(f"无法读取素材「{row['name']}」。请重新关联原文件。") from None
        if row['legacy']:
            return row['legacy']
        raise ValueError(f"请重新关联素材「{row['name']}」。")

    def relink(self, identifier, file_id, path):
        path = path.resolve()
        with self.database.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            qpcr = db.execute('SELECT mode FROM figure_projects WHERE id=?',(identifier,)).fetchone()[0]=='qpcr'
            if qpcr and any(os.path.normcase(str(Path(row['path']).resolve()))==os.path.normcase(str(path))
                            for row in db.execute("SELECT path FROM figure_files WHERE project=? AND id!=? AND path!=''",
                                                  (identifier,file_id))):
                raise ValueError('该文件已经是项目素材，请在同一素材内设置重复组。')
            db.execute("UPDATE figure_files SET name=?,path=?,data=X'' WHERE project=? AND id=?",
                       (path.name,str(path),identifier,file_id))
            db.execute('UPDATE figure_projects SET updated=CURRENT_TIMESTAMP WHERE id=?',(identifier,))

    def remove_files(self, identifier, identifiers, state):
        with self.database.connect() as db:
            db.executemany('DELETE FROM figure_files WHERE project=? AND id=?',
                           [(identifier,file_id) for file_id in identifiers])
            self._save_state(db,identifier,state)

    @staticmethod
    def _save_state(db, identifier, state):
        db.execute('UPDATE figure_projects SET state=?,updated=CURRENT_TIMESTAMP WHERE id=?',
                   (json.dumps(state, ensure_ascii=False, allow_nan=False), identifier))

    def save(self, identifier, state, artifacts=None):
        with self.database.connect() as db:
            self._save_state(db,identifier,state)
            if artifacts is not None:
                db.execute('DELETE FROM figure_exports WHERE project=?', (identifier,))
                db.executemany('INSERT INTO figure_exports VALUES(?,?,?)',
                               [(identifier, key, data) for key, data in artifacts.items()])

    def exports(self, identifier):
        with self.database.connect() as db:
            return {row['extension']: row['data'] for row in
                    db.execute('SELECT extension,data FROM figure_exports WHERE project=?', (identifier,))}

    def delete(self, identifier):
        with self.database.connect() as db:
            db.execute('DELETE FROM figure_projects WHERE id=?', (identifier,))


def cleanup_figures(db):
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='figure_projects'").fetchone():
        for table in ('figure_files', 'figure_exports'):
            db.execute(f'DELETE FROM {table} WHERE project NOT IN (SELECT id FROM figure_projects)')


def sheet_rows(name, data, sheet_name='', *, expand_headers=False):
    return next(rows for _,rows in workbook_rows(name,data,sheet_name,expand_headers=expand_headers))


def workbook_rows(name,data,sheet_name='',*,expand_headers=False,all_sheets=False):
    suffix = Path(name).suffix.lower()
    if suffix == '.xls':
        import xlrd
        book = xlrd.open_workbook(file_contents=data,formatting_info=expand_headers)
        sheets = book.sheets() if all_sheets and not sheet_name else [
            book.sheet_by_name(sheet_name) if sheet_name else book.sheet_by_index(0)]
        for sheet in sheets:
            rows = [sheet.row_values(i) for i in range(sheet.nrows)]
            if expand_headers:
                for r0,r1,c0,c1 in sheet.merged_cells:
                    expand_matrix_header(rows,r0,r1,c0,c1)
            yield sheet.name,rows
        return
    if suffix == '.xlsx':
        import openpyxl
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=not expand_headers, data_only=True)
        try:
            sheets = book.worksheets if all_sheets and not sheet_name else [
                book[sheet_name] if sheet_name else book.worksheets[0]]
            for sheet in sheets:
                rows = list(sheet.values)
                if expand_headers:
                    rows = [list(row) for row in rows]
                    for span in sheet.merged_cells.ranges:
                        expand_matrix_header(rows,span.min_row-1,span.max_row,span.min_col-1,span.max_col)
                yield sheet.title,rows
            return
        finally:
            book.close()
    for encoding in ('utf-8-sig', 'gb18030', 'utf-16'):
        try:
            source = data.decode(encoding)
            if '\x00' not in source:
                break
        except UnicodeError:
            continue
    else:
        raise ValueError('无法读取表格编码，请另存为 UTF-8 CSV。')
    try:
        dialect = csv.Sniffer().sniff(source[:8192], delimiters=',;\t')
    except csv.Error:
        dialect = 'excel-tab' if '\t' in source else 'excel'
    yield '',list(csv.reader(io.StringIO(source), dialect))


def expand_matrix_header(rows,r0,r1,c0,c1):
    if r1-r0>1 and c1-c0>1:
        return
    value = rows[r0][c0]
    for row in rows[r0:r1]:
        row[c0:c1] = [value]*(c1-c0)


def label(value):
    return '' if value is None else str(value).strip()


def norm(value):
    return re.sub(r'\s+', ' ', label(value).casefold().replace('т', 't'))


def header_columns(row,fields,qpcr=False):
    names = [norm(cell) for cell in row]
    indices = []
    for field in fields:
        name = norm(field)
        if name.startswith('#') and name[1:].isdecimal() and 0 < int(name[1:]) <= len(row):
            indices.append(int(name[1:]) - 1)
        elif name and names.count(name) == 1:
            indices.append(names.index(name))
        elif qpcr and name in ('ct mean','ct'):
            pattern = r'(?:ct|cq|c?\ufffd+|c[ₜᵗ]) mean' if name=='ct mean' else r'(?:cq|c[ₜᵗ])'
            candidates = [i for i,value in enumerate(names) if re.fullmatch(pattern,value)]
            if len(candidates)==1:
                indices.append(candidates[0])
    return indices if len(indices)==len(fields) and len(set(indices))==len(fields) else None


def table_rows(rows, fields, header=0, qpcr=False):
    for offset,row in enumerate(rows):
        if header and offset!=header-1:
            continue
        indices = header_columns(row,fields,qpcr)
        if indices is not None:
            return [(offset + 2 + i, [line[j] if j < len(line) else None for j in indices])
                    for i, line in enumerate(rows[offset + 1:])]
    raise ValueError('没有找到唯一匹配的表头：' + '、'.join(fields) + '。可指定表头行及 #列号。')


def number(value, where):
    try:
        n = float(value)
    except (ValueError, TypeError):
        raise ValueError(f'{where}：数值为空、未定或无效，请核查原表。') from None
    if not math.isfinite(n):
        raise ValueError(f'{where}：需要有限数值。')
    return n


def qpcr_sources(file,options):
    """Find independent repeats without splitting technical wells by position."""
    columns = [options[k] for k in ('sample','gene')]
    raw_column = options.get('raw_ct','Ct').strip()
    ct_columns = [(raw_column,True)] if raw_column else []
    if norm(raw_column) in ('','ct'):
        ct_columns.extend([(options.get('ct','Ct Mean').strip(),False),('Ct',True)])
    repeat_names = {'biological replicate','biological repeat','replicate group','repeat group',
                    'replicate','repeat','生物学重复','生物重复','重复组'}
    sources = {}
    for sheet,rows in workbook_rows(file['name'],file['data'],options.get('sheet',''),all_sheets=True):
        block = 0
        groups,indices = None,None
        footer = False
        for row_number,row in enumerate(rows,1):
            detected = next(((result,raw) for name,raw in ct_columns if name and
                             (result:=header_columns(row,[*columns,name],qpcr=True)) is not None),None)
            if detected is not None and (indices is None or any(not name.startswith('#') for name in
                                                               (*columns,ct_columns[0][0]))):
                block += 1
                indices,raw = detected
                repeat_index = next((i for i,cell in enumerate(row) if norm(cell) in repeat_names),None)
                groups = {}
                footer = False
                continue
            if indices is None or footer:
                continue
            def cell(index):
                return row[index] if index is not None and index<len(row) else None
            sample,gene,ct = map(cell,indices)
            sample,gene = label(sample),label(gene)
            if not any((sample,gene,label(ct))):
                continue
            if not gene and not label(ct) and groups and not any(label(c) for c in rows[row_number-2]):
                footer = True
                continue
            repeat = label(cell(repeat_index))
            if repeat not in groups:
                key = json.dumps([sheet,block,repeat],ensure_ascii=False)
                description = ' · '.join(part for part in (sheet,f'数据块 {block}',repeat) if part)
                groups[repeat] = sources[key] = (description,[])
            groups[repeat][1].append((row_number,sample,gene,ct,raw))
    if not sources:
        raise ValueError(f"{file['name']} 没有找到匹配的 qPCR 表头及数据。请检查样本、基因和 Ct 列名。")
    return sources


def qpcr_repeat_inventory(files,options):
    representatives = qpcr_repeat_representatives(files,options.get('qpcr_sample_repeats',{}))
    return [(file,str(file.get('id',index)),
             {key:(description,list(dict.fromkeys(sample for _,sample,_,_,_ in qpcr_entries(file,options,entries))))
              for key,(description,entries) in qpcr_sources(file,options).items()})
            for index,file in enumerate(files,1)
            if not file.get('path') or representatives[qpcr_file_path(file)]==str(file.get('id',index))]


def qpcr_file_path(file):
    return os.path.normcase(str(Path(file['path']).resolve()))


def qpcr_repeat_representatives(files,configured):
    representatives = {}
    for index,file in enumerate(files,1):
        if file.get('path'):
            identifier = str(file.get('id',index))
            path = qpcr_file_path(file)
            previous = representatives.setdefault(path,identifier)
            if previous not in configured and identifier in configured:
                representatives[path] = identifier
    return representatives


def qpcr_scope(identifier):
    if isinstance(identifier,str) and identifier.startswith('['):
        try:
            parts = json.loads(identifier)
            if isinstance(parts,list) and len(parts)==3 and all(isinstance(part,str) for part in parts):
                return parts
        except ValueError:
            pass
    return str(identifier),None,None


def assigned_qpcr_repeats(file,identifier,choices,assignments):
    repeats = []
    for source,assignment in assignments.items():
        if source not in choices:
            raise ValueError(f"{file['name']}：已设置的工作表或数据块不可用，请重新设置重复组。")
        description,entries = choices[source]
        selected = {row[1] for row in entries}
        grouped = defaultdict(set)
        for sample,placement in assignment.items():
            if (sample not in selected or not isinstance(placement,(list,tuple)) or len(placement)!=2
                    or not all(isinstance(item,str) for item in placement) or not placement[0].strip()):
                raise ValueError(f"{file['name']}：样本 {sample} 的重复组设置无效。")
            repeat,_ = placement
            grouped[repeat].add(sample)
        for repeat,samples in grouped.items():
            key = json.dumps([identifier,source,repeat],ensure_ascii=False)
            choices_for_repeat = {source:(f'{description} · {repeat}',[row for row in entries if row[1] in samples])}
            repeats.append((file,key,source,choices_for_repeat))
    if not repeats:
        raise ValueError(f"{file['name']}：请至少设置一个生物学重复组。")
    return repeats


def qpcr_repeat_sources(files,options,*,strict=True):
    available,used,reserved = {},defaultdict(set),defaultdict(set)
    selections = options.get('qpcr_repeats',{})
    configured = options.get('qpcr_sample_repeats',{})
    representatives = qpcr_repeat_representatives(files,configured)
    for file in files:
        if file.get('path') and str(file.get('id')) in selections:
            reserved[qpcr_file_path(file)].add(selections[str(file['id'])])
    result = {}
    for index,file in sorted(enumerate(files,1),key=lambda item:item[1].get('id',item[0])):
        path = file.get('path')
        source = qpcr_file_path(file) if path else ('attachment',index)
        identifier = str(file.get('id',index))
        if path and representatives[source] in configured and representatives[source]!=identifier:
            result[index] = []
            continue
        if source not in available:
            available[source] = qpcr_sources(file,options)
        choices = available[source]
        assignments = configured.get(identifier)
        if assignments:
            result[index] = assigned_qpcr_repeats(file,identifier,choices,assignments)
            continue
        key = selections.get(identifier)
        if key is None:
            key = next((name for name in choices if name not in used[source] and name not in reserved[source]),None)
        if strict and (key not in choices or key in used[source]):
            raise ValueError(f"{file['name']} 的第 {index} 个素材条目未分配独立重复组。请打开「选择重复组」。")
        used[source].add(key)
        result[index] = [(file,identifier,key,choices)]
    return [repeat for index in range(1,len(files)+1) for repeat in result[index]]


def qpcr_entries(file,options,entries=None):
    if entries is None:
        entries = next(iter(qpcr_sources(file,options).values()))[1]
    for row,sample,gene,ct,raw in entries:
        if not sample or not gene:
            raise ValueError(f"{file['name']} 第 {row} 行缺少样本或基因名。")
        yield row,sample,gene,ct,raw


def qpcr_names(files,options):
    samples,genes = {},{}
    for file,_,key,choices in qpcr_repeat_sources(files,options):
        for _,sample,gene,_,_ in qpcr_entries(file,options,choices[key][1]):
            samples.setdefault(sample,None)
            genes.setdefault(gene,None)
    if not samples or not genes:
        raise ValueError('素材中没有可识别的样本或基因。')
    return list(samples),list(genes)


def qpcr_file_names(files,options,sources=None,*,by_repeat=False):
    dimensions = {}
    if sources is None:
        sources = qpcr_repeat_sources(files,options)
    for file,identifier,key,choices in sources:
        _,entries = choices[key]
        if not by_repeat:
            identifier = qpcr_scope(identifier)[0]
        _,samples,genes = dimensions.setdefault(identifier,(file['name'],{},{}))
        for _,sample,gene,_,_ in qpcr_entries(file,options,entries):
            samples[sample] = genes[gene] = None
    return [(identifier,name,(list(samples),list(genes))) for identifier,(name,samples,genes) in dimensions.items()]


def qpcr_mapping_entry(options,kind,original,file_id=None):
    common = options.get('name_mapping',{}).get(kind,{}).get(original,{})
    mappings = options.get('file_mappings',{})
    attachment = qpcr_scope(file_id)[0]
    inherited = mappings.get(attachment,{}).get(kind,{}).get(original,common)
    return mappings.get(str(file_id),{}).get(kind,{}).get(original,inherited)


def qpcr_name(options,kind,original,file_id=None):
    return label(qpcr_mapping_entry(options,kind,original,file_id).get('name')) or original


def qpcr_required(options,kind):
    if kind=='genes' and options.get('analysis')=='enrichment':
        return ''
    required = options.get('control' if kind=='samples' else 'reference','')
    return required if 'file_mappings' in options else qpcr_name(options,kind,required)


def qpcr_keep(options,kind,original,file_id=None):
    if kind=='samples' and options.get('analysis')=='enrichment':
        pairs = qpcr_saved_enrichment_pairs(options,file_id)
        if pairs is not None:
            aliases = (original,qpcr_name(options,kind,original,file_id))
            if any(name in pairs or name in pairs.values() for name in aliases):
                return True
    return (qpcr_name(options,kind,original,file_id)==qpcr_required(options,kind)
            or qpcr_mapping_entry(options,kind,original,file_id).get('keep',True))


def qpcr_saved_enrichment_pairs(options,file_id):
    control_type = options.get('enrichment_control','input')
    saved = options.get('enrichment_pairs',{}).get(control_type,{})
    return saved.get(str(file_id),saved.get(qpcr_scope(file_id)[0]))


def qpcr_mapping_issues(files,options,*,strict_samples=True):
    issues = {}
    for dimension,kind in enumerate(('samples','genes')):
        sets = {}
        for file_id,_,names in files:
            groups = defaultdict(list)
            for original in names[dimension]:
                if qpcr_keep(options,kind,original,file_id):
                    groups[qpcr_name(options,kind,original,file_id)].append(original)
            sets[file_id] = groups
        compare_names = kind=='genes' or strict_samples and not any(qpcr_scope(file_id)[2] for file_id in sets)
        union = set().union(*sets.values()) if compare_names else set()
        required = qpcr_required(options,kind)
        common = {name for name in union if all(name in names for names in sets.values())}
        for file_id,_,_ in files:
            names = sets[file_id]
            duplicates = {name for name,items in names.items() if len(items)>1}
            flagged = ((set(names)-common) if compare_names else set())|duplicates
            missing = (union|({required} if required and compare_names else set()))-names.keys()
            if flagged or missing:
                issues[file_id,kind] = ({item for name in flagged for item in names[name]},missing,duplicates)
    return issues


def qpcr_wells(wells,raw):
    if not raw or len(wells)<3:
        return wells
    ordered = sorted(wells)
    start = int(ordered[1]-ordered[0]>.5)
    end = len(ordered)-int(ordered[-1]-ordered[-2]>.5)
    return ordered[start:end]


def suggested_enrichment_pairs(samples,control_type):
    def suffix(value,pattern):
        match = re.fullmatch(r'(.+?)[\s_-]*(?:'+pattern+')',value,re.I)
        return match.group(1).casefold() if match else None
    reference_suffix = 'input|i' if control_type=='input' else 'IgG'
    controls = defaultdict(list)
    for name in samples:
        stem = suffix(name,reference_suffix)
        if stem:
            controls[stem].append(name)
    return {name:controls[stem][0] if len(controls[stem])==1 else ''
            for name in samples if (stem:=suffix(name,'IP'))}


def qpcr_values(files,options,sources=None):
    values = []
    enrichment = options.get('analysis')=='enrichment'
    control_type = options.get('enrichment_control','input')
    control = qpcr_required(options,'samples')
    reference = qpcr_required(options,'genes')
    if sources is None:
        sources = qpcr_repeat_sources(files,options)
    for replicate,(file,identifier,key,choices) in enumerate(sources,1):
        attachment,source,repeat = qpcr_scope(identifier)
        assignments = options.get('qpcr_sample_repeats',{}).get(attachment,{}).get(source,{}) if repeat else {}
        grouped = defaultdict(list)
        sample_names,gene_names = {},{}
        description,entries = choices[key]
        for row,original_sample,original_gene,ct,raw in qpcr_entries(file,options,entries):
            if not (qpcr_keep(options,'samples',original_sample,identifier)
                    and qpcr_keep(options,'genes',original_gene,identifier)):
                continue
            sample = qpcr_name(options,'samples',original_sample,identifier)
            gene = qpcr_name(options,'genes',original_gene,identifier)
            if (sample_names.setdefault(sample,original_sample)!=original_sample
                    or gene_names.setdefault(gene,original_gene)!=original_gene):
                raise ValueError(f"{file['name']} 第 {row} 行：同一文件内多个样本或基因映射到了同一个名称。")
            grouped[sample,gene].append(number(ct,f"{file['name']} 第 {row} 行"))
        if not grouped:
            raise ValueError(f"{file['name']} · {description}：此重复组没有保留可计算的样本与基因。"
                             '请检查重复篮子、IP 配对或样本与基因映射。')
        retained = {key:qpcr_wells(wells,raw) for key,wells in grouped.items()}
        means = {key:statistics.mean(wells) for key,wells in retained.items()}
        pair_labels = {}
        if enrichment:
            saved = options.get('enrichment_pairs',{}).get(control_type,{})
            configured = saved.get(identifier)
            aliases = {original:name for name,original in sample_names.items()}
            def mapped(name):
                return aliases.get(name,name)
            if configured is None and attachment in saved:
                configured = {ip:background for ip,background in saved[attachment].items() if mapped(ip) in sample_names}
            if configured is None:
                configured = suggested_enrichment_pairs(aliases,control_type)
            pair_labels = {mapped(ip):mapped(background) for ip,background in configured.items()}
            if not pair_labels:
                raise ValueError(f"{file['name']} 未识别到 IP 配对。请打开「设置 IP 配对」。")
            for ip,background in pair_labels.items():
                if ip not in sample_names or background not in sample_names or ip==background:
                    raise ValueError(f"{file['name']} · {description}：{ip} 缺少有效的 {control_type} 对照。请检查 IP 配对。")
            if set(pair_labels).intersection(pair_labels.values()):
                raise ValueError(f"{file['name']}：同一样本不能同时作为 IP 和对照。")
        plotting = list(pair_labels) if enrichment else list(sample_names)
        conditions = {}
        for sample in plotting:
            placement = assignments.get(sample_names[sample])
            condition = (placement[1].strip() if placement else '') or sample
            if condition in conditions:
                raise ValueError(f"{file['name']} · {description}：条件 {condition} 下有多个样本，请分配到不同的独立重复组。")
            conditions[condition] = sample
        condition_names = {sample:condition for condition,sample in conditions.items()}
        if not enrichment and not options.get('sample_grouping') and (conditions.get(control,control),reference) not in means:
            raise ValueError(f"{file['name']} · {description} 缺少对照组 {control} 的内参 {reference}。")
        for (sample, gene), ct in means.items():
            if (enrichment and sample not in pair_labels) or (not enrichment and gene==reference):
                continue
            condition = condition_names[sample]
            group,treatment,normalizer = sample_group(options,condition,control)
            reference_sample = conditions.get(normalizer,normalizer)
            background = (pair_labels[sample],gene) if enrichment else (sample,reference)
            norm_target = (reference_sample,gene)
            norm_background = (pair_labels.get(reference_sample),gene) if enrichment else (reference_sample,reference)
            if (background not in means or normalizer and (norm_target not in means or norm_background not in means)):
                if enrichment:
                    raise ValueError(f"{file['name']} · {description}：{sample} / {gene} 缺少对应的 {control_type} Ct 或同重复组的归一化配对。")
                raise ValueError(f"{file['name']}：{sample} / {gene} 缺少内参或同文件对照。")
            ref_ct = means[background]
            delta = ct - ref_ct
            dd = delta - (means[norm_target] - means[norm_background]) if normalizer else None
            try:
                relative = 2 ** (-(dd if dd is not None else delta))
                raw_enrichment = 2 ** -delta if enrichment else None
            except OverflowError:
                raise ValueError(f"{file['name']}：Ct 差值超出数值范围，请核查 Ct。") from None
            if not math.isfinite(relative) or relative <= 0:
                raise ValueError(f"{file['name']}：相对水平超出数值范围。")
            values.append(dict(replicate=replicate, source=file['name'], group=cell_name(group,treatment), target=gene,
                               sample_name=condition,normalizer=normalizer,sample_group=group,treatment=treatment,
                               ct=ct, reference_ct=ref_ct, delta_ct=delta, delta_delta_ct=dd, value=relative,
                               technical_rows=len(grouped[sample, gene]),pair=identifier,source_repeat=description,
                               technical_rows_used=len(retained[sample,gene]),
                               technical_wells_removed=len(grouped[sample,gene])-len(retained[sample,gene]),
                               original_sample=sample_names[sample],original_gene=gene_names[gene]))
            if enrichment:
                values[-1].update(analysis='enrichment',ip_control_type=control_type,
                                  ip_control=background[0],raw_enrichment=raw_enrichment)
    if not values:
        raise ValueError('没有可绘制的目标基因。')
    return values


def sample_group(options,sample,default=''):
    if not options.get('sample_grouping'):
        return sample,'',default
    item = options['sample_grouping'].get(sample)
    if not item:
        raise ValueError(f'样本「{sample}」尚未设置分组。')
    if len(item)==2:
        return item[0],sample,item[1]
    return item


def cell_name(group,treatment):
    return f'{group} / {treatment}' if treatment else group


def matrix_number(value):
    if isinstance(value,bool):
        return None
    source = unicodedata.normalize('NFKC',label(value)).replace('−','-')
    if re.fullmatch(r'[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?',source):
        source = source.replace(',','')
    try:
        number = float(source)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def matrix_rows(file,options):
    rows = sheet_rows(file['name'],file['data'],options.get('sheet',''),expand_headers=True)
    width = max(map(len,rows),default=0)
    by_rows = options.get('orientation')=='matrix_rows'
    def at(i,j):
        return rows[i][j] if 0<=i<len(rows) and 0<=j<len(rows[i]) else None
    numeric = [tuple(j for j,cell in enumerate(row) if matrix_number(cell) is not None) for row in rows]
    start = next(((i,columns[0]) for i,columns in enumerate(numeric) if columns),None)
    if start is None:
        raise ValueError(f"{file['name']} 没有可识别的数值矩阵。")
    top,left = start
    bottom = top
    occupied = set()
    while bottom<len(rows) and numeric[bottom]:
        occupied.update(numeric[bottom])
        bottom += 1
    while left>0 and left-1 in occupied:
        left -= 1
    right = left+1
    while right<width and right in occupied:
        right += 1
    while bottom>top+1 and not any(left<=j<right for j in numeric[bottom-1]):
        bottom -= 1
    body = [[at(i,j) for j in range(left,right)] for i in range(top,bottom)]
    column_names = [label(at(top-1,j)) for j in range(left,right)]
    row_names = [label(at(i,left-1)) for i in range(top,bottom)]
    named_rows = [name for name in row_names if name]
    named_columns = [name for name in column_names if name]
    repeated_rows = len(named_rows)!=len(set(named_rows))
    repeated_columns = len(named_columns)!=len(set(named_columns))
    grouped = repeated_rows or repeated_columns or (any(row_names) and any(column_names)) or (
        any(row_names) and not by_rows)
    transposed = (repeated_rows and not repeated_columns) if grouped else by_rows
    transpose_groups = grouped and transposed!=by_rows
    if transposed:
        body = list(zip(*body))
        row_names,column_names = column_names,row_names
        group_prefix,target_prefix = 'row','col'
    else:
        group_prefix,target_prefix = 'col','row'
    # CSV exports may omit a repeated header within a side-by-side block.
    previous = ''
    named_positions = [index for index,name in enumerate(column_names) if name]
    compact_headers = (grouped and len(named_positions)>1 and named_positions[0]==0
                       and named_positions[1]>1
                       and list(range(0,len(column_names),named_positions[1]))==named_positions
                       and len(column_names)%named_positions[1]==0)
    groups = []
    for index,name in enumerate(column_names,1):
        if compact_headers and not name:
            name = previous
        name = name or f'{group_prefix}{index}'
        groups.append(name)
        previous = name
    targets = [name or f'{target_prefix}{index}' for index,name in enumerate(row_names,1)]
    if grouped and len(targets)!=len(set(targets)):
        raise ValueError(f"{file['name']} 的 Grouped 矩阵含重复的类别名称。")
    counters = defaultdict(int)
    subcolumns = []
    for group in groups:
        counters[group]+=1
        subcolumns.append(counters[group])
    values = []
    for index,row in enumerate(body):
        for column,cell in enumerate(row):
            if not label(cell):
                continue
            row_number,col_number = ((top+column+1,left+index+1) if transposed
                                     else (top+index+1,left+column+1))
            value = matrix_number(cell)
            if value is None:
                raise ValueError(f"{file['name']} 第 {row_number} 行第 {col_number} 列的矩阵值无效。")
            group,target = groups[column],targets[index] if grouped else ''
            if transpose_groups:
                group,target = target,group
            values.append(dict(row=row_number,column=col_number,group=group,target=target,value=value,
                               row_name=targets[index],
                               pair_index=subcolumns[column] if grouped else index+1,matrix_grouped=grouped))
    if not values:
        raise ValueError(f"{file['name']} 的数值矩阵为空。")
    return values


def observation_key(row):
    return f"{row['file_index']}:{row['row']}:{row.get('column',1)}"


def matrix_values(files,options,excluded=()):
    rows = [dict(row,source=file['name'],file_index=index,pair=f"{index}:{row['pair_index']}")
            for index,file in enumerate(files,1) for row in matrix_rows(file,options)]
    rows = [row for row in rows if observation_key(row) not in excluded]
    if not rows:
        raise ValueError('排除后没有剩余的表格观测值。')
    formats = {row['matrix_grouped'] for row in rows}
    if len(formats)!=1:
        raise ValueError('请在同一作图项目中使用相同结构的 Column 或 Grouped 矩阵。')
    grouped = True in formats
    readings = defaultdict(list)
    for row in rows:
        readings[row['file_index'],row['target'],row['group']].append(row['value'])
    for row in rows:
        sample = row['group']
        group,treatment,reference = ((sample,'',options.get('matrix_control','')) if grouped else
                                     sample_group(options,sample))
        raw = row['value']
        scale = 1
        if reference:
            controls = readings[row['file_index'],row['target'],reference]
            scale = statistics.mean(controls) if controls else 0
            if not scale:
                raise ValueError(f"「{row['target'] or sample}」的归一化参考「{reference}」缺失或均值为零。")
        value = raw/scale
        if options.get('matrix_percent'):
            value *= 100
        row.update(value=value,group=cell_name(group,treatment),sample_group=group,treatment=treatment,
                   sample_name=sample,normalizer=reference,raw_value=raw)
    return rows


def ordinary_values(files,options,excluded=()):
    if options.get('orientation') in MATRIX_ORIENTATIONS:
        return matrix_values(files,options,excluded)
    values = []
    for file_index,file in enumerate(files,1):
        rows = sheet_rows(file['name'], file['data'], options['sheet'])
        if options.get('orientation') == 'wide':
            header_index = max(0,options['header']-1)
            if header_index >= len(rows):
                raise ValueError('指定的表头行不存在。')
            groups = [(i,label(name)) for i,name in enumerate(rows[header_index]) if label(name)]
            if len(groups)<2 or len({name for _,name in groups})!=len(groups):
                raise ValueError('宽表需要至少两个唯一的组名。组名行仅填写各组名称。')
            for row, cells in enumerate(rows[header_index+1:],header_index+2):
                for i, group in groups:
                    if i<len(cells) and label(cells[i]):
                        values.append(dict(source=file['name'],file_index=file_index,row=row,column=i+1,group=group,target='',pair=f'{file_index}:{row}',
                                           value=number(cells[i],f"第 {row} 行 / {group}")))
            continue
        fields = [options['sample'], options['ct']]
        optional = [(key,options.get(key,'').strip()) for key in ('gene','pair')]
        optional = [(key,name) for key,name in optional if name]
        fields.extend(name for _,name in optional)
        for row, cells in table_rows(rows, fields, options['header']):
            if not any(label(cell) for cell in cells):
                continue
            mapped = {key:label(cell) for (key,_),cell in zip(optional,cells[2:])}
            if not label(cells[0]) or ('gene' in mapped and not mapped['gene']):
                raise ValueError(f"{file['name']} 第 {row} 行缺少组别或类别。")
            values.append(dict(source=file['name'],file_index=file_index, row=row, group=label(cells[0]),
                               target=mapped.get('gene',''),
                               pair=f"{file_index}:{mapped['pair']}" if mapped.get('pair') else '',
                               value=number(cells[1], f"第 {row} 行")))
    values = [row for row in values if observation_key(row) not in excluded]
    if not values:
        raise ValueError('表格没有可绘制的数据。')
    if options['control'] and not any(row['group'] == options['control'] for row in values):
        raise ValueError('没有找到指定的对照组。')
    if options.get('sample_grouping'):
        readings = defaultdict(list)
        for row in values:
            readings[row['file_index'],row['target'],row['group']].append(row['value'])
        for row in values:
            sample = row['group']
            group,treatment,normalizer = sample_group(options,sample)
            raw = row['value']
            if normalizer:
                controls = readings[row['file_index'],row['target'],normalizer]
                baseline = statistics.mean(controls) if controls else 0
                if not baseline:
                    raise ValueError(f'「{sample}」的归一化样本「{normalizer}」缺失或均值为零。')
                row['value'] = raw/baseline
            row.update(group=cell_name(group,treatment),sample_group=group,treatment=treatment,
                       sample_name=sample,normalizer=normalizer,raw_value=raw)
    return values


def beta_fraction(a, b, x):
    # Modified Lentz method for the regularized incomplete beta function.
    tiny = 1e-300
    def nonzero(value):
        return value if abs(value) >= tiny else math.copysign(tiny, value)
    c = 1.0
    d = 1 / nonzero(1 - (a+b)*x/(a+1))
    result = d
    for m in range(1, 301):
        even = m*(b-m)*x/((a+2*m-1)*(a+2*m))
        odd = -(a+m)*(a+b+m)*x/((a+2*m)*(a+2*m+1))
        for term in (even, odd):
            d = 1 / nonzero(1 + term*d)
            c = nonzero(1 + term/c)
            change = d*c
            result *= change
        if abs(change-1) < 3e-14:
            return result
    raise ValueError('t 检验数值未收敛。')


def two_tail(t, df):
    x = df / (df+t*t)
    if not 0 < x < 1:
        return x
    a, b = df/2, 0.5
    factor = math.exp(math.lgamma(a+b)-math.lgamma(a)-math.lgamma(b)+a*math.log(x)+b*math.log1p(-x))
    p = factor*beta_fraction(a,b,x)/a if x < (a+1)/(a+b+2) else 1-factor*beta_fraction(b,a,1-x)/b
    return max(0, min(1, p))


def t_test(a, b, method):
    if min(len(a), len(b)) < 2:
        return None, None, None, '样本不足'
    n, m = len(a), len(b)
    va, vb = statistics.variance(a), statistics.variance(b)
    if method == 'welch':
        left, right = va/n, vb/m
        se2 = left+right
        df = se2**2/(left**2/(n-1)+right**2/(m-1)) if se2 else None
    else:
        df = n+m-2
        se2 = ((n-1)*va+(m-1)*vb)/df*(1/n+1/m)
    if not se2:
        return None, df, None, '组内方差均为零'
    t = (statistics.mean(a)-statistics.mean(b))/math.sqrt(se2)
    return t, df, two_tail(t, df), 'OK'


def comparison_groups(files,options,mode):
    if mode=='qpcr':
        return list(dict.fromkeys(row['group'] for row in qpcr_values(files,options)))
    if options.get('sample_grouping') or options.get('orientation') in MATRIX_ORIENTATIONS:
        return list(dict.fromkeys(row['group'] for row in ordinary_values(files,options)))
    groups = {}
    for file in files:
        rows = sheet_rows(file['name'],file['data'],options['sheet'])
        if mode=='table' and options.get('orientation')=='wide':
            index = max(0,options['header']-1)
            if index>=len(rows):
                raise ValueError('指定的表头行不存在。')
            found = [label(cell) for cell in rows[index]]
        else:
            mapped = table_rows(rows,[options['sample'],options['ct']],options['header'])
            found = [label(cells[0]) for _,cells in mapped]
        groups.update(dict.fromkeys(name for name in found if name))
    return list(groups)


def tests_for(values, method, pairs=None, posthoc=False):
    if method == 'none':
        return []
    targets = defaultdict(lambda: defaultdict(list))
    for row in values:
        targets[row['target']][row['group']].append(row)
    if pairs is not None:
        available = {row['group'] for row in values}
        unknown = {name for pair in pairs for name in pair}-available
        if unknown:
            raise ValueError('数据中找不到以下比较组：'+'、'.join(sorted(unknown)))
    result = []
    for target, groups in targets.items():
        if method in OMNIBUS_TESTS:
            statistic,df,p,status = extended_test(list(groups.values()),method)
            result.append(dict(target=target,group_a=' | '.join(groups),group_b='',n_a=sum(map(len,groups.values())),
                               n_b=None,test=STAT_TESTS[method][1],t=None,statistic=statistic,df=df,
                               p_unadjusted=p,status=status,comparison='omnibus'))
            if posthoc:
                comparisons = list(itertools.combinations(groups,2)) if pairs is None else pairs
                result.extend(posthoc_tests(target,groups,method,comparisons))
            continue
        comparisons = itertools.combinations(groups,2) if pairs is None else pairs
        for a,b in comparisons:
            first,second = groups.get(a,[]),groups.get(b,[])
            if method in ('pooled','welch'):
                statistic,df,p,status = t_test([r['value'] for r in first],[r['value'] for r in second],method)
            else:
                statistic,df,p,status = extended_test([first,second],method)
            if not first or not second:
                status = '该类别缺少所选组'
            result.append(dict(target=target, group_a=a, group_b=b, n_a=len(first), n_b=len(second),
                               test=STAT_TESTS[method][1],t=statistic if method in ('pooled','welch','paired') else None,
                               statistic=statistic,df=df,p_unadjusted=p,status=status,comparison='pairwise'))
    return result


def posthoc_tests(target,groups,method,comparisons):
    from scipy import stats
    names = [group for group,rows in groups.items() if len(rows)>=2]
    estimates = {}
    if len(names)>=2 and method=='anova':
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore',RuntimeWarning)
            output = stats.tukey_hsd(*[[row['value'] for row in groups[name]] for name in names])
        for i,j in itertools.combinations(range(len(names)),2):
            statistic,p = float(output.statistic[i,j]),float(output.pvalue[i,j])
            if math.isfinite(statistic) and math.isfinite(p):
                estimates[frozenset((names[i],names[j]))] = (statistic,None,p)
    elif len(names)>=2:
        raw = []
        for a,b in itertools.combinations(names,2):
            statistic,_,p,status = extended_test([groups[a],groups[b]],'ranksum')
            if status=='OK':
                raw.append((p,frozenset((a,b)),statistic))
        adjusted = 0
        for rank,(p,key,statistic) in enumerate(sorted(raw,key=lambda item:item[0])):
            adjusted = max(adjusted,min(1,(len(raw)-rank)*p))
            estimates[key] = (statistic,p,adjusted)
    results = []
    for a,b in comparisons:
        statistic,raw_p,p = estimates.get(frozenset((a,b)),(None,None,None))
        results.append(dict(target=target,group_a=a,group_b=b,n_a=len(groups.get(a,[])),n_b=len(groups.get(b,[])),
                            test='Tukey HSD' if method=='anova' else 'Mann–Whitney U with Holm correction',
                            statistic=statistic,p_unadjusted=raw_p,p_adjusted=p,
                            status='OK' if p is not None else '该类别样本不足或无法计算',comparison='posthoc'))
    return results


def extended_test(groups,method):
    if len(groups)<2 or any(len(group)<2 for group in groups):
        return None,None,None,'样本不足'
    if method in PAIRED_TESTS:
        maps = [{row.get('pair',''):row['value'] for row in group} for group in groups]
        if any('' in pairs or len(pairs)!=len(group) for pairs,group in zip(maps,groups)):
            return None,None,None,'缺少、重复或不匹配的配对编号'
        if maps[0].keys()!=maps[1].keys() and 'matrix_grouped' not in groups[0][0]:
            return None,None,None,'缺少、重复或不匹配的配对编号'
        matched = [key for key in maps[0] if key in maps[1]]
        if len(matched)<2:
            return None,None,None,'完整配对不足'
        points = [[pairs[key] for key in matched] for pairs in maps]
        if all(a==b for a,b in zip(*points)):
            return None,None,None,'配对差值均为零'
    else:
        points = [[row['value'] for row in group] for group in groups]
    from scipy import stats
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('ignore',RuntimeWarning)
        try:
            if method=='paired':
                output = stats.ttest_rel(*points,alternative='two-sided')
            elif method=='ranksum':
                output = stats.mannwhitneyu(*points,alternative='two-sided',method='auto')
            elif method=='signedrank':
                output = stats.wilcoxon(*points,alternative='two-sided',method='auto')
            elif method=='anova':
                output = stats.f_oneway(*points)
            else:
                output = stats.kruskal(*points)
        except ValueError:
            return None,None,None,'数据无变化或不满足检验条件'
    if not math.isfinite(output.statistic) or not math.isfinite(output.pvalue):
        return None,None,None,'数据无变化或不满足检验条件'
    df = float(output.df) if method=='paired' else len(groups)-1 if method=='kruskal' else None
    return float(output.statistic),df,float(output.pvalue),'OK'


def csv_bytes(rows, fields):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8-sig')


def qpcr_export_csv(csv_data):
    reader = csv.DictReader(io.StringIO(csv_data.decode('utf-8-sig'),newline=''))
    fields = reader.fieldnames or []
    if not {'replicate','group','target','value'}.issubset(fields):
        raise ValueError('作图数据缺少重复组字段，请重新作图后导出。')
    columns,groups,labels = {},{},{}
    for row in reader:
        target,group,replicate = row['target'],row['group'],int(row['replicate'])
        columns.setdefault(target,set()).add(replicate)
        sample = row.get('sample_name') or group
        labels[group,sample] = group if sample in (group,row.get('treatment')) else f'{group} / {sample}'
        groups.setdefault((group,sample),{})[target,replicate] = row['value']
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    header = ['group']
    positions = []
    for target,replicates in columns.items():
        header.extend([target,*(['']*(len(replicates)-1))])
        positions.extend((target,replicate) for replicate in sorted(replicates))
    writer.writerow(header)
    for key,values in groups.items():
        writer.writerow([labels[key],*(values.get(position) for position in positions)])
    writer.writerow([])
    stream.write(csv_data.decode('utf-8-sig'))
    return stream.getvalue().encode('utf-8-sig')
