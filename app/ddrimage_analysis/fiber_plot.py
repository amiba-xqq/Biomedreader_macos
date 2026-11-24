
# %% [cell 0]
from pathlib import Path

input_dir = Path(r"/mnt/NC/image_python_202609/fiber_test")  # 母文件夹
analysis_mode = 3                 # 1：复制叉速度；2：后标记/先标记长度比；3：两者
IF_order = "red_green"            # "red_green" 或 "green_red"，按掺入先后顺序
scale = 1.0                      # pixels/μm，必须 > 0
time = 15.0                      # min，后一次标记的掺入时间，必须 > 0

plot_order = "default"        # 原 CSV 列顺序；或按选择顺序指定列号，例如 "1,3"
group_colors = "nature"           # Nature/NPG 常用配色；或 "plasma" 等 matplotlib 色图
# group_colors = "#66CCFF,#FFA07A"  # 自定义颜色数量必须等于组数
# 自定义颜色按最终图中从左至右对应；统计组编号始终指原 CSV 的列编号。
wilcox_pairs = "all"              # 或 "(1,2),(2,3)"；"none" 表示不做比较

# %% [cell 1]
# 1. 依赖：首次运行若缺少包，取消下一行注释安装，然后重新运行。
# %pip install numpy pandas scipy matplotlib
from itertools import combinations
import ast
import re
import warnings
import numpy as np
import csv
from itertools import zip_longest
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib import font_manager, colors
from matplotlib.ticker import MaxNLocator
from scipy.stats import mannwhitneyu
from ddrimage_plot_groups import plot_folders
from ddrimage_output import TEST_PREFIX, plot_statistics

# 参数可以通过应用覆盖。
# 2. 输入参数
p_adjust = "none"                # "none" 或 "holm"；图中使用所选的 P 值

group_width_pt = 8.5              # 每组中心间距的默认最小值，参考 PDF
y_axis_height_pt = 72.0           # 固定纵轴物理长度，不随组数、比较数量变化
x_font_max_pt = 7.0               # 长组名自动缩小，但不低于 4.5 pt
random_seed = 2026               # 固定散点抖动，重跑图形一致
arial_path = ""                  # 可选：Arial Regular 字体文件；留空时按系统字体顺序自动选择
show_plots = True
# 依照用户指定的自定义星号规则，从大 P 值到小 P 值排列。
p_labels = [(0.05, "n.s."), (0.01, "*"), (0.001, "**"), (0.0001, "***"), (0.0, "****")]

# %% [cell 2]
# 3. 检查参数、读取各组 CSV、计算宽表（此节尚不写文件，绘图设置检查通过后统一导出）。
input_dir = Path(input_dir).expanduser().resolve()
if not input_dir.is_dir():
    raise FileNotFoundError(f"母文件夹不存在：{input_dir}")
if analysis_mode not in (1, 2, 3) or IF_order not in ("red_green", "green_red"):
    raise ValueError("analysis_mode 必须是 1/2/3，IF_order 必须是 red_green/green_red")
if not np.isfinite([scale, time]).all() or scale <= 0 or time <= 0:
    raise ValueError("scale、time 必须是大于 0 的有限数")
if p_adjust not in ("none", "holm"):
    raise ValueError("p_adjust 只能是 none 或 holm")
if not np.isfinite([group_width_pt, y_axis_height_pt, x_font_max_pt]).all():
    raise ValueError("图尺寸和字体大小必须是有限数")
if group_width_pt <= 0 or y_axis_height_pt <= 0 or not 4.5 <= x_font_max_pt <= 7:
    raise ValueError("图尺寸须大于 0，x_font_max_pt 须在 4.5 到 7 之间")

folders = plot_folders(input_dir, "fiber")
if not folders:
    raise ValueError("母文件夹中没有子文件夹")
_, test_statistics = plot_statistics(input_dir, "fiber")
files = [folder / (f"{TEST_PREFIX if test_statistics else ''}{folder.name}_fiber.csv") for folder in folders]
missing = [file for file in files if not file.is_file()]
if missing:
    raise FileNotFoundError("以下子文件夹缺少对应 CSV：\n" + "\n".join(map(str, missing)))

groups = {}
for folder, path in zip(folders, files):
    required = ["red_length_px", "green_length_px"]
    records = []
    invalid = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not set(required).issubset(reader.fieldnames or []):
            raise ValueError(f"{path} 必须包含 {required}")
        for row in reader:
            try:
                values = [float(row[key]) for key in required]
                if any(not np.isfinite(value) or value < 0 for value in values):
                    raise ValueError
                records.append(values)
            except (ValueError, TypeError):
                invalid.append(reader.line_num)
    if invalid:
        raise ValueError(f"{path} 有缺失、非数值、无穷或负长度；CSV 行号：{invalid}")
    lengths = np.array(records, dtype=float).reshape(-1, 2)
    groups[folder.name] = dict(zip(required, lengths.T))

