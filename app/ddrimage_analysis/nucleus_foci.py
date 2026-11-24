
# %% [cell 0]
from pathlib import Path
# CSV 和标注后的 TIFF 会直接写入这里的各个 LIF 子文件夹。
input_dir = Path(r"/mnt/NC/image_python_202609/test_tif2/")
foci_mode = "test"  # "test"：第一个子文件夹的第一张合并图；"all"：全部分析
# 面积单位均为像素面积 px²，范围包含上下限。
nucleus_area_px = (1000, 20000)
foci_area_px = (2, 25)
# 调整顺序：window 拉伸 → contrast → brightness → gamma。
dapi_adjust_windows = "full" # window="full"：当前页 min..max 拉伸到 0..255；None：不拉伸；也可填 (low, high)。
dapi_adjust_contrast = 1.0
dapi_adjust_brightness = 0.0

channel1_adjust_window = (0,255)
channel1_adjust_contrast = 10.0
channel1_adjust_brightness = 0.0
channel2_adjust_window = (0,255)
channel2_adjust_contrast = 10.0
channel2_adjust_brightness = 0.0
channel3_adjust_window = (0,255)
channel3_adjust_contrast = 10.0
channel3_adjust_brightness = 0.0
channel4_adjust_window = (0,255)
channel4_adjust_contrast = 10.0
channel4_adjust_brightness = 0.0
channel5_adjust_window = (0,255)
channel5_adjust_contrast = 10.0
channel5_adjust_brightness = 0.0

# %% [cell 1]
# 运行前删除 input_dir 所有子文件夹中的 *.csv 和 *foci.tif。
cleanup_old_results = True

dapi_adjust = dict(window=dapi_adjust_windows, contrast=dapi_adjust_contrast, brightness=dapi_adjust_brightness, gamma=1.0)
# 以下编号表示“排除 blue 后的第几个蛋白通道”，不是源 TIFF 的通道编号。
# 例如源通道为 red / blue / cyan，则 red 使用 channel1_adjust，cyan 使用 channel2_adjust。
# 实际只有 n 个蛋白通道时仅使用前 n 组；最多支持 5 个非 blue 通道。
# 五组参数彼此独立，可分别修改。默认值相同，便于从不额外增益的设置开始。
channel1_adjust = dict(window=channel1_adjust_window, contrast=channel1_adjust_contrast, brightness=channel1_adjust_brightness, gamma=1.5)
channel2_adjust = dict(window=channel2_adjust_window, contrast=channel2_adjust_contrast, brightness=channel2_adjust_brightness, gamma=1.5)
channel3_adjust = dict(window=channel3_adjust_window, contrast=channel3_adjust_contrast, brightness=channel3_adjust_brightness, gamma=1.5)
channel4_adjust = dict(window=channel4_adjust_window, contrast=channel4_adjust_contrast, brightness=channel4_adjust_brightness, gamma=1.5)
channel5_adjust = dict(window=channel5_adjust_window, contrast=channel5_adjust_contrast, brightness=channel5_adjust_brightness, gamma=1.5)
# 阈值可设为 "otsu" 或 0..255 的固定数值。
dapi_threshold = "otsu"
protein_threshold = "otsu"
dapi_smooth_size = 3       # 类似 Fiji Smooth；1 表示不平滑，必须为奇数
dapi_fill_holes = True
nucleus_seed_distance = 8  # 核分水岭种子间最小距离，px
nucleus_split_solidity = 0.95  # solidity >= 此值的近凸形 DAPI 对象不分水岭切开
foci_seed_distance = 2     # foci 分水岭种子间最小距离，px

font_size = 12
font_path = None  # 例如 r"C:\Windows\Fonts\arial.ttf"；None 时自动查找
outline_width = 1

# %% [cell 2]
import csv
import json
import os
import re
import time
from collections import defaultdict
from contextlib import ExitStack
from tempfile import TemporaryDirectory

import numpy as np
import tifffile
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage as ndi
from skimage.filters import threshold_otsu
from skimage.measure import regionprops
from skimage.morphology import h_maxima
from skimage.segmentation import clear_border, find_boundaries, relabel_sequential, watershed

