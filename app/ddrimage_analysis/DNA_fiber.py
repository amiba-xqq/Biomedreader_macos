
# %% [cell 0]
from pathlib import Path
parent_folder = Path(r"/mnt/NC/image_python_202609/fiber_test")  # 改为母文件夹路径
fiber_mode = "test"                              # "test" 或 "all"
green_contrast = 1.0
green_brightness = 50.0
green_threshold = 80.0
red_contrast = 1.0
red_brightness = 20.0
red_threshold = 80.0

# %% [cell 1]
# 首次使用时取消下一行的注释；安装到当前 Notebook 内核。
# %pip install numpy scipy scikit-image tifffile pillow matplotlib imagecodecs
import csv
import json
import os
import numpy as np
import tifffile
from scipy import ndimage as ndi
from scipy.spatial import cKDTree
from skimage.measure import label, regionprops
from skimage.draw import line
from PIL import Image, ImageDraw, ImageFont

# %% [cell 2]
intensity_max = 255.0

green_window = (0, 255)             # 将每张 green 通道的 min..max 线性映射到 low..high
red_window = (0, 255)               # 将每张 red 通道的 min..max 线性映射到 low..high
pre_ligation_overlap_threshold = 0.7  # 连通前跨通道删除阈值；严格使用“重叠率 > 此值”

min_object_area = 5                     # 删除面积过小的初始对象
max_object_area_before_ligation = 400   # 连通前删除面积超过此值的初始对象
edge_exclusion_px = 5                   # 删除距任一图片边缘不超过 5 px 的 fiber
fragment_area_range = (6, 500)         # 可参与连接的初始片段面积，px²
fragment_min_aspect = 1.1              # 长/宽；圆形斑点不参与连接
ligation_gap_px = 5                  # 同通道连接的最大间隙
ligation_search_radius_px = 15     # 仅检索端点周围此半径内的候选；必须 >= ligation_gap_px + 1
ligation_angle_deg = 30.0               # 两片段主轴、端点连线与主轴的角度上限
ligation_axis_change_deg = 15.0         # 合并主轴相对当前主轴及最初种子轴的角度上限
ligation_lateral_px = 3             # 端点连线横向偏移上限
ligation_short_gap_px = 2            # 短间隙允许像素离散造成较大的端点连线角度

overlap_range = (0.02, 0.55)
pair_gap_px = 6.0
pair_angle_deg = 30
pair_lateral_px = 4.0
red_length_range = (5, 60)
green_length_range = (5, 60)
pair_at_least_one_length_min = 15     # red/green 至少一条达到此主轴长度；避免两条都很短
red_width_range = (1, 6)
green_width_range = (1, 6)

font_size = 12
box_line_width = 2

# %% [cell 3]
def stretch_to_window(channel, window):
    """将单通道当前图片的 min..max 线性映射到指定 low..high。"""
    low, high = map(float, window)
    channel_min, channel_max = float(channel.min()), float(channel.max())
    if channel_max <= channel_min:
        return np.full(channel.shape, low, dtype=np.float32)
    normalized = (channel.astype(np.float32) - channel_min) / (channel_max - channel_min)
    return normalized * (high - low) + low


def axis_angle(u, v):
    """无向主轴夹角，单位为度；u、v 都是单位向量。"""
    return float(np.degrees(np.arccos(np.clip(abs(np.dot(u, v)), 0, 1))))


def geometry(xy):
    """xy 为 (x, y) 像素坐标；返回 PCA 轴、旋转矩形、实际端点和投影长宽。"""
    xy = np.asarray(xy, dtype=float)
    center = xy.mean(axis=0)
    _, vectors = np.linalg.eigh((xy - center).T @ (xy - center))
    u = vectors[:, -1]
    v = np.array([-u[1], u[0]])
    basis = np.column_stack([u, v])
    projected = (xy - center) @ basis
    # 加入单位正方形像素沿各轴的半宽，避免单像素粗纤维的宽度被算成零。
    half_pixel = np.abs(basis).sum(axis=0) / 2
    lo, hi = projected.min(axis=0) - half_pixel, projected.max(axis=0) + half_pixel
    corners = np.array([[lo[0], lo[1]], [hi[0], lo[1]],
                        [hi[0], hi[1]], [lo[0], hi[1]]]) @ basis.T + center
    tips = []
    for extreme in (projected[:, 0].min(), projected[:, 0].max()):
        tip_xy = xy[np.abs(projected[:, 0] - extreme) <= 1.0]
        tips.append(tip_xy[np.argmin(np.abs((tip_xy - center) @ v))])
    return dict(xy=xy, center=center, u=u, v=v, length=float(hi[0] - lo[0]),
                width=float(hi[1] - lo[1]), corners=corners, tips=np.array(tips),
                bbox=np.array([xy.min(axis=0), xy.max(axis=0)]))