tables = {}
if analysis_mode in (1, 3):
    channel = "green_length_px" if IF_order == "red_green" else "red_length_px"
    tables["DNA_replication_speed"] = (
        {name: columns[channel] / scale * 2.59 / time
         for name, columns in groups.items()}, "Replication speed (kb/min)")
if analysis_mode in (2, 3):
    numerator, denominator = (("green_length_px", "red_length_px") if IF_order == "red_green"
                              else ("red_length_px", "green_length_px"))
    ratios = {}
    for name, columns in groups.items():
        valid = columns[denominator] > 0
        if not valid.all():
            warnings.warn(f"{name}：比值分母为 0 的 {int((~valid).sum())} 条 fiber 被排除（速度分析不受影响）")
        ratios[name] = columns[numerator][valid] / columns[denominator][valid]
    ylabel = "Green/Red ratio" if IF_order == "red_green" else "Red/Green ratio"
    tables["Fiber_length_ratio"] = (ratios, ylabel)

names = list(groups)
n = len(names)
if plot_order.strip().lower() == "default":
    order = list(range(n))
else:
    if not re.fullmatch(r"\s*\d+\s*(,\s*\d+\s*)*", plot_order):
        raise ValueError('plot_order 格式应为 "1,3,4"')
    order = [int(v) - 1 for v in plot_order.split(",")]
    if len(order) != len(set(order)) or not all(0 <= i < n for i in order):
        raise ValueError(f"plot_order 的编号必须在 1 到 {n} 之间，且不能重复")
plot_names = [names[i] for i in order]
plot_n = len(plot_names)

if wilcox_pairs.strip().lower() == "all":
    comparisons = list(combinations(order, 2))
elif wilcox_pairs.strip().lower() == "none":
    comparisons = []
else:
    try:
        parsed = ast.literal_eval("[" + wilcox_pairs + "]")
        if not all(isinstance(p, tuple) and len(p) == 2 and
                   all(type(v) is int and 1 <= v <= n for v in p) and p[0] != p[1] for p in parsed):
            raise ValueError()
    except (ValueError, SyntaxError):
        raise ValueError(f'wilcox_pairs 应为 "all"、"none" 或 "(1,2),(2,3)"，编号在 1 到 {n} 之间') from None
    comparisons = list(dict.fromkeys(tuple(sorted((a - 1, b - 1))) for a, b in parsed))
    if any(a not in order or b not in order for a, b in comparisons):
        raise ValueError("wilcox_pairs 只能包含 plot_order 当前展示的组")

nature = ["#E64B35", "#4DBBD5", "#00A087", "#3C5488", "#F39B7F",
          "#8491B4", "#91D1C2", "#DC0000", "#7E6148", "#B09C85"]
if group_colors.strip().lower() == "nature":
    palette = [nature[i % len(nature)] for i in range(plot_n)]
elif group_colors.strip() in mpl.colormaps:
    palette = mpl.colormaps[group_colors.strip()](np.linspace(0.15, 0.85, plot_n))
else:
    palette = [v.strip() for v in group_colors.split(",")]
    if len(palette) != plot_n or not all(colors.is_color_like(v) for v in palette):
        raise ValueError(f"颜色须为 nature、matplotlib 色图名或恰好 {plot_n} 个逗号分隔的颜色")

# 字体优先级：指定 Arial Regular > 系统 Arial > Liberation Sans > DejaVu Sans。
# 用户指定了路径时严格校验；自动搜索时仅接受 Regular/Normal/Roman 字形。
regular_styles = {"regular", "normal", "roman", "book"}
if arial_path:
    font_path = Path(arial_path).expanduser().resolve()
    if not font_path.is_file():
        raise FileNotFoundError(f"arial_path 指定的字体文件不存在：{font_path}")
    face = font_manager.get_font(str(font_path))
    if "arial" not in face.family_name.casefold() or face.style_name.casefold() not in regular_styles:
        raise ValueError(f"arial_path 必须指向 Arial Regular；实际为 {face.family_name} {face.style_name}")
    font_manager.fontManager.addfont(str(font_path))
    selected_font, font_file = face.family_name, str(font_path)
else:
    selected_font = font_file = None
    for family in ("Arial", "Liberation Sans", "DejaVu Sans"):
        try:
            candidate = font_manager.findfont(
                font_manager.FontProperties(family=family, style="normal", weight="normal"),
                fallback_to_default=False)
            face = font_manager.get_font(candidate)
            if face.style_name.casefold() not in regular_styles:
                continue
            selected_font, font_file = face.family_name, candidate
            break
        except ValueError:
            continue
    if selected_font is None:  # Matplotlib 正常安装时不会发生，保留明确错误便于诊断。
        raise RuntimeError("未找到 Arial、Liberation Sans 或 Matplotlib 自带 DejaVu Sans")