def _watershed_mask(mask, distance, min_area, show_progress=False,
                    seed_prominence=0, split_solidity=None):
    """先删小连通域，再以一次 marker-controlled watershed 切分多峰对象。"""
    structure = np.ones((3, 3))
    started = time.perf_counter()
    components, before_count = ndi.label(mask, structure)
    areas = np.bincount(components.ravel())
    remove = areas < min_area
    remove[0] = False
    mask = mask.copy()
    mask[remove[components]] = False
    components, component_count = ndi.label(mask, structure)
    if show_progress:
        print(f'          填孔及去小信号完成：{before_count} → {component_count} 个连通域，'
              f'{time.perf_counter() - started:.2f} 秒', flush=True)
    if not component_count:
        return np.zeros(mask.shape, np.int32)

    # 距离图和最大值滤波均是数组运算。最大值平台作为一个 marker；如果相邻但不相连
    # 的对象互相抑制，则为缺少 marker 的连通域补上其距离最大点。
    started = time.perf_counter()
    depth = ndi.distance_transform_edt(mask)
    effective_distance = distance
    marker_limit = component_count + max(5000, component_count * 4)
    while True:
        maxima = ((depth == ndi.maximum_filter(
            depth, size=2 * effective_distance + 1, mode='constant')) & mask)
        if seed_prominence > 0:
            maxima &= h_maxima(depth, seed_prominence).astype(bool)
        markers, marker_count = ndi.label(maxima, structure)
        seeded = np.unique(components[markers > 0])
        missing = np.setdiff1d(np.arange(1, component_count + 1), seeded)
        if missing.size:
            positions = ndi.maximum_position(depth, labels=components, index=missing.tolist())
            for offset, (y, x) in enumerate(positions, 1):
                markers[y, x] = marker_count + offset
            marker_count += len(missing)
        if marker_count <= marker_limit or effective_distance >= 64:
            break
        effective_distance = min(64, effective_distance * 2)
        print(f'          种子过多，自动把峰间距增至 {effective_distance}px', flush=True)
    if marker_count > marker_limit:
        raise RuntimeError(
            f'分水岭种子仍过多（{marker_count}）；请提高阈值或增大 seed_distance')
    if show_progress:
        print(f'          距离变换及种子检测完成：{marker_count} 个种子，'
              f'{time.perf_counter() - started:.2f} 秒', flush=True)

    # 旧代码逐 marker 调用 scipy.maximum_position；marker 多时耗时随 marker 数显著增长。
    # 这里一次扫描 marker 数组取得每个 marker 的首像素，并用该像素映射所属连通域。
    flat_markers = markers.ravel()
    locations = np.flatnonzero(flat_markers)
    marker_ids = flat_markers[locations]
    first = np.full(marker_count + 1, flat_markers.size, dtype=np.intp)
    np.minimum.at(first, marker_ids, locations)
    marker_components = components.ravel()[first[1:]]
    seeds_per_component = np.bincount(marker_components, minlength=component_count + 1)
    split_components = np.flatnonzero(seeds_per_component > 1)
    protected_count = 0
    if split_solidity is not None and len(split_components):
        # solidity = 面积/凸包面积。接近 1 的对象完整而近凸形，即使内部有两个距离峰，
        # 通常仍是一个椭圆或轻微不规则的核；明显凹陷的粘连核才允许 watershed。
        solidity = np.zeros(component_count + 1, float)
        for region in regionprops(components):
            solidity[region.label] = region.solidity
        # 此处不能再乘以 min_area。对象既然已经通过面积下限，就必须参与
        # solidity 判断；否则用户提高 nucleus_area_px[0] 时反而会关闭保护。
        protect = solidity[split_components] >= split_solidity
        allow_split = ~protect
        protected_count = int((~allow_split).sum())
        split_components = split_components[allow_split]
    if not len(split_components):
        if show_progress:
            print(f'          无对象需要分水岭；近凸形保护 {protected_count} 个', flush=True)
        return components.astype(np.int32, copy=False)

    # 所有需要切分的连通域合并为一个 mask，只调用一次 Cython watershed。
    # 不需要切分的对象直接复用 connected-component 标签。
    started = time.perf_counter()
    split_lookup = np.zeros(component_count + 1, bool)
    split_lookup[split_components] = True
    split_mask = split_lookup[components]
    # scikit-image 0.26 在 watershed_line=True 与 3×3 全邻域 connectivity 同用时，
    # 某些普通 512×512 图也会进入异常慢路径。使用其默认二维连接方式可避免该问题。
    split_markers = relabel_sequential(np.where(split_mask, markers, 0))[0]
    split_labels = watershed(-depth, split_markers, mask=split_mask, watershed_line=True)
    split_labels = relabel_sequential(split_labels)[0]
    result = components.astype(np.int32, copy=True)
    result[split_mask] = 0
    result[split_labels > 0] = split_labels[split_labels > 0] + component_count
    result = relabel_sequential(result)[0].astype(np.int32, copy=False)
    if show_progress:
        print(f'          快速分水岭完成：仅切分 {len(split_components)}/{component_count} 个连通域，'
              f'近凸形保护 {protected_count} 个，{time.perf_counter() - started:.2f} 秒', flush=True)
    return result