def ligate(binary):
    """去噪、删除过大对象，以端点空间索引进行局部连接，最后去除边缘 fiber。"""
    labels = label(binary, connectivity=2)
    objects, eligible, anchors = {}, set(), {}
    for region in regionprops(labels):
        # 大对象在连接前直接删除，而不只是禁止其参与连接。
        if region.area < min_object_area or region.area > max_object_area_before_ligation:
            labels[tuple(region.coords.T)] = 0
            continue
        k = region.label
        obj = geometry(region.coords[:, ::-1])
        objects[k] = obj
        if (fragment_area_range[0] <= region.area <= fragment_area_range[1]
                and obj['length'] / obj['width'] >= fragment_min_aspect):
            eligible.add(k)
            anchors[k] = obj['u'].copy()

    # 动态端点网格：连接成功后只更新两个相关对象，不重建全局 KD-tree。
    cell_size = float(ligation_search_radius_px)
    endpoint_grid, object_cells = {}, {}

    def grid_cells(obj):
        return {tuple(np.floor(tip / cell_size).astype(int)) for tip in obj['tips']}

    def add_to_grid(k):
        cells = grid_cells(objects[k])
        object_cells[k] = cells
        for cell in cells:
            endpoint_grid.setdefault(cell, set()).add(k)

    def remove_from_grid(k):
        for cell in object_cells.pop(k, ()):
            bucket = endpoint_grid[cell]
            bucket.discard(k)
            if not bucket:
                del endpoint_grid[cell]

    for k in eligible:
        add_to_grid(k)

    while True:
        did_merge = False
        ordered = sorted(eligible, key=lambda k: (-objects[k]['length'], k))
        for i in ordered:
            a = objects[i]
            nearby = set()
            for tip in a['tips']:
                cell_x, cell_y = np.floor(tip / cell_size).astype(int)
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        nearby.update(endpoint_grid.get((cell_x + dx, cell_y + dy), ()))
            nearby.discard(i)

            candidates = []
            for j in sorted(nearby):
                if j not in eligible:
                    continue
                b = objects[j]
                if b['length'] > a['length']:
                    continue
                tip_distances = np.linalg.norm(a['tips'][:, None, :] - b['tips'][None, :, :], axis=2)
                if tip_distances.min() > ligation_search_radius_px:
                    continue
                box_gap = np.maximum(0, np.maximum(a['bbox'][0] - b['bbox'][1],
                                                   b['bbox'][0] - a['bbox'][1]))
                if np.linalg.norm(box_gap) > ligation_gap_px + 1:
                    continue
                if axis_angle(a['u'], b['u']) > ligation_angle_deg:
                    continue
                for ia, pa in enumerate(a['tips']):
                    for ib, pb in enumerate(b['tips']):
                        delta = pb - pa
                        dist = np.linalg.norm(delta)
                        if dist == 0 or dist - 1 > ligation_gap_px:
                            continue
                        out_a = a['u'] * (-1 if ia == 0 else 1)
                        out_b = b['u'] * (-1 if ib == 0 else 1)
                        if np.dot(delta, out_a) <= 0 or np.dot(-delta, out_b) <= 0:
                            continue
                        connector_angle = max(axis_angle(delta / dist, a['u']),
                                              axis_angle(delta / dist, b['u']))
                        lateral_offset = max(abs(np.dot(delta, a['v'])),
                                             abs(np.dot(delta, b['v'])))
                        if (lateral_offset > ligation_lateral_px
                                or (dist - 1 > ligation_short_gap_px
                                    and connector_angle > ligation_angle_deg)):
                            continue
                        candidates.append((dist, j, pa, pb))

            for _, j, pa, pb in sorted(candidates, key=lambda c: (c[0], c[1])):
                rr, cc = line(int(pa[1]), int(pa[0]), int(pb[1]), int(pb[0]))
                near_r = np.clip(rr[:, None] + np.repeat([-1, 0, 1], 3), 0, labels.shape[0] - 1)
                near_c = np.clip(cc[:, None] + np.tile([-1, 0, 1], 3), 0, labels.shape[1] - 1)
                if not np.isin(labels[near_r, near_c], [0, i, j]).all():
                    continue
                xy = np.unique(np.vstack([a['xy'], objects[j]['xy'], np.column_stack([cc, rr])]), axis=0)
                merged = geometry(xy)
                if (axis_angle(merged['u'], a['u']) > ligation_axis_change_deg
                        or axis_angle(merged['u'], anchors[i]) > ligation_axis_change_deg):
                    continue
                remove_from_grid(i)
                remove_from_grid(j)
                labels[xy[:, 1].astype(int), xy[:, 0].astype(int)] = i
                objects[i] = merged
                del objects[j]
                eligible.remove(j)
                del anchors[j]
                add_to_grid(i)
                did_merge = True
                break
            if did_merge:
                break
        if not did_merge:
            break

    height, width = labels.shape
    border = edge_exclusion_px
    for k, obj in list(objects.items()):
        x0, y0 = obj['bbox'][0]
        x1, y1 = obj['bbox'][1]
        if x0 <= border or y0 <= border or x1 >= width - 1 - border or y1 >= height - 1 - border:
            xy = obj['xy'].astype(int)
            labels[xy[:, 1], xy[:, 0]] = 0
            del objects[k]
    return labels, objects