# 将具体文件绑定到文字对象；arial_path 与系统同名时也能保证采用指定文件。
selected_font_prop = font_manager.FontProperties(fname=font_file, style="normal", weight="normal")
mpl.rcParams.update({
    "font.family": selected_font, "font.size": 7, "font.weight": "normal", "font.style": "normal",
    "axes.labelweight": "normal", "axes.titleweight": "normal", "axes.linewidth": 0.5,
    "xtick.major.width": 0.5, "ytick.major.width": 0.5,
    "pdf.fonttype": 42, "ps.fonttype": 42, "pdf.use14corefonts": False,
    "text.usetex": False, "text.parse_math": False, "axes.unicode_minus": False,
    "savefig.bbox": None,
})
print(f"绘图字体：{selected_font}（{font_file}）")
print("原始 CSV 列编号：", dict(enumerate(names, 1)))
print("图中从左到右：", plot_names)

# %% [cell 4]
# 4. 导出宽表，并重新读取该 CSV 作图，确保图与保存的数据一致。
# 只定义一个绘图函数；画布采用 pt 计算，再除以 72 转成 matplotlib 的 inch。
def draw_fiber_plot(table, ylabel, pdf_path):
    samples = [table[name] for name in plot_names]
    stats_rows = []
    for a, b in comparisons:
        x, y = table[names[a]], table[names[b]]
        # 空组不能检验，明确标记 n/a，不能当成“不显著”。
        p = float(mannwhitneyu(x, y, alternative="two-sided", method="auto").pvalue) if len(x) and len(y) else np.nan
        stats_rows.append([names[a], names[b], len(x), len(y), p, p])
    if p_adjust == "holm":
        valid_ids = sorted((i for i, row in enumerate(stats_rows) if np.isfinite(row[4])), key=lambda i: stats_rows[i][4])
        running = 0.0
        for rank, i in enumerate(valid_ids):
            running = max(running, (len(valid_ids) - rank) * stats_rows[i][4])
            stats_rows[i][5] = min(1.0, running)

    # 实际测量文字边界，不用按字符数量猜测。短名称用 7 pt，长名称先缩到 4.5 pt。
    measure, measure_ax = plt.subplots(figsize=(2, 2), dpi=144)
    probe_prop = selected_font_prop.copy()
    probe_prop.set_size(x_font_max_pt)
    probe = [measure_ax.text(0, 0, name, rotation=45, ha="right", va="top",
                             fontproperties=probe_prop) for name in plot_names]
    chosen_size, pitch = x_font_max_pt, group_width_pt
    for size in np.linspace(x_font_max_pt, 4.5, 26):
        for label in probe:
            label.set_fontsize(size)
        measure.canvas.draw()
        renderer = measure.canvas.get_renderer()
        widths = [t.get_window_extent(renderer).width * 72 / measure.dpi for t in probe]
        heights = [t.get_window_extent(renderer).height * 72 / measure.dpi for t in probe]
        chosen_size = float(size)
        if max(widths) + 2 <= pitch:
            break
    pitch = max(pitch, max(widths) + 2)  # 全部组采用同一间距，两个模式也会得到相同间距。
    plt.close(measure)

    # 每个比较独占一层，所有括号均位于数据区上方；纵轴保持固定物理长度。
    position = {name: i for i, name in enumerate(plot_names)}
    annotations = []
    for row in stats_rows:
        p = row[5]
        mark = next(label for cutoff, label in p_labels if p >= cutoff) if np.isfinite(p) else "n/a"
        a, b = sorted((position[row[0]], position[row[1]]))
        annotations.append((a, b, mark))
    annotations.sort(key=lambda item: (item[1] - item[0], item[0]))

    left = max(31.0, max(widths) - pitch / 2 + 4)
    bottom = max(12.0, max(heights) + 8)
    right, top = 5.0, 8.0 + 10.0 * len(annotations)
    axis_width = plot_n * pitch
    fig_width, fig_height = left + axis_width + right, bottom + y_axis_height_pt + top
    fig = plt.figure(figsize=(fig_width / 72, fig_height / 72), dpi=144)
    ax = fig.add_axes([left / fig_width, bottom / fig_height, axis_width / fig_width, y_axis_height_pt / fig_height])
    rng = np.random.default_rng(random_seed)
    for i, (values, color) in enumerate(zip(samples, palette)):
        if not len(values):
            warnings.warn(f"{plot_names[i]} 没有有效值：保留该列和组位置，但不画点和均值")
            continue
        # scatter 的 s 单位是 pt²：s=1 对应直径 1 pt；不画描边，以免点变粗。
        jitter = rng.uniform(-0.27, 0.27, len(values))
        ax.scatter(i + jitter, values, s=1, marker="o", c=[color], linewidths=0, edgecolors="none", zorder=2)
        ax.hlines(values.mean(), i - 0.32, i + 0.32, color="black", linewidth=1, zorder=4)
    ax.set_xlim(-0.5, plot_n - 0.5)
    maximum = max((float(v.max()) for v in samples if len(v)), default=0)
    ax.set_ylim(0, maximum * 1.08 if maximum > 0 else 1)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=4, min_n_ticks=3))
    ylabel_prop = selected_font_prop.copy()
    ylabel_prop.set_size(6.5)
    xlabels_prop = selected_font_prop.copy()
    xlabels_prop.set_size(chosen_size)
    ytick_prop = selected_font_prop.copy()
    ytick_prop.set_size(5.5)
    annotation_prop = selected_font_prop.copy()
    annotation_prop.set_size(6)
    ax.set_ylabel(ylabel, labelpad=3, fontproperties=ylabel_prop)
    ax.set_xticks(range(plot_n), plot_names, rotation=45, ha="right", rotation_mode="anchor",
                  fontproperties=xlabels_prop)
    ax.tick_params(axis="y", labelsize=5.5, width=0.5, length=2, pad=2)
    ax.tick_params(axis="x", width=0.5, length=2, pad=2)
    plt.setp(ax.get_yticklabels(), fontproperties=ytick_prop)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(False)
    ax.yaxis.get_offset_text().set_fontproperties(ytick_prop)
    transform = ax.get_xaxis_transform()  # x 使用组位置，y 使用纵轴的 0..1 比例。
    for level, (a, b, mark) in enumerate(annotations):
        y = 1 + (4 + level * 10) / y_axis_height_pt
        h = 2 / y_axis_height_pt
        ax.plot([a, a, b, b], [y, y + h, y + h, y], transform=transform,
                color="black", linewidth=0.5, clip_on=False)
        ax.text((a + b) / 2, y + h + 0.5 / y_axis_height_pt, mark, transform=transform,
                ha="center", va="bottom", fontproperties=annotation_prop, clip_on=False)

    # 测量纵轴标题和刻度，必要时增加左边距；只增加画布，不缩小纵轴或改变组间距。
    fig.canvas.draw()
    bbox = ax.get_tightbbox(fig.canvas.get_renderer())
    extra = max(0, 3 - bbox.x0 * 72 / fig.dpi)
    if extra:
        left += extra
        fig_width += extra
        fig.set_size_inches(fig_width / 72, fig_height / 72)
        ax.set_position([left / fig_width, bottom / fig_height, axis_width / fig_width, y_axis_height_pt / fig_height])
    fig.savefig(pdf_path, format="pdf", bbox_inches=None, facecolor="white")
    print(f"已导出 {pdf_path.name}；组间距 {pitch:.1f} pt；纵轴 {y_axis_height_pt:.1f} pt；组名字号 {chosen_size:.1f} pt")
    if stats_rows:
        print(f"双侧 Wilcoxon 秩和检验；P 值校正：{p_adjust}；图中标注依据 p_used")
        for row in stats_rows:
            print(*row, sep="\t")
    if show_plots:
        plt.show()
    plt.close(fig)
    return stats_rows

