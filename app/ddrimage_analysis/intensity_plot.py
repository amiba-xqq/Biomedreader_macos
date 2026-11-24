
# %% [cell 0]
from pathlib import Path

input_dir = Path(r'/mnt/NC/image_python_202609/test_tif')

plot_order = 'default'        # 'default' 显示全部组；'1,3,4' 只按此顺序显示原始第1、3、4组
group_colors = 'nature'       # 'nature'（NPG 10色）、'plasma' 等色图，或 '#66CCFF,#FFA07A'
wilcox_pairs = '(1,2),(1,3),(1,4),(1,5)'          # 'all'、'(1,2),(2,3)'；'none' 只绘图不检验

# %% [cell 1]
# 顺序与配对编号始终引用原始 CSV 列；自定义颜色数量应等于实际显示的组数。
# 某种颜色需要单独配置时，填入下面字典；未指定字段继承上面的公共设置。
color_overrides = {}          # 例如 {'red': {'plot_order': '2,1', 'wilcox_pairs': '(1,2)'}}

p_adjust = 'none'             # 原始 P 值；可选 'holm' 对每张图中指定的比较做 Holm 校正
group_width_pt = 8.46         # 相邻组中心距；同一批图固定，参考 test.pdf
y_axis_height_pt = 72.7       # y 轴物理长度固定；额外比较括号增加画布上方空间
random_seed = 42              # 固定抖动位置，重复运行得到相同散点位置
arial_path = None            # 优先自动找 Arial；也可填写 Arial Regular 的 .ttf/.otf 路径
font_fallbacks = ['Liberation Sans', 'DejaVu Sans']  # 系统无 Arial 时依次回退；前者与 Arial 指标兼容

# %% [cell 2]
import ast
import csv
import itertools
import re
from collections import defaultdict
from ddrimage_plot_groups import plot_folders
from ddrimage_output import TEST_PREFIX, plot_statistics

import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib import font_manager, ft2font
from matplotlib.ticker import MaxNLocator
from scipy.stats import mannwhitneyu

# 普通文本模式使 & ~ $ # α 按字面显示，不作为 TeX 或数学表达式处理。
# TrueType 嵌入保留可选取文字；每个标签仅创建一个 Text 对象。
# PDF 导入 Illustrator 后的文本分组仍取决于 AI 版本与字体环境，不保证绝不拆段。
# Linux 通常没有微软 Arial：先逐个检查字体文件的真实 family/style，找不到 Arial
# 时使用 Liberation Sans（Arial 的开放指标兼容字体），最后使用 Matplotlib 自带 DejaVu Sans。
if arial_path is not None:
    font_file = str(Path(arial_path).expanduser().resolve())
    if not Path(font_file).is_file():
        raise FileNotFoundError(f'arial_path 不存在：{font_file}')
    font_manager.fontManager.addfont(font_file)
    face = ft2font.FT2Font(font_file)
    if face.family_name.casefold() != 'arial' or face.style_name.casefold() not in {'regular', 'normal'}:
        raise ValueError(f'arial_path 必须指向 Arial Regular；实际为 {face.family_name} {face.style_name}')
else:
    font_file = None
    for candidate in font_manager.findSystemFonts(fontext='ttf') + font_manager.findSystemFonts(fontext='otf'):
        try:
            face = ft2font.FT2Font(candidate)
            if face.family_name.casefold() == 'arial' and face.style_name.casefold() in {'regular', 'normal'}:
                font_file = candidate
                break
        except (OSError, RuntimeError):
            pass
    if font_file is None:
        for family in font_fallbacks:
            try:
                font_file = font_manager.findfont(
                    font_manager.FontProperties(family=family, style='normal', weight='normal'),
                    fallback_to_default=False)
                break
            except ValueError:
                pass
    if font_file is None:
        raise ValueError('找不到 Arial、Liberation Sans 或 DejaVu Sans；请设置 arial_path')