def find_pairs(red_labels, green_labels, reds, greens):
    """用中心空间索引预筛局部 red/green 对象，再执行完整几何条件与双向唯一性检查。"""
    red_neighbors = {k: [] for k in reds}
    green_neighbors = {k: [] for k in greens}
    accepted_edges = {}
    if not reds or not greens:
        return []

    green_ids = list(greens)
    green_centers = np.array([greens[k]['center'] for k in green_ids])
    green_radii = np.array([
        np.linalg.norm(greens[k]['xy'] - greens[k]['center'], axis=1).max()
        for k in green_ids])
    max_green_radius = float(green_radii.max())
    green_center_tree = cKDTree(green_centers)
    green_pixel_trees = {}  # 仅在无重叠候选真正需要最近像素距离时创建。

    for ri, r in reds.items():
        red_radius = float(np.linalg.norm(r['xy'] - r['center'], axis=1).max())
        search_radius = red_radius + max_green_radius + pair_gap_px + 1
        nearby_positions = green_center_tree.query_ball_point(r['center'], search_radius)
        for position in sorted(nearby_positions, key=lambda p: green_ids[p]):
            gi = green_ids[position]
            g = greens[gi]
            box_gap = np.maximum(0, np.maximum(r['bbox'][0] - g['bbox'][1],
                                               g['bbox'][0] - r['bbox'][1]))
            if box_gap @ box_gap > (pair_gap_px + 1) ** 2 or axis_angle(r['u'], g['u']) > pair_angle_deg:
                continue
            offset = g['center'] - r['center']
            if max(abs(offset @ r['v']), abs(offset @ g['v'])) > pair_lateral_px:
                continue
            rxy = r['xy'].astype(int)
            intersection = int(np.count_nonzero(green_labels[rxy[:, 1], rxy[:, 0]] == gi))
            overlap = intersection / min(len(r['xy']), len(g['xy']))
            gap_axis_length = 0.0
            if intersection:
                edge_ok = overlap_range[0] <= overlap <= overlap_range[1]
                overlap_xy = rxy[green_labels[rxy[:, 1], rxy[:, 0]] == gi]
                projected = overlap_xy @ g['u']
                overlap_axis_length = float(np.ptp(projected) + np.abs(g['u']).sum())
            else:
                if gi not in green_pixel_trees:
                    green_pixel_trees[gi] = cKDTree(g['xy'])
                gap = max(0.0, float(green_pixel_trees[gi].query(r['xy'])[0].min()) - 1.0)
                if gap > pair_gap_px:
                    continue
                edge_ok = True
                overlap_axis_length = 0.0
                half_pixel = np.abs(g['u']).sum() / 2
                red_projection = r['xy'] @ g['u']
                green_projection = g['xy'] @ g['u']
                red_interval = (red_projection.min() - half_pixel, red_projection.max() + half_pixel)
                green_interval = (green_projection.min() - half_pixel, green_projection.max() + half_pixel)
                gap_axis_length = float(max(0.0, green_interval[0] - red_interval[1],
                                            red_interval[0] - green_interval[1]))
            red_neighbors[ri].append(gi)
            green_neighbors[gi].append(ri)
            if edge_ok:
                accepted_edges[(ri, gi)] = (overlap_axis_length, gap_axis_length)

    result = []
    for ri, gi in sorted(accepted_edges):
        if len(red_neighbors[ri]) != 1 or len(green_neighbors[gi]) != 1:
            continue
        r, g = reds[ri], greens[gi]
        if (red_length_range[0] <= r['length'] <= red_length_range[1]
                and green_length_range[0] <= g['length'] <= green_length_range[1]
                and max(r['length'], g['length']) >= pair_at_least_one_length_min
                and red_width_range[0] <= r['width'] <= red_width_range[1]
                and green_width_range[0] <= g['width'] <= green_width_range[1]):
            overlap_axis_length, gap_axis_length = accepted_edges[(ri, gi)]
            result.append((ri, gi, overlap_axis_length, gap_axis_length))
    return result

