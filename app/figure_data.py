"""Local figure projects and auditable calculations."""
import csv
import io
import itertools
import json
import math
import os
import re
import statistics
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
        paths = [path.absolute() for path in paths]
        with self.database.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            known = {os.path.normcase(row['path']) for row in db.execute(
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
        path = path.absolute()
        with self.database.connect() as db:
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


def sheet_rows(name, data, sheet_name=''):
    suffix = Path(name).suffix.lower()
    if suffix == '.xls':
        import xlrd
        book = xlrd.open_workbook(file_contents=data)
        sheet = book.sheet_by_name(sheet_name) if sheet_name else book.sheet_by_index(0)
        return [sheet.row_values(i) for i in range(sheet.nrows)]
    if suffix == '.xlsx':
        import openpyxl
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            sheet = book[sheet_name] if sheet_name else book.worksheets[0]
            return list(sheet.values)
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
    return list(csv.reader(io.StringIO(source), dialect))


def label(value):
    return '' if value is None else str(value).strip()


def norm(value):
    return re.sub(r'\s+', ' ', label(value).casefold().replace('т', 't'))


def table_rows(rows, fields, header=0, qpcr=False):
    for offset, row in enumerate(rows):
        if header and offset != header - 1:
            continue
        names = [norm(cell) for cell in row]
        indices = []
        for field in fields:
            name = norm(field)
            if name.startswith('#') and name[1:].isdecimal() and 0 < int(name[1:]) <= len(row):
                indices.append(int(name[1:]) - 1)
            elif name and names.count(name) == 1:
                indices.append(names.index(name))
            elif qpcr and name == 'ct mean':
                candidates = [i for i, value in enumerate(names)
                              if re.fullmatch(r'(?:ct|cq|c?\ufffd+|c[ₜᵗ]) mean', value)]
                if len(candidates) == 1:
                    indices.append(candidates[0])
            elif qpcr and name=='ct':
                candidates = [i for i,value in enumerate(names)
                              if re.fullmatch(r'(?:cq|c[ₜᵗ])',value)]
                if len(candidates)==1:
                    indices.append(candidates[0])
        if len(indices) == len(fields) and len(set(indices)) == len(fields):
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


def qpcr_entries(file,options):
    rows = sheet_rows(file['name'],file['data'],options['sheet'])
    columns = [options[k] for k in ('sample','gene')]
    raw_column = options.get('raw_ct','Ct').strip()
    raw = bool(raw_column)
    if raw:
        try:
            selected = table_rows(rows,[*columns,raw_column],options['header'],qpcr=True)
        except ValueError:
            if norm(raw_column)!='ct':
                raise
            raw = False
    if not raw:
        selected = table_rows(rows,[*columns,options['ct']],options['header'],qpcr=True)
    observed = False
    for row,(sample,gene,ct) in selected:
        sample,gene = label(sample),label(gene)
        if not sample or not gene:
            if not any((sample,gene,label(ct))):
                continue
            # An instrument footer follows a blank line after valid data.
            if not gene and not label(ct) and observed and not any(label(cell) for cell in rows[row-2]):
                break
            raise ValueError(f"{file['name']} 第 {row} 行缺少样本或基因名。")
        observed = True
        yield row,sample,gene,ct,raw


def qpcr_names(files,options):
    samples,genes = {},{}
    for file in files:
        for _,sample,gene,_,_ in qpcr_entries(file,options):
            samples.setdefault(sample,None)
            genes.setdefault(gene,None)
    if not samples or not genes:
        raise ValueError('素材中没有可识别的样本或基因。')
    return list(samples),list(genes)


def qpcr_file_names(files,options):
    return [(str(file['id']),file['name'],qpcr_names([file],options)) for file in files]


def qpcr_mapping_entry(options,kind,original,file_id=None):
    common = options.get('name_mapping',{}).get(kind,{}).get(original,{})
    return options.get('file_mappings',{}).get(str(file_id),{}).get(kind,{}).get(original,common)


def qpcr_name(options,kind,original,file_id=None):
    return label(qpcr_mapping_entry(options,kind,original,file_id).get('name')) or original


def qpcr_required(options,kind):
    required = options['control' if kind=='samples' else 'reference']
    return required if 'file_mappings' in options else qpcr_name(options,kind,required)


def qpcr_keep(options,kind,original,file_id=None):
    return (qpcr_name(options,kind,original,file_id)==qpcr_required(options,kind)
            or qpcr_mapping_entry(options,kind,original,file_id).get('keep',True))


def qpcr_mapping_issues(files,options):
    issues = {}
    for dimension,kind in enumerate(('samples','genes')):
        sets = {}
        for file_id,_,names in files:
            groups = defaultdict(list)
            for original in names[dimension]:
                if qpcr_keep(options,kind,original,file_id):
                    groups[qpcr_name(options,kind,original,file_id)].append(original)
            sets[file_id] = groups
        union = set().union(*sets.values())
        required = qpcr_required(options,kind)
        common = {name for name in union if all(name in names for names in sets.values())}
        for file_id,_,_ in files:
            names = sets[file_id]
            duplicates = {name for name,items in names.items() if len(items)>1}
            flagged = (set(names)-common)|duplicates
            missing = (union|{required})-names.keys()
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


def qpcr_values(files, options):
    values = []
    control = qpcr_required(options,'samples')
    reference = qpcr_required(options,'genes')
    for replicate,file in enumerate(files,1):
        file_id = file.get('id')
        grouped = defaultdict(list)
        sample_names,gene_names = {},{}
        for row,original_sample,original_gene,ct,raw in qpcr_entries(file,options):
            if not (qpcr_keep(options,'samples',original_sample,file_id)
                    and qpcr_keep(options,'genes',original_gene,file_id)):
                continue
            sample = qpcr_name(options,'samples',original_sample,file_id)
            gene = qpcr_name(options,'genes',original_gene,file_id)
            if (sample_names.setdefault(sample,original_sample)!=original_sample
                    or gene_names.setdefault(gene,original_gene)!=original_gene):
                raise ValueError(f"{file['name']} 第 {row} 行：同一文件内多个样本或基因映射到了同一个名称。")
            grouped[sample,gene].append(number(ct,f"{file['name']} 第 {row} 行"))
        if not grouped:
            raise ValueError(f"{file['name']} 没有可用的 Ct 数据。")
        retained = {key:qpcr_wells(wells,raw) for key,wells in grouped.items()}
        means = {key:statistics.mean(wells) for key,wells in retained.items()}
        if not options.get('sample_grouping') and (control, reference) not in means:
            raise ValueError(f"{file['name']} 缺少对照组 {control} 的内参 {reference}。")
        for (sample, gene), ct in means.items():
            if gene == reference:
                continue
            group,treatment,normalizer = sample_group(options,sample,control)
            if ((sample,reference) not in means
                    or normalizer and any(pair not in means for pair in ((normalizer,gene),(normalizer,reference)))):
                raise ValueError(f"{file['name']}：{sample} / {gene} 缺少内参或同文件对照。")
            ref_ct = means[sample, reference]
            delta = ct - ref_ct
            dd = delta - (means[normalizer, gene] - means[normalizer, reference]) if normalizer else None
            try:
                relative = 2 ** (-(dd if dd is not None else delta))
            except OverflowError:
                raise ValueError(f"{file['name']}：ΔΔCt 超出数值范围，请核查 Ct。") from None
            if not math.isfinite(relative) or relative <= 0:
                raise ValueError(f"{file['name']}：相对水平超出数值范围。")
            values.append(dict(replicate=replicate, source=file['name'], group=cell_name(group,treatment), target=gene,
                               sample_name=sample,normalizer=normalizer,sample_group=group,treatment=treatment,
                               ct=ct, reference_ct=ref_ct, delta_ct=delta, delta_delta_ct=dd, value=relative,
                               technical_rows=len(grouped[sample, gene]),pair=str(replicate),
                               technical_rows_used=len(retained[sample,gene]),
                               technical_wells_removed=len(grouped[sample,gene])-len(retained[sample,gene]),
                               original_sample=sample_names[sample],original_gene=gene_names[gene]))
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


def ordinary_values(files, options):
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
                        values.append(dict(source=file['name'],file_index=file_index,row=row,group=group,target='',pair=f'{file_index}:{row}',
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
    if options.get('sample_grouping'):
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
        if any('' in pairs or len(pairs)!=len(group) for pairs,group in zip(maps,groups)) or maps[0].keys()!=maps[1].keys():
            return None,None,None,'缺少、重复或不匹配的配对编号'
        points = [[pairs[key] for key in maps[0]] for pairs in maps]
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