def segment_dapi(rgb, adjust, threshold, distance, min_area, smooth=1, fill_holes=True,
                 split_solidity=0.95):
    """DAPI 专用：调整→二值→填孔→去小背景信号→快速分水岭。"""
    if rgb.ndim != 3 or rgb.shape[-1] != 3 or rgb.dtype != np.uint8:
        raise ValueError('需要上一版导出的 8-bit RGB 通道 TIFF')
    started = time.perf_counter()
    image = rgb.max(axis=-1).astype(float)
    window = adjust.get('window')
    if window is not None:
        low, high = (image.min(), image.max()) if window == 'full' else window
        image = (image - low) * 255 / (high - low) if high > low else np.zeros_like(image)
    image = np.clip((image - 127.5) * adjust['contrast'] + 127.5 + adjust['brightness'], 0, 255)
    image = np.rint(255 * (image / 255) ** (1 / adjust['gamma'])).astype(np.uint8)
    image = ndi.uniform_filter(image.astype(np.float32), size=smooth, mode='nearest')
    if threshold == 'otsu' and image.min() == image.max():
        return np.zeros(image.shape, np.int32)
    cutoff = threshold_otsu(image) if threshold == 'otsu' else threshold
    mask = image > cutoff
    if fill_holes:
        mask = ndi.binary_fill_holes(mask)
    print(f'          DAPI 二值化/填孔完成：阈值 {float(cutoff):.2f}，'
          f'前景 {mask.mean():.1%}，{time.perf_counter() - started:.2f} 秒', flush=True)
    if not mask.any():
        return np.zeros(mask.shape, np.int32)
    # 1 px 的 h-maxima 抑制由边缘锯齿造成的浅距离峰；solidity 保护完整近凸形核。
    return _watershed_mask(mask, distance, min_area, show_progress=True,
                           seed_prominence=1.0, split_solidity=split_solidity)


def segment(rgb, adjust, threshold, distance, min_area=1):
    """蛋白通道快速二维分割；不执行 DAPI 的平滑和填孔。"""
    if rgb.ndim != 3 or rgb.shape[-1] != 3 or rgb.dtype != np.uint8:
        raise ValueError('需要上一版导出的 8-bit RGB 通道 TIFF')
    image = rgb.max(axis=-1).astype(float)
    window = adjust.get('window')
    if window is not None:
        low, high = (image.min(), image.max()) if window == 'full' else window
        image = (image - low) * 255 / (high - low) if high > low else np.zeros_like(image)
    image = np.clip((image - 127.5) * adjust['contrast'] + 127.5 + adjust['brightness'], 0, 255)
    image = np.rint(255 * (image / 255) ** (1 / adjust['gamma'])).astype(np.uint8)
    if threshold == 'otsu' and image.min() == image.max():
        return np.zeros(image.shape, np.int32)
    mask = image > (threshold_otsu(image) if threshold == 'otsu' else threshold)
    if not mask.any():
        return np.zeros(mask.shape, np.int32)
    return _watershed_mask(mask, distance, min_area)