# %% [cell 4]
parent_folder = Path(parent_folder).expanduser().resolve()
if not parent_folder.is_dir():
    raise FileNotFoundError(f"母文件夹不存在：{parent_folder}")
if fiber_mode not in ("test", "all"):
    raise ValueError('fiber_mode 必须为 "test" 或 "all"')

for name in ('green_window', 'red_window'):
    window = globals()[name]
    if (len(window) != 2 or not np.isfinite(window).all()
            or not 0 <= window[0] < window[1] <= 255):
        raise ValueError(f'{name} 必须满足 0 <= low < high <= 255')

for name in ('fragment_area_range', 'overlap_range', 'red_length_range',
             'green_length_range', 'red_width_range', 'green_width_range'):
    bounds = globals()[name]
    if len(bounds) != 2 or not np.isfinite(bounds).all() or not 0 <= bounds[0] <= bounds[1]:
        raise ValueError(f"{name} 必须是递增的非负有限数范围")
if overlap_range[1] > 1 or min_object_area < 2 or intensity_max <= 0:
    raise ValueError("重叠率不超过 1，min_object_area 至少为 2，intensity_max 必须大于 0")
if not 0 <= pre_ligation_overlap_threshold <= 1:
    raise ValueError("pre_ligation_overlap_threshold 必须在 0–1")
for name in ('green_contrast', 'red_contrast', 'ligation_gap_px', 'ligation_lateral_px',
             'pair_gap_px', 'pair_lateral_px', 'fragment_min_aspect', 'edge_exclusion_px',
             'ligation_short_gap_px', 'ligation_search_radius_px', 'pair_at_least_one_length_min'):
    if not np.isfinite(globals()[name]) or globals()[name] < 0:
        raise ValueError(f"{name} 必须是非负有限数")
for name in ('green_brightness', 'red_brightness', 'intensity_max', 'min_object_area'):
    if not np.isfinite(globals()[name]):
        raise ValueError(f"{name} 必须是有限数")
for name in ('green_threshold', 'red_threshold'):
    if not 0 <= globals()[name] <= 255:
        raise ValueError(f"{name} 必须在 0–255")
for name in ('ligation_angle_deg', 'ligation_axis_change_deg', 'pair_angle_deg'):
    if not 0 <= globals()[name] <= 90:
        raise ValueError(f"{name} 必须在 0–90 度")
if int(font_size) != font_size or font_size < 1 or int(box_line_width) != box_line_width or box_line_width < 1:
    raise ValueError("字体大小和线宽必须是正整数")
if int(edge_exclusion_px) != edge_exclusion_px:
    raise ValueError("edge_exclusion_px 必须是非负整数")
if (int(max_object_area_before_ligation) != max_object_area_before_ligation
        or max_object_area_before_ligation < min_object_area):
    raise ValueError("max_object_area_before_ligation 必须是 >= min_object_area 的整数")
if ligation_search_radius_px < ligation_gap_px + 1:
    raise ValueError("ligation_search_radius_px 必须 >= ligation_gap_px + 1，避免漏掉合法连接")

all_files = []
for folder, directories, files in os.walk(parent_folder, followlinks=False):
    # Windows junction 和软链接都不进入；避免清理范围越出输入目录。
    directories[:] = sorted(d for d in directories
        if not d.startswith(".ddrimage-")
        and not (Path(folder) / d).is_symlink()
        and not os.path.islink(Path(folder) / d)
        and not getattr(os.path, 'isjunction', lambda p: bool(
            getattr(os.lstat(p), 'st_file_attributes', 0) & 0x400))(Path(folder) / d))
    all_files.extend(Path(folder) / name for name in files if not (Path(folder) / name).is_symlink())