font_manager.fontManager.addfont(font_file)
font_family = font_manager.FontProperties(fname=font_file).get_name()
print(f'绘图字体：{font_family}（{font_file}）')
glyphs = ft2font.FT2Font(font_file).get_charmap()
mpl.rcParams.update({
    'font.family': font_family, 'font.size': 6, 'font.weight': 'normal', 'font.style': 'normal',
    'axes.labelweight': 'normal', 'axes.titleweight': 'normal',
    'pdf.fonttype': 42, 'ps.fonttype': 42, 'pdf.use14corefonts': False,
    'text.usetex': False, 'text.parse_math': False, 'axes.unicode_minus': False,
    'axes.linewidth': 0.5, 'xtick.major.width': 0.5, 'ytick.major.width': 0.5,
    'savefig.bbox': None,
})
root = Path(input_dir).resolve()
if not root.is_dir():
    raise FileNotFoundError(root)
if p_adjust not in {'none', 'holm'}:
    raise ValueError('p_adjust 只能为 none 或 holm')
if not np.isfinite([group_width_pt, y_axis_height_pt]).all() or min(group_width_pt, y_axis_height_pt) <= 0:
    raise ValueError('物理尺寸必须是正有限数')

# 只扫描母目录直属子文件夹；母目录中本脚本上次生成的 CSV 不会再次被读取。
folders = plot_folders(root, 'intensity')
managed, test_statistics = plot_statistics(root, 'intensity')
tables = defaultdict(dict)
for folder in folders:
    for path in sorted(folder.glob('*.csv'), key=lambda p: p.name.casefold()):
        prefix = (TEST_PREFIX if test_statistics else '') + folder.name
        if managed is not None or _in_place:
            prefix += '_intensity'
        match = re.fullmatch(re.escape(prefix) + r'_channel\d+_([\w-]+)\.csv', path.name, re.I)
        if not match:
            continue
        color = match[1].lower()
        with path.open(encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle)
            if not {'image', 'nucleus_id', 'integrated_density'} <= set(reader.fieldnames or []):
                raise ValueError(f'{path}: 缺少 image/nucleus_id/integrated_density 列')
            values = tables[color].setdefault(folder.name, [])
            for line, row in enumerate(reader, 2):
                try:
                    value = float(row['integrated_density'])
                    if not np.isfinite(value) or value < 0:
                        raise ValueError()
                except (ValueError, TypeError):
                    raise ValueError(f'{path}:{line}: integrated_density 必须是非负有限数值') from None
                values.append(value)
if not tables:
    raise FileNotFoundError('没有找到 子文件夹名_channelN_颜色.csv')

# 每种伪彩得到一张宽表。列按子文件夹名称排序；没有该颜色的组也保留空列。
# 不同组细胞数无需相等；缺失填空白而非 0（0 表示真实的零 foci 细胞）。
# 同一组若有两个同色通道，按文件名顺序追加；务必确认它们代表同一蛋白/指标。
merged_files = []
for color, groups in sorted(tables.items()):
    path = ddrimage_output / f'{color}_integrated_density.csv'
    columns = [groups.get(folder.name, []) for folder in folders]
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow([folder.name for folder in folders])
        writer.writerows(itertools.zip_longest(*columns, fillvalue=''))
    merged_files.append((color, path))
    print(f'已合并 {path.name}；各组细胞数 {[len(v) for v in columns]}')