statistics = {}  # 同时保留在变量中，便于 notebook 内继续检查或分析。
for prefix, (wide_table, ylabel) in tables.items():
    if any(not np.isfinite(column).all() for column in wide_table.values()):
        raise ValueError(f"{prefix} 计算溢出，请检查长度和参数")
    csv_path = ddrimage_output / f"{prefix}.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(wide_table)
        writer.writerows(zip_longest(*wide_table.values(), fillvalue=""))
    stats_rows = draw_fiber_plot(wide_table, ylabel, csv_path.with_suffix(".pdf"))
    stats_path = ddrimage_output / f"{prefix}_wilcoxon.csv"
    with stats_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["group_1", "group_2", "n_1", "n_2", "p_raw", "p_used", "significance", "p_adjust"])
        for group_1, group_2, n_1, n_2, raw, used in stats_rows:
            label = next(mark for cutoff, mark in p_labels if used >= cutoff) if np.isfinite(used) else "n/a"
            writer.writerow([group_1, group_2, n_1, n_2, format(raw, ".17g") if np.isfinite(raw) else "",
                             format(used, ".17g") if np.isfinite(used) else "", label, p_adjust])
    statistics[prefix] = stats_rows
    print(f"已导出 {stats_path.name}；比较数量：{len(stats_rows)}")
    print(f"已导出 {csv_path.name}；各组有效数量：{[len(column) for column in wide_table.values()]}")

# %% [cell 5]