inputs = [p for p in all_files if p.parent != parent_folder
          and p not in _generated_images
          and p.suffix.lower() in ('.tif', '.tiff') and not p.name.lower().endswith('fiber.tif')]


def exported_channel(path):
    with tifffile.TiffFile(path) as image:
        try:
            description = json.loads(image.pages[0].description or "{}")
        except ValueError:
            return False
    return isinstance(description, dict) and "exported_channel" in description and "channels" in description


inputs = [path for path in inputs if not exported_channel(path)]
inputs.sort(key=lambda p: (p.parent.relative_to(parent_folder).as_posix().casefold(), p.name.casefold(), p.name))
if _review is not None:
    inputs = [path for path in inputs if _review.includes(path)]
if not inputs:
    raise FileNotFoundError("子文件夹中没有可分析的原始 TIFF；未执行清理")
# 同目录 a.tif 与 a.tiff 会产生相同前缀输出；在清理前报错。
output_keys = [(str(p.parent).casefold(), p.stem.casefold()) for p in inputs]
if len(output_keys) != len(set(output_keys)):
    raise ValueError("同目录存在相同前缀的 TIFF，会导致输出覆盖；请先重命名")
if fiber_mode == 'test':
    inputs = inputs[:1]
print(f"模式：{fiber_mode}；待分析：{len(inputs)} 张；首张：{inputs[0]}")

# %% [cell 5]
try:
    font = ImageFont.truetype('DejaVuSans.ttf', font_size)
except OSError:
    try:
        font = ImageFont.truetype('arial.ttf', font_size)
    except OSError:
        font = ImageFont.load_default()

# 每个子文件夹单独累计结果；每处理完一张 TIFF 就立即刷新该文件夹的 CSV。
folder_rows = {}