# 只定义一个绘图函数，其余汇总、参数解释及检验都直接在循环中执行。
def draw_density(data, names, colors, comparisons, output_path, color):
    """固定组间距、固定 y 轴长度的强度散点图；页边距容纳标签和比较线。"""
    n = len(data)
    width, height = n * group_width_pt, y_axis_height_pt
    fig = plt.figure(figsize=((width + 100) / 72, (height + 100) / 72), dpi=144)
    ax = fig.add_axes([50 / (width + 100), 50 / (height + 100),
                       width / (width + 100), height / (height + 100)])
    ax.set_xlim(-0.5, n - 0.5)
    maximum = max((float(v.max()) for v in data if len(v)), default=0)
    ax.set_ylim(0, max(1, maximum * 1.08))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=5, min_n_ticks=2))
    ax.spines[['top', 'right']].set_visible(False)
    ax.tick_params(length=2, width=0.5, pad=2, labelsize=5.5)
    rng = np.random.default_rng(random_seed)
    for i, (values, group_color) in enumerate(zip(data, colors)):
        # markersize 单位 pt；用 plot 而非 scatter 避免将 1 pt 误写成面积单位 pt²。
        ax.plot(i + rng.uniform(-0.22, 0.22, len(values)), values, linestyle='none',
                marker='o', markersize=1, markeredgewidth=0, color=group_color,
                zorder=2, clip_on=False)
        if len(values):
            ax.plot([i - 0.28, i + 0.28], [values.mean()] * 2, color='black',
                    linewidth=1, solid_capstyle='butt', zorder=5)
    ax.set_ylabel(f'Intergrated density\nof nuclear {color} per cell', fontsize=6.5, labelpad=3)
    ax.set_xticks(range(n), names, rotation=45, ha='right', rotation_mode='anchor')
    # 标签均为单行且同角度，可用平行文字行之间的垂直距离检查重叠。
    # 如果 4.5 pt 仍放不下，则明确报错，不能偷偷改变固定组宽或把文字缩得更小。
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    for size in np.arange(5.5, 4.49, -0.25):
        prop = font_manager.FontProperties(fname=font_file, size=size)
        tallest = max(renderer.get_text_width_height_descent(name, prop, False)[1]
                      for name in names) * 72 / fig.dpi
        if tallest + 0.6 <= group_width_pt / np.sqrt(2):
            break
    else:
        plt.close(fig)
        raise ValueError('组宽不足以容纳 4.5 pt 标签，请增大 group_width_pt 后统一重绘')
    for label in ax.get_xticklabels():
        label.set_fontsize(float(size))
    # 一条比较独占一层，按物理 pt 排列在 y 轴上方，不压缩数据轴。
    for level, (left, right, symbol) in enumerate(comparisons):
        y = 1 + (5 + level * 10) / height
        leg = 2 / height
        ax.plot([left, left, right, right], [y-leg, y, y, y-leg],
                transform=ax.get_xaxis_transform(), color='black', lw=0.5, clip_on=False)
        ax.text((left+right)/2, y + 1/height, symbol, ha='center', va='bottom',
                fontsize=5.5, transform=ax.get_xaxis_transform(), clip_on=False)
    # 测量所有文字/括号的外接区域，以真实 pt 调整纸张和页边距；不使用 tight_layout。
    fig.canvas.draw()
    bounds = ax.get_tightbbox(fig.canvas.get_renderer()).transformed(fig.dpi_scale_trans.inverted())
    axis_box = ax.get_window_extent().transformed(fig.dpi_scale_trans.inverted())
    margin = 3 / 72
    page_w, page_h = bounds.width + 2*margin, bounds.height + 2*margin
    fig.set_size_inches(page_w, page_h)
    ax.set_position([(axis_box.x0-bounds.x0+margin)/page_w,
                     (axis_box.y0-bounds.y0+margin)/page_h,
                     width/72/page_w, height/72/page_h])
    fig.savefig(output_path, format='pdf', bbox_inches=None)
    plt.close(fig)