def annotate(rgb, nuclei, counts, identifiers):
    """原通道底图上添加白色核轮廓及 ROI 内的编号、计数。"""
    rgb = rgb.copy()
    border = find_boundaries(nuclei, mode='inner')
    if outline_width > 1:
        border = ndi.binary_dilation(border, iterations=outline_width - 1) & (nuclei > 0)
    rgb[border] = 255
    canvas = Image.fromarray(rgb)
    for region in regionprops(nuclei):
        text = f'{identifiers[region.label]}:{counts[region.label]}'
        y0, x0, _, _ = region.bbox
        roi = region.image
        integral = np.pad(roi.astype(np.int64), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        for size in range(font_size, 0, -1):
            font = fonts[size]
            box = font.getbbox(text)
            width, height = max(1, box[2] - box[0]), max(1, box[3] - box[1])
            if width > roi.shape[1] or height > roi.shape[0]:
                continue
            sums = (integral[height:, width:] - integral[:-height, width:]
                    - integral[height:, :-width] + integral[:-height, :-width])
            ys, xs = np.nonzero(sums == width * height)
            if len(ys):
                cy, cx = region.centroid
                pick = np.argmin((ys + y0 + height / 2 - cy)**2 + (xs + x0 + width / 2 - cx)**2)
                ImageDraw.Draw(canvas).text((int(xs[pick]) + x0 - box[0], int(ys[pick]) + y0 - box[1]),
                                           text, font=font, fill=(255, 255, 255))
                break
        else:
            raise ValueError(f'核 {identifiers[region.label]} 无法容纳文字，请增大核面积下限')
    return np.asarray(canvas)

# %% [cell 3]
# 1. 检查参数，准备字体。
root = Path(input_dir).resolve()
if not root.is_dir() or foci_mode not in {'test', 'all'}:
    raise ValueError('请检查 input_dir 路径及 foci_mode（test/all）')
for value in (dapi_smooth_size, nucleus_seed_distance, foci_seed_distance, font_size, outline_width):
    if not isinstance(value, int) or value < 1:
        raise ValueError('窗口、种子距离、字号和线宽必须为正整数')
if dapi_smooth_size % 2 == 0:
    raise ValueError('平滑窗口必须为奇数')
if not np.isfinite(nucleus_split_solidity) or not 0 <= nucleus_split_solidity <= 1:
    raise ValueError('nucleus_split_solidity 必须在 0..1 之间')
# 依次对应第 1..5 个非 blue 通道；每张图根据实际蛋白通道数取前 n 组。
channel_adjustments = [channel1_adjust, channel2_adjust, channel3_adjust, channel4_adjust, channel5_adjust]
for low, high in (nucleus_area_px, foci_area_px):
    if not np.isfinite([low, high]).all() or not 1 <= low <= high:
        raise ValueError('面积要求有限数值且 1 <= min <= max')
for threshold in (dapi_threshold, protein_threshold):
    if threshold != 'otsu' and not 0 <= threshold <= 255:
        raise ValueError('阈值应为 otsu 或 0..255')
fonts = {}
for size in range(1, font_size + 1):
    for candidate in ([font_path] if font_path else ['DejaVuSans.ttf', r'C:\Windows\Fonts\arial.ttf']):
        try:
            fonts[size] = ImageFont.truetype(str(candidate), size)
            break
        except OSError:
            pass
    if size not in fonts:
        fonts[size] = ImageFont.load_default(size=size)

# 2. 按名称排序，找出合并图及配套通道；test 只选择第一文件夹的第一张图。
folders = sorted((p for p in root.iterdir() if p.is_dir() and not p.name.startswith('.')),
                 key=lambda p: p.name.casefold())
if not folders:
    raise FileNotFoundError('input_dir 下没有子文件夹')
jobs = defaultdict(list)
for folder in (folders[:1] if foci_mode == 'test' else folders):
    layout = None
    if folder.resolve().parent != root:
        raise ValueError('不处理指向其他目录的文件夹链接')
    for path in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
        if path in _generated_images:
            continue
        if path.suffix.lower() != '.tif' or path.name.lower().endswith(('foci.tif', 'level.tif')):
            continue
        if _review is not None and not _review.includes(path):
            continue
        with tifffile.TiffFile(path) as tif:
            meta = json.loads(tif.pages[0].description or '{}')
            pages = len(tif.pages)
        if 'exported_channel' in meta:  # 跳过单通道图，仅以 merge 图建立分析任务。
            continue
        if 'channels' not in meta:
            raise ValueError(f'{path.name} 缺少上一版导出的通道元数据')
        channels = [(int(c['channel']), c['lut_name'].strip().lower()) for c in meta['channels']]
        blue = [c for c, color in channels if color == 'blue']
        if len(blue) != 1 or len(channels) < 2 or len({c for c, _ in channels}) != len(channels):
            raise ValueError(f'{path.name} 需要唯一 blue 通道及至少一个蛋白通道')
        protein_count = len(channels) - 1
        if protein_count > 5:
            raise ValueError(f'{path.name} 有 {protein_count} 个非 blue 通道，本脚本最多支持 5 个')
        # 只验证本图会使用的前 n 组，未使用的参数不参与计算。
        for a in [dapi_adjust, *channel_adjustments[:protein_count]]:
            if set(a) != {'window', 'contrast', 'brightness', 'gamma'}:
                raise ValueError('adjust 必须包含 window、contrast、brightness、gamma 四个参数')
            if not np.isfinite([a['contrast'], a['brightness'], a['gamma']]).all() or min(a['contrast'], a['gamma']) <= 0:
                raise ValueError('contrast、gamma 必须为正有限数值，brightness 必须为有限数值')
            window = a['window']
            if window is not None and window != 'full':
                if not isinstance(window, (tuple, list)) or len(window) != 2 or not 0 <= window[0] < window[1] <= 255:
                    raise ValueError('window 应为 None、full 或合法的 (low, high)')
        if layout is not None and layout != channels:
            raise ValueError(f'{folder.name} 内图片通道布局不一致')
        layout = channels
        files = {}
        for channel, color in channels:
            channel_path = path.with_name(f'{path.stem}_channel{channel}.tif')
            if not channel_path.is_file():
                raise FileNotFoundError(f'缺少 {channel_path.name}；需要 merge_and_channels 导出的通道 TIFF')
            with tifffile.TiffFile(channel_path) as tif:
                info = json.loads(tif.pages[0].description or '{}')
                if len(tif.pages) != pages or info.get('exported_channel') != channel:
                    raise ValueError(f'{channel_path.name} 页数或通道编号不匹配')
            files[channel] = channel_path
        jobs[folder].append((path, pages, channels, blue[0], files))
        if foci_mode == 'test':
            break
if not jobs:
    raise FileNotFoundError('没有找到可分析的合并 TIFF')

def review_segmentation(dapi_rgb):
    nuclei = segment_dapi(dapi_rgb, dapi_adjust, dapi_threshold,
                          nucleus_seed_distance, nucleus_area_px[0],
                          dapi_smooth_size, dapi_fill_holes,
                          nucleus_split_solidity)
    nuclei = clear_border(nuclei)
    for region in regionprops(nuclei):
        if not nucleus_area_px[0] <= region.area <= nucleus_area_px[1]:
            nuclei[nuclei == region.label] = 0
    nuclei = relabel_sequential(nuclei)[0]
    return nuclei


# %% [cell 5]
# 4. 逐文件夹、逐图、逐页处理。每张图完成后立即发布 TIFF 并刷新 CSV。
foci_report = []
total_images = sum(len(images) for images in jobs.values())
completed_images = 0
for folder_number, (folder, images) in enumerate(jobs.items(), 1):
    result_folder = ddrimage_output / folder.relative_to(root)
    result_folder.mkdir(parents=True, exist_ok=True)
    rows = defaultdict(list)
    folder_errors = []
    folder_completed = 0
    print(f'\n[{folder_number}/{len(jobs)}] 文件夹 {folder.name}：共 {len(images)} 张 merge 图', flush=True)
    for image_number, (path, pages, channels, dapi, files) in enumerate(images, 1):
        started = time.perf_counter()
        proteins = [(c, color) for c, color in channels if c != dapi]
        image_rows = defaultdict(list)
        print(f'  [{image_number}/{len(images)}] 开始 {path.name}（{pages} 页，{len(proteins)} 个蛋白通道）', flush=True)
        for order, (channel, color) in enumerate(proteins, 1):
            print(f'      源 channel{channel} ({color}) → channel{order}_adjust', flush=True)
        try:
            # 临时目录只服务当前图片；当前图片成功即发布，不等待同一文件夹的其他图片。
            with TemporaryDirectory(prefix='.foci_', dir=result_folder) as temp:
                stage = Path(temp)
                with ExitStack() as stack:
                    readers = {c: stack.enter_context(tifffile.TiffFile(p)) for c, p in files.items()}
                    estimated_bytes = sum(p.size + 65536 for p in readers[dapi].pages)
                    bigtiff = estimated_bytes >= 2**32 - 2**25
                    writers = {c: stack.enter_context(tifffile.TiffWriter(
                        stage / f'{path.stem}_channel{c}_foci.tif', bigtiff=bigtiff)) for c, _ in proteins}
                    dapi_writer = stack.enter_context(tifffile.TiffWriter(
                        stage / f'{path.stem}_channel{dapi}_foci.tif', bigtiff=bigtiff))
                    binary_writers = {c: stack.enter_context(tifffile.TiffWriter(
                        stage / f'{path.stem}_channel{c}_binary_foci.tif', bigtiff=bigtiff)) for c, _ in proteins}

                    for page in range(pages):
                        page_started = time.perf_counter()
                        print(f'      页 {page + 1}/{pages}：DAPI 分割与 ROI 筛选…', flush=True)
                        dapi_rgb = readers[dapi].pages[page].asarray()
                        if _review is None:
                            nuclei = review_segmentation(dapi_rgb)
                        else:
                            nuclei = _review.nuclei(path, page, files[dapi], [path, *files.values()],
                                                    lambda: review_segmentation(dapi_rgb))
                        dapi_writer.write(np.where(nuclei > 0, 255, 0).astype(np.uint8),
                            photometric='minisblack', metadata=None,
                            description=json.dumps({'source_merge': path.name, 'page': page + 1,
                                'channel': dapi, 'stage': 'watershed_after_roi_filter',
                                'foreground': 0, 'background': 255,
                                'nucleus_split_solidity': nucleus_split_solidity,
                                'nucleus_seed_distance': nucleus_seed_distance,
                                'nucleus_area_px': list(nucleus_area_px)}))
                        identifiers = {r.label: (f'P{page + 1:03d}_N{r.label:03d}' if pages > 1 else str(r.label))
                                       for r in regionprops(nuclei)}
                        print(f'        保留 {len(identifiers)} 个细胞核', flush=True)

                        for order, (channel, color) in enumerate(proteins):
                            protein_started = time.perf_counter()
                            print(f'        蛋白 {order + 1}/{len(proteins)}：channel{channel} ({color}) 分割与计数…', flush=True)
                            rgb = readers[channel].pages[page].asarray()
                            if rgb.shape != dapi_rgb.shape:
                                raise ValueError(f'{path.name} 的通道尺寸不匹配')
                            adjust = channel_adjustments[order]
                            foci = segment(rgb, adjust, protein_threshold,
                                           foci_seed_distance, foci_area_px[0])
                            binary_writers[channel].write(np.where(foci > 0, 255, 0).astype(np.uint8),
                                photometric='minisblack', metadata=None,
                                description=json.dumps({'source_merge': path.name, 'page': page + 1,
                                    'channel': channel, 'adjust_parameter': f'channel{order + 1}_adjust',
                                    'adjust': adjust, 'stage': 'protein_after_watershed',
                                    'foreground': 0, 'background': 255}, ensure_ascii=True))
                            counts = dict.fromkeys(identifiers, 0)
                            for region in regionprops(foci):
                                if foci_area_px[0] <= region.area <= foci_area_px[1]:
                                    y, x = region.coords.T
                                    owners = np.unique(nuclei[y, x])
                                    if len(owners) == 1 and owners[0] > 0:
                                        counts[int(owners[0])] += 1
                            image_rows[channel, color].extend(
                                [path.stem, identifiers[n], count] for n, count in counts.items())
                            writers[channel].write(annotate(rgb, nuclei, counts, identifiers),
                                photometric='rgb', metadata=None,
                                description=json.dumps({'source_merge': path.name, 'page': page + 1,
                                                        'channel': channel, 'color': color}))
                            print(f'          完成，用时 {time.perf_counter() - protein_started:.1f} 秒', flush=True)
                        print(f'      页 {page + 1}/{pages} 完成，用时 {time.perf_counter() - page_started:.1f} 秒', flush=True)

                # 所有 writers 已关闭，文件现在完整可读。先在临时目录生成包含当前图的累计 CSV。
                next_rows = defaultdict(list, {key: list(values) for key, values in rows.items()})
                for key, values in image_rows.items():
                    next_rows[key].extend(values)
                for (channel, color), values in next_rows.items():
                    color_name = re.sub(r'[^\w-]', '_', color)
                    csv_file = stage / f'{folder.name}_channel{channel}_{color_name}.csv'
                    with csv_file.open('w', encoding='utf-8-sig', newline='') as handle:
                        writer = csv.writer(handle)
                        writer.writerow(['image', 'nucleus_id', 'foci_count'])
                        writer.writerows(values)
                # 当前图片的 TIFF 和刷新后的 CSV 一起发布；无需等待整个子文件夹处理结束。
                output_files = [file for file in stage.iterdir() if file.suffix.lower() == '.tif']
                if _test_preview is not None:
                    images = []
                    for channel, color in proteins:
                        images.append((f"通道 {channel} ({color}) · Foci 标注", f"{path.stem}_channel{channel}_foci.tif"))
                        images.append((f"通道 {channel} ({color}) · 二值分割", f"{path.stem}_channel{channel}_binary_foci.tif"))
                    images.insert(1, ("DAPI · 核分割", f"{path.stem}_channel{dapi}_foci.tif"))
                    _test_preview.update(source=str(path), pages=pages, originals=[
                        [f"原图 · 通道 {channel} ({color})", str(files[channel])]
                        for channel, color in channels], images=[
                        [title, (result_folder / name).relative_to(ddrimage_output).as_posix()] for title, name in images])
                for file in stage.iterdir():
                    file.replace(result_folder / file.name)
                rows = next_rows

            completed_images += 1
            folder_completed += 1
            print(f'  [{image_number}/{len(images)}] {path.name} 已发布 {len(output_files)} 个 TIFF；'
                  f'总进度 {completed_images}/{total_images}；用时 {time.perf_counter() - started:.1f} 秒', flush=True)
        except Exception as exc:
            message = f'{path.name}: {exc}'
            folder_errors.append(message)
            print(f'  [{image_number}/{len(images)}] 失败并继续下一张：{message}', flush=True)

    status = 'ok' if not folder_errors else ('partial' if folder_completed else 'error')
    foci_report.append(dict(folder=folder.name, status=status, images_completed=folder_completed,
                            images_total=len(images), csv_files=len(rows), errors=folder_errors))
    print(f'[{folder_number}/{len(jobs)}] 文件夹 {folder.name} 结束：'
          f'{folder_completed}/{len(images)} 张成功', flush=True)
print('\n批处理报告：', foci_report)
if completed_images < total_images or not completed_images:
    raise RuntimeError('部分或全部图像处理失败。已生成的结果可在结果目录查看。')

# %% [cell 6]