for image_number, path in enumerate(inputs, 1):
    try:
        with tifffile.TiffFile(path) as tif:
            series = tif.series[0]
            rgb = series.asarray()
            axes = series.axes
            if len(tif.series) != 1 or tif.pages[0].photometric.name != 'RGB':
                raise ValueError('要求单张 RGB TIFF，不接受多 series 或非 RGB 图像')
        if axes == 'SYX':
            rgb = np.moveaxis(rgb, 0, -1)
        elif axes != 'YXS':
            raise ValueError(f'不支持 TIFF 轴顺序 {axes}；要求单张 RGB，不能为 Z/T 堆栈')
        if rgb.ndim != 3 or rgb.shape[-1] != 3:
            raise ValueError(f'要求恰好三个 RGB 通道，实际 shape={rgb.shape}')
        if not np.isfinite(rgb).all() or rgb.min() < 0 or rgb.max() > intensity_max:
            raise ValueError('像素值不在 0–intensity_max；请检查强度参数')

        scaled = rgb.astype(np.float32) * (255.0 / intensity_max)
        # 先将每个通道的 min..max 拉伸到各自 window，再调整对比度/亮度并二值化。
        green_corrected = stretch_to_window(scaled[..., 1], green_window)
        red_corrected = stretch_to_window(scaled[..., 0], red_window)
        green = np.clip(green_corrected * green_contrast + green_brightness, 0, 255) >= green_threshold
        red = np.clip(red_corrected * red_contrast + red_brightness, 0, 255) >= red_threshold
        threshold_rgb = np.stack([red, green, np.zeros_like(red)], axis=-1).astype(np.uint8) * 255

        # 按对象两两计算方向性重叠率，并同时判定两通道，避免删除顺序影响结果。
        raw_red_labels = label(red, connectivity=2)
        raw_green_labels = label(green, connectivity=2)
        red_areas = np.bincount(raw_red_labels.ravel())
        green_areas = np.bincount(raw_green_labels.ravel())
        shared = (raw_red_labels > 0) & (raw_green_labels > 0)
        red_remove, green_remove = set(), set()
        if shared.any():
            object_pairs, intersection_areas = np.unique(
                np.column_stack([raw_red_labels[shared], raw_green_labels[shared]]),
                axis=0, return_counts=True)
            for (red_id, green_id), intersection in zip(object_pairs, intersection_areas):
                if intersection / red_areas[red_id] > pre_ligation_overlap_threshold:
                    red_remove.add(int(red_id))
                if intersection / green_areas[green_id] > pre_ligation_overlap_threshold:
                    green_remove.add(int(green_id))
        red = red.copy()
        green = green.copy()
        if red_remove:
            red[np.isin(raw_red_labels, list(red_remove))] = False
        if green_remove:
            green[np.isin(raw_green_labels, list(green_remove))] = False

        red_labels, reds = ligate(red)
        green_labels, greens = ligate(green)
        ligation_rgb = np.stack([red_labels > 0, green_labels > 0, np.zeros_like(red)], axis=-1).astype(np.uint8) * 255
        pairs = find_pairs(red_labels, green_labels, reds, greens)
        if _review is not None:
            pairs = _review.fibers(path, pairs, reds, greens, intensity_max)

        annotated = Image.fromarray(np.rint(np.clip(scaled, 0, 255)).astype(np.uint8))
        draw = ImageDraw.Draw(annotated)
        rows = []
        for number, (ri, gi, overlap_axis_length, gap_axis_length) in enumerate(pairs, 1):
            pair_id = str(number)
            # 重叠配对各减去重叠投影的一半；间隔配对各增加轴向间隔的一半。
            red_length = max(0.0, reds[ri]['length'] - overlap_axis_length / 2 + gap_axis_length / 2)
            green_length = max(0.0, greens[gi]['length'] - overlap_axis_length / 2 + gap_axis_length / 2)
            rows.append([path.stem, number, round(red_length, 3), round(green_length, 3)])
            for obj, color in ((reds[ri], 'red'), (greens[gi], 'lime')):
                corners = [tuple(point) for point in obj['corners']]
                draw.line(corners + [corners[0]], fill=color, width=box_line_width)
            # 一组配对只标一次：水平居中，并紧贴两条 fiber 整体的下方。
            bounds = draw.textbbox((0, 0), pair_id, font=font, stroke_width=1)
            tw, th = bounds[2] - bounds[0], bounds[3] - bounds[1]
            pair_x0 = min(reds[ri]['bbox'][0, 0], greens[gi]['bbox'][0, 0])
            pair_x1 = max(reds[ri]['bbox'][1, 0], greens[gi]['bbox'][1, 0])
            pair_y1 = max(reds[ri]['bbox'][1, 1], greens[gi]['bbox'][1, 1])
            tx = int(np.clip((pair_x0 + pair_x1 - tw) / 2, 0, max(0, annotated.width - tw)))
            ty = int(np.clip(pair_y1 + 3, 0, max(0, annotated.height - th)))
            draw.text((tx - bounds[0], ty - bounds[1]), pair_id, fill='white',
                      font=font, stroke_width=1, stroke_fill='black')

        result_folder = ddrimage_output / path.parent.relative_to(parent_folder)
        result_folder.mkdir(parents=True, exist_ok=True)
        for suffix, array in (('threshold', threshold_rgb), ('ligation', ligation_rgb), ('pair', np.array(annotated))):
            tifffile.imwrite(result_folder / f'{path.stem}_{suffix}_fiber.tif', array,
                             photometric='rgb', metadata=None)
        # 汇总到“子文件夹名称_fiber.csv”；重写而不是追加，避免表头或结果重复。
        accumulated_rows = folder_rows.setdefault(path.parent, [])
        accumulated_rows.extend(rows)
        csv_path = result_folder / f'{path.parent.name}_fiber.csv'
        with csv_path.open('w', newline='', encoding='utf-8-sig') as handle:
            writer = csv.writer(handle)
            writer.writerow(['image_prefix', 'fiber_pair_ID', 'red_length_px', 'green_length_px'])
            writer.writerows(accumulated_rows)
        print(f'[{image_number}/{len(inputs)}] {path.name}：高重叠删除 red={len(red_remove)}、green={len(green_remove)}；'
              f'连接后 red={len(reds)}、green={len(greens)}；配对={len(pairs)}')
        if _test_preview is not None:
            _test_preview.update(source=str(path), pages=1, maximum=float(intensity_max), images=[
                [title, (result_folder / f"{path.stem}_{kind}_fiber.tif").relative_to(ddrimage_output).as_posix()]
                for title, kind in (("配对标注", "pair"), ("二值分割", "threshold"), ("片段连接", "ligation"))])
    except Exception as error:
        raise RuntimeError(f'处理失败：{path}；{error}') from error
print('完成。长度单位为 px；请结合 pair 图检查结果。')

# %% [cell 6]