plot_report = []
for color, path in merged_files:
    # 重新读取实际合并 CSV，保证绘图数据与磁盘宽表一致。
    with path.open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.reader(handle))
    names = rows[0]
    n = len(names)
    data = [np.array([float(row[i]) for row in rows[1:] if row[i] != '']) for i in range(n)]
    settings = dict(plot_order=plot_order, group_colors=group_colors, wilcox_pairs=wilcox_pairs)
    override = color_overrides.get(color, {})
    if set(override) - set(settings):
        raise ValueError(f'{color}: color_overrides 中存在未知参数')
    settings.update(override)
    try:
        spec = settings['plot_order'].strip()
        order = (list(range(n)) if spec.lower() == 'default'
                 else [int(x.strip()) - 1 for x in spec.split(',')])
    except (AttributeError, ValueError):
        raise ValueError(f'{color}: plot_order 应为 default，或逗号分隔的原始 CSV 列号') from None
    if not order or len(set(order)) != len(order) or any(i < 0 or i >= n for i in order):
        raise ValueError(f'{color}: plot_order 应为 default，或由 1..{n} 中不重复列号组成，例如 1,3,4')
    shown = set(order)
    shown_n = len(order)
    palette = settings['group_colors'].strip()
    if palette.lower() == 'nature':
        # 常用 NPG/npg 风格10色，不表示 Nature 规定的唯一官方色表。
        colors = ['#E64B35', '#4DBBD5', '#00A087', '#3C5488', '#F39B7F',
                  '#8491B4', '#91D1C2', '#DC0000', '#7E6148', '#B09C85']
        colors = [colors[i % len(colors)] for i in range(shown_n)]
    elif palette in mpl.colormaps:
        colors = mpl.colormaps[palette](np.linspace(0.1, 0.9, shown_n))
    else:
        colors = [x.strip() for x in palette.split(',')]
        if len(colors) != shown_n or not all(mpl.colors.is_color_like(x) for x in colors):
            raise ValueError(f'{color}: 颜色必须为 nature、有效色图，或恰好 {shown_n} 个合法颜色')
    spec = settings['wilcox_pairs'].strip().lower()
    if spec == 'all':
        pairs = list(itertools.combinations(order, 2))
    elif spec == 'none':
        pairs = []
    else:
        try:
            parsed = ast.literal_eval('[' + spec + ']')
            if any(not isinstance(p, tuple) or len(p) != 2 or
                   any(type(x) is not int or not 1 <= x <= n for x in p) or p[0] == p[1]
                   for p in parsed):
                raise ValueError()
            pairs = [tuple(sorted((p[0]-1, p[1]-1))) for p in parsed]
            if len(set(pairs)) != len(pairs) or any(a not in shown or b not in shown for a, b in pairs):
                raise ValueError()
        except (ValueError, SyntaxError, TypeError):
            raise ValueError(f'{color}: wilcox_pairs 应为 all、none 或仅包含显示组的配对，如 (1,3),(3,4)') from None
    # 非配对双侧秩和检验，不是配对符号秩检验。auto 对有并列值的计数使用并列校正。
    # 零细胞组不可检验，标记 N/A；该组仍保留在 CSV 和图中。
    raw_p = []
    for a, b in pairs:
        raw_p.append(float(mannwhitneyu(data[a], data[b], alternative='two-sided', method='auto').pvalue)
                     if len(data[a]) and len(data[b]) else np.nan)
    used_p = np.array(raw_p)
    if p_adjust == 'holm':
        valid = np.flatnonzero(np.isfinite(used_p))
        rank = valid[np.argsort(used_p[valid])]
        used_p[rank] = np.minimum(1, np.maximum.accumulate(
            used_p[rank] * np.arange(len(rank), 0, -1)))
    comparisons, results = [], []
    for (a, b), raw, p in zip(pairs, raw_p, used_p):
        symbol = ('N/A' if not np.isfinite(p) else 'n.s.' if p >= .05 else '*'
                  if p >= .01 else '**' if p >= .001 else '***' if p >= .0001 else '****')
        left, right = sorted((order.index(a), order.index(b)))
        comparisons.append((left, right, symbol))
        results.append([a+1, b+1, names[a], names[b], len(data[a]), len(data[b]), raw, p, symbol])
    # 额外保存确切 P 值，便于核查符号及选择校正方法；原始数据 CSV 不混入统计信息。
    with (ddrimage_output / f'{color}_integrated_density_statistics.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['column1', 'column2', 'group1', 'group2', 'n1', 'n2', 'p_raw', f'p_{p_adjust}', 'symbol'])
        writer.writerows(results)
    text_content = ''.join(names[i] for i in order) + f'Intergrated density\nof nuclear {color} per cell'
    # Newlines and other non-printing layout controls are interpreted by
    # Matplotlib, not rendered as font glyphs.  Arial therefore does not need
    # to contain a glyph for "\n"; only visible characters must be checked.
    missing = sorted({c for c in text_content if c.isprintable() and ord(c) not in glyphs})
    if missing:
        raise ValueError(f'{font_family} 缺少这些字符：{missing}；请更换字体或组名')
    output = ddrimage_output / f'{color}_integrated_density.pdf'
    draw_density([data[i] for i in order], [names[i] for i in order], colors,
              sorted(comparisons, key=lambda item: item[1]-item[0]), output, color)
    plot_report.append(dict(color=color, groups=shown_n, source_groups=n,
                            cells=[len(data[i]) for i in order], pdf=str(output)))
    print(f'已绘图 {output.name}；检验方法：双侧 Mann–Whitney U，P 值校正：{p_adjust}')
print('完成：', plot_report)
