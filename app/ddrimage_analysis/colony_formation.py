
# %% [cell 0]
from pathlib import Path

# ===== 必改参数 =====
parent_dir = Path(r"/mnt/NC/image_python_202609/colony2")  # 母文件夹；图片放在它的子文件夹内
mode = "test"                     # "test"：首个子文件夹的首图；"all"：全部
area_range = (10, 12000)           # 基准ROI像素²，含上下限；示例值，须按实验定义调整
# ===== ROI 参数（检测在缩略图进行，计数始终使用原分辨率） =====
hough_votes = 110                 # 越大越严格；漏孔可降至 70~100，误检则增大
# ===== 分割参数 =====
threshold_factor = 1.0            # Otsu阈值乘数；漏掉浅染色时减小，噪点多时增大
min_contrast = 8.0                # 背景校正后最低灰度差，范围0~255，防空白孔误计数
peak_prominence = 3.0             # 距离峰突出度（基准ROI像素）；增大可减少过度分割

# %% [cell 1]
# ===== ROI 参数（检测在缩略图进行，计数始终使用原分辨率） =====
recursive = True                  # True：遍历所有层级子文件夹；False：仅直接子文件夹
radius_fraction = (0.08, 0.49)     # 圆半径 / 图片短边；小孔很多时降低下限

edge_threshold = 60               # Hough 使用的 Canny 高阈值
roi_radius_scale = 1.00           # 若检测到外壁，可缩至 0.90~0.97；须看叠加图确认
image_margin = 3                  # ROI 距原图边缘至少 3 像素，拒绝不完整孔
detect_six_well_grid = True       # 用较低阈值寻找规则2×3/3×2六孔布局，再排除大假圆
refine_strong_outer_wall = True   # 比较候选圆内外的同心边缘；内缘明显占优时改选孔底
inner_radius_fraction = (0.78, 0.87)  # 底部内缘半径相对外壁半径的搜索范围
inner_edge_dominance = 1.35       # 内缘圆周得分至少为外侧同心边缘的1.35倍才缩小ROI
inner_edge_current_fraction = 0.30 # 内缘得分还须达到当前Hough圆得分的30%，排除弱伪边缘
reference_guided_roi = True        # 后续单ROI图片若圈到更大侧壁，用首图ROI半径比例辅助改选内缘
reference_radius_tolerance = 0.08  # 相对首图半径比例大于8%时才启动辅助检查

# 自动找圆不能保证识别物理底面；可直接指定底面圆，跳过该图的自动识别。
# 键：相对母目录的路径，使用正斜杠；值：(中心x, 中心y, 半径)，原图像素。
# 坐标基于按 EXIF 纠正方向后的原图；手动圆不会再次乘 roi_radius_scale。
manual_rois = {}                  # 例如 {"组A/a.jpg": [(1502, 2042, 1023)]}

# ===== 分割参数 =====
background_sigma = 100.0          # 基准ROI像素；宜大于最大克隆半径，避免消去大克隆
split_touching = True            # 是否用分水岭分离粘连克隆
watershed_boundary_width = 1      # 整图中分水岭分界线宽度（原图像素）；0表示不画分界线

# %% [cell 2]
import csv
import warnings
import numpy as np
import cv2
from PIL import Image, ImageOps, ImageDraw, ImageFont
from scipy import ndimage as ndi
from skimage import filters, morphology, measure, segmentation


def read_gray8(path):
    """统一方向和位深。RGB用亮度，16-bit/float灰度拉伸到8-bit。"""
    with Image.open(path) as im:
        # 某些手机/相机JPG是MPO多帧容器，首帧就是主照片，可以正常分析。
        # 仅对真正的多页TIFF报错，避免无意遗漏实验页。
        if Path(path).suffix.lower() in (".tif", ".tiff") and getattr(im, "n_frames", 1) != 1:
            raise ValueError("多页TIFF请先拆分，避免只统计第一页")
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA") or "transparency" in im.info:
            rgba = im.convert("RGBA")
            base = Image.new("RGBA", rgba.size, "white")
            im = Image.alpha_composite(base, rgba).convert("RGB")
        if im.mode in ("RGB", "CMYK", "P", "YCbCr"):
            rgb = np.array(im.convert("RGB"))
            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        else:
            gray = np.array(im)
            if gray.ndim != 2:
                raise ValueError("只支持单幅二维灰度或彩色照片")
            if not np.isfinite(gray).all():
                raise ValueError("图片含NaN/Inf，请先修复")
            if gray.dtype != np.uint8:
                gray = gray.astype(np.float32)
                lo, hi = np.percentile(gray, (0.1, 99.9))
                if hi <= lo:
                    lo, hi = gray.min(), gray.max()
                gray = (np.clip((gray-lo)/max(float(hi-lo), 1e-9), 0, 1)*255).astype(np.uint8)
            rgb = np.repeat(gray[..., None], 3, axis=2)
    return gray, rgb


def detect_rois(gray, key, expected_radius_fraction=None):
    """Hough圆候选 + 完整圆检查 + 重叠去重；人工覆盖优先。"""
    h, w = gray.shape
    if key in manual_rois:
        circles = np.asarray(manual_rois[key], dtype=float).reshape(-1, 3)
    else:
        scale = min(1.0, 1200 / max(h, w))
        small_raw = cv2.resize(gray, None, fx=scale, fy=scale)
        small = cv2.GaussianBlur(small_raw, (7, 7), 1.5)
        short = min(small.shape)
        found = cv2.HoughCircles(
            small, cv2.HOUGH_GRADIENT, dp=1.2,
            minDist=max(10, short*radius_fraction[0]*1.6),
            param1=edge_threshold, param2=hough_votes,
            minRadius=max(5, round(short*radius_fraction[0])),
            maxRadius=round(short*radius_fraction[1]))
        circles = [] if found is None else found[0] / scale

        # 六孔板上的染色点很多，严格Hough常把多个孔合成一个大假圆。
        # 再以较低投票阈值取得候选，并只接受半径相近、近似正交且位置符合
        # 2×3或3×2晶格的六个圆；普通培养皿不会满足这一结构条件。
        if detect_six_well_grid:
            fallback_votes = min(hough_votes, max(45, round(hough_votes*0.64)))
            fallback = cv2.HoughCircles(
                small, cv2.HOUGH_GRADIENT, dp=1.2,
                minDist=max(10, short*radius_fraction[0]*1.6),
                param1=edge_threshold, param2=fallback_votes,
                minRadius=max(5, round(short*radius_fraction[0])),
                maxRadius=round(short*radius_fraction[1]))
            candidates = [] if fallback is None else [tuple(map(float, q/scale)) for q in fallback[0]]
            image_short = min(h, w)
            candidates = [q for q in candidates
                          if 0.11*image_short <= q[2] <= 0.25*image_short
                          and min(q[0]-q[2], q[1]-q[2],
                                  w-1-q[0]-q[2], h-1-q[1]-q[2]) >= image_margin]
            best_grid = None
            for ncols, nrows in ((2, 3), (3, 2)):
                for origin in candidates:
                    for right in candidates:
                        r0 = (origin[2]+right[2])/2
                        ax, ay = right[0]-origin[0], right[1]-origin[1]
                        step_x = np.hypot(ax, ay)
                        if not (1.7*r0 < step_x < 2.8*r0 and ax > 0
                                and abs(ay) < 0.45*abs(ax)
                                and abs(right[2]-r0) < 0.20*r0):
                            continue
                        for below in candidates:
                            bx, by = below[0]-origin[0], below[1]-origin[1]
                            step_y = np.hypot(bx, by)
                            if not (1.7*r0 < step_y < 2.8*r0 and by > 0
                                    and abs(bx) < 0.45*abs(by)
                                    and abs(below[2]-r0) < 0.20*r0):
                                continue
                            if abs(ax*bx+ay*by)/(step_x*step_y) > 0.30:
                                continue
                            predictions = [(origin[0]+col*ax+row*bx,
                                            origin[1]+col*ay+row*by)
                                           for row in range(nrows) for col in range(ncols)]
                            chosen, error = [], 0.0
                            for px, py in predictions:
                                options = [(np.hypot(q[0]-px, q[1]-py)+2*abs(q[2]-r0), i)
                                           for i, q in enumerate(candidates)
                                           if i not in chosen and abs(q[2]-r0) < 0.20*r0]
                                if not options:
                                    break
                                candidate_error, index = min(options)
                                q = candidates[index]
                                if np.hypot(q[0]-px, q[1]-py) > 0.45*r0:
                                    break
                                chosen.append(index)
                                error += candidate_error/r0
                            if len(chosen) == 6:
                                radii = np.array([candidates[i][2] for i in chosen])
                                score = error + 10*np.std(radii)/np.mean(radii)
                                if score < 3 and (best_grid is None or score < best_grid[0]):
                                    best_grid = (score, [candidates[i] for i in chosen])
            if best_grid is not None:
                circles = best_grid[1]

        # 单皿/单孔照片有时最清楚的是外壁，Hough会圈到侧壁外缘。对大圆同时
        # 比较内侧孔底候选和圆外同心侧壁：内缘明显占优时才缩小；若圆外侧壁
        # 与内缘相当，则当前圆更可能已经是孔底，保持不变。
        if refine_strong_outer_wall and len(circles) > 0:
            gradient = cv2.magnitude(cv2.Sobel(small_raw, cv2.CV_32F, 1, 0, ksize=3),
                                     cv2.Sobel(small_raw, cv2.CV_32F, 0, 1, ksize=3))
            angles = np.linspace(0, 2*np.pi, 1080, endpoint=False)
            refined = []
            for x, y, r in circles:
                sx, sy, sr = x*scale, y*scale, r*scale
                xs = np.clip(np.rint(sx+sr*np.cos(angles)).astype(int), 0, small.shape[1]-1)
                ys = np.clip(np.rint(sy+sr*np.sin(angles)).astype(int), 0, small.shape[0]-1)
                outer_mean = np.mean(gradient[ys, xs])
                max_complete = min(sx, sy, small.shape[1]-1-sx, small.shape[0]-1-sy)
                outside_end = min(1.18*sr, max_complete-2)
                outside_scores = []
                if outside_end > 1.03*sr:
                    for test_r in np.arange(1.03*sr, outside_end+1, 1.0):
                        tx = np.clip(np.rint(sx+test_r*np.cos(angles)).astype(int),
                                     0, small.shape[1]-1)
                        ty = np.clip(np.rint(sy+test_r*np.sin(angles)).astype(int),
                                     0, small.shape[0]-1)
                        outside_scores.append(np.mean(gradient[ty, tx]))
                if sr > 0.28*short:
                    search = np.arange(inner_radius_fraction[0]*sr,
                                       inner_radius_fraction[1]*sr+1, 1.0)
                    scores = []
                    for test_r in search:
                        tx = np.clip(np.rint(sx+test_r*np.cos(angles)).astype(int),
                                     0, small.shape[1]-1)
                        ty = np.clip(np.rint(sy+test_r*np.sin(angles)).astype(int),
                                     0, small.shape[0]-1)
                        scores.append(np.mean(gradient[ty, tx]))
                    best_index = int(np.argmax(scores))
                    inner_score = scores[best_index]
                    outside_score = max(outside_scores, default=0.0)
                    if (inner_score >= inner_edge_dominance*max(outside_score, 1e-6)
                            and inner_score >= inner_edge_current_fraction*outer_mean):
                        r = search[best_index]/scale
                refined.append((x, y, r))
            circles = refined

    kept = []
    for x, y, r in circles:
        # 必须先检查检测圆的完整性，不能把裁切孔缩小后冒充完整孔。
        if not np.isfinite([x, y, r]).all() or r <= 0:
            raise ValueError("ROI参数不合法")
        if min(x-r, y-r, w-1-x-r, h-1-y-r) < image_margin:
            if key in manual_rois:
                raise ValueError("手动ROI接触图像边缘或超出图像")
            continue
        if key not in manual_rois:
            r *= roi_radius_scale
        # Hough返回顺序优先保留高票候选，避免同一孔多重圆和重叠ROI。
        if any(np.hypot(x-a, y-b) < r+c for a, b, c in kept):
            if key in manual_rois:
                raise ValueError("手动ROI互相重叠")
            continue
        kept.append((float(x), float(y), float(r)))

    # 同一批次、同一板型的后续照片，可用首图首ROI的“半径/短边”作为弱先验。
    # 在重叠候选去重后只剩一个圆时执行，因此不会因Hough返回多个同心候选而漏检。
    # 只有当前圆明显更大、且预期半径附近确实存在足够强的圆边缘时才改选内缘。
    if (key not in manual_rois and reference_guided_roi
            and expected_radius_fraction is not None and len(kept) == 1):
        x, y, r = kept[0]
        expected_r = expected_radius_fraction*min(h, w)
        if r > expected_r*(1+reference_radius_tolerance):
            gradient = cv2.magnitude(cv2.Sobel(small_raw, cv2.CV_32F, 1, 0, ksize=3),
                                     cv2.Sobel(small_raw, cv2.CV_32F, 0, 1, ksize=3))
            angles = np.linspace(0, 2*np.pi, 1080, endpoint=False)
            sx, sy, sr = x*scale, y*scale, r*scale
            search = np.arange(expected_r*(1-reference_radius_tolerance)*scale,
                               expected_r*(1+reference_radius_tolerance)*scale+1, 1.0)
            scores = []
            for test_r in search:
                tx = np.clip(np.rint(sx+test_r*np.cos(angles)).astype(int),
                             0, small.shape[1]-1)
                ty = np.clip(np.rint(sy+test_r*np.sin(angles)).astype(int),
                             0, small.shape[0]-1)
                scores.append(np.mean(gradient[ty, tx]))
            cx = np.clip(np.rint(sx+sr*np.cos(angles)).astype(int), 0, small.shape[1]-1)
            cy = np.clip(np.rint(sy+sr*np.sin(angles)).astype(int), 0, small.shape[0]-1)
            current_score = np.mean(gradient[cy, cx])
            # 克隆密集环也可能给出很高的圆周梯度。先找可靠的局部峰，再从中
            # 选择最接近首图预期半径者，而不是直接选择全局最强峰。
            local_peaks = [i for i in range(2, len(scores)-2)
                           if scores[i] == max(scores[i-2:i+3])
                           and scores[i] >= 0.25*current_score]
            if local_peaks:
                best_index = min(local_peaks,
                                 key=lambda i: abs(search[i]/scale-expected_r))
                kept = [(x, y, float(search[best_index]/scale))]

    # 编号顺序：近似同一行从左到右，再从上到下。
    ordered = []
    pending = sorted(kept, key=lambda q: q[1])
    while pending:
        top = pending[0][1]
        tolerance = 0.5 * min(q[2] for q in pending)
        row = [q for q in pending if q[1] <= top+tolerance]
        ordered.extend(sorted(row, key=lambda q: q[0]))
        pending = [q for q in pending if q not in row]
    return ordered


def analyze_image(path, key, reference_radius, expected_radius_fraction=None):
    gray, rgb = read_gray8(path)
    circles = detect_rois(gray, key, expected_radius_fraction)
    if _review is not None and _review.images is None:
        _review.record_frame(path, 0, [path], display=key, circles=circles, margin=image_margin)
    if not circles:
        raise ValueError("没有检测到完整ROI；检查半径范围/投票阈值或设置manual_rois")
    # 灰度图每像素8-bit；带红色文字的输出必须是RGB，每通道8-bit。
    result = np.full((*gray.shape, 3), 45, dtype=np.uint8)
    rows, roi_exports = [], []
    # 所有ROI统一到首图首ROI的直径。area_range及下列分割尺度都在这一基准坐标系中解释。
    target_r = max(1, int(round(reference_radius)))
    target_size = 2*target_r + 1
    for roi_id, (cx, cy, radius) in enumerate(circles, 1):
        # 仿射映射将当前圆心/半径变换到基准圆；不改变原图文件，仅用于分割与面积判断。
        scale = target_r / radius
        # ROI外保留3σ上下文供背景校正，导出单ROI图时再裁掉这些上下文。
        pad = int(np.ceil(3*background_sigma))
        standard_center = target_r + pad
        work_size = target_size + 2*pad
        to_standard = np.array([[scale, 0, standard_center-scale*cx],
                                [0, scale, standard_center-scale*cy]], dtype=np.float32)
        standard = cv2.warpAffine(gray, to_standard, (work_size, work_size),
                                  flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101).astype(np.float32)
        yy, xx = np.ogrid[:work_size, :work_size]
        roi = (xx-standard_center)**2 + (yy-standard_center)**2 <= target_r**2
        smooth = cv2.GaussianBlur(standard, (0, 0), 0.8)
        background = cv2.GaussianBlur(smooth, (0, 0), background_sigma)
        signal = np.maximum(background-smooth, 0)  # 深染克隆在这里变为亮信号
        values = signal[roi]
        threshold = max(min_contrast, float(filters.threshold_otsu(values))*threshold_factor)
        binary = (signal > threshold) & roi
        # 不用clear_border！接触圆形ROI边界的克隆仍参与统计。
        # 面积按ROI内可见部分计算；不会推测ROI外的未知面积。
        distance = ndi.distance_transform_edt(binary)
        if split_touching and binary.any():
            peaks = morphology.h_maxima(distance, peak_prominence) & binary
            markers, _ = ndi.label(peaks)
            components, n_components = ndi.label(binary)
            # h_maxima可能漏掉很小的连通块；每块至少补一个种子。
            next_id = int(markers.max())
            for region in measure.regionprops(components):
                sl = region.slice
                component = components[sl] == region.label
                if not np.any(markers[sl][component]):
                    local = np.where(component, distance[sl], -1)
                    py, px = np.unravel_index(local.argmax(), local.shape)
                    next_id += 1
                    markers[sl][py, px] = next_id
            labels = segmentation.watershed(-distance, markers, mask=binary)
        else:
            labels, _ = ndi.label(binary)
        # 先分离粘连，再按每个对象的面积筛选；上下限均包含。
        sizes = np.bincount(labels.ravel())
        accepted_ids = np.flatnonzero((sizes >= area_range[0]) & (sizes <= area_range[1]))
        accepted_ids = accepted_ids[accepted_ids != 0]
        accepted = np.isin(labels, accepted_ids)
        # 单ROI输出只来自原始图片的8-bit灰度像素：仅做尺度统一，不做背景校正、
        # 阈值、二值化、分水岭、面积筛选、边缘或文字标注；圆外填白色。
        roi_export = np.clip(standard[pad:pad+target_size, pad:pad+target_size],
                             0, 255).astype(np.uint8)
        export_yy, export_xx = np.ogrid[:target_size, :target_size]
        export_circle = ((export_xx-target_r)**2 +
                         (export_yy-target_r)**2 <= target_r**2)
        roi_export[~export_circle] = 255
        roi_exports.append((roi_id, Image.fromarray(roi_export)))

        # 把标准坐标中的每个分水岭标签分别反投影到原图。必须保留label编号，
        # 不能先合并成bool，否则相接克隆之间的分水岭边界会消失。
        to_original = cv2.invertAffineTransform(to_standard)
        accepted_labels = np.where(accepted, labels, 0).astype(np.float32)
        labels_original = cv2.warpAffine(accepted_labels, to_original,
                                         (gray.shape[1], gray.shape[0]),
                                         flags=cv2.INTER_NEAREST).astype(np.int32)
        accepted_original = labels_original > 0
        original_roi = (np.ogrid[:gray.shape[0], :gray.shape[1]][1]-cx)**2 + \
                       (np.ogrid[:gray.shape[0], :gray.shape[1]][0]-cy)**2 <= radius**2
        result[original_roi] = 0
        result[accepted_original & original_roi] = 255
        # 黄色线包含每个对象的外轮廓，也包含相邻标签间真正的分水岭。
        # 加粗仅用于显示，不参与面积判断、标签筛选或计数。
        if watershed_boundary_width > 0:
            boundaries = segmentation.find_boundaries(labels_original, mode="inner")
            boundaries &= original_roi
            if watershed_boundary_width > 1:
                size = 2*watershed_boundary_width-1
                boundaries = cv2.dilate(boundaries.astype(np.uint8),
                                        np.ones((size, size), np.uint8)).astype(bool)
                boundaries &= original_roi
            result[boundaries] = (255, 220, 0)
        total_area = int(sizes[accepted_ids].sum())
        rows.append([path.stem, roi_id, len(accepted_ids), total_area])

    output = Image.fromarray(result)
    overlay = Image.fromarray(rgb)
    font_size = max(18, round(min(gray.shape)*0.022))
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", font_size)
    except OSError:
        font = ImageFont.load_default(size=font_size)
    for canvas in (output, overlay):
        draw = ImageDraw.Draw(canvas)
        for (_, roi_id, count, _), (x, y, r) in zip(rows, circles):
            draw.ellipse((x-r, y-r, x+r, y+r), outline=(0, 255, 100),
                         width=max(2, round(min(gray.shape)/700)))
            draw.text((x, y), f"ROI {roi_id}\nN = {count}", font=font,
                      fill=(255, 0, 0), anchor="mm", align="center",
                      stroke_width=1, stroke_fill=(255, 255, 255))
    return rows, output, overlay, roi_exports

# %% [cell 3]
# ===== 批处理：先验证路径与输出重名，再执行用户指定的清理 =====
if mode not in ("all", "test"):
    raise ValueError('mode只能为"all"或"test"')
if not (0 < area_range[0] <= area_range[1]):
    raise ValueError("面积上下限不合法")
if not (0 < radius_fraction[0] < radius_fraction[1] <= 0.5):
    raise ValueError("半径比例须满足0 < 下限 < 上限 <= 0.5")
if not (0 < inner_radius_fraction[0] < inner_radius_fraction[1] < 1):
    raise ValueError("培养皿内缘搜索比例须满足0 < 下限 < 上限 < 1")
if not (0 < reference_radius_tolerance < 0.5):
    raise ValueError("首图ROI半径容差须在0到0.5之间")
if not (inner_edge_dominance > 1 and 0 < inner_edge_current_fraction < 1
        and 0 < roi_radius_scale <= 1
        and background_sigma > 0 and peak_prominence > 0
        and threshold_factor > 0 and min_contrast >= 0 and image_margin >= 1
        and isinstance(watershed_boundary_width, int) and watershed_boundary_width >= 0):
    raise ValueError("ROI或分割参数不合法")
parent_dir = Path(parent_dir).expanduser().resolve(strict=True)
if not parent_dir.is_dir():
    raise ValueError("母路径不是文件夹")
extensions = {".jpg", ".jpeg", ".tif", ".tiff", ".png"}
all_subfolders = sorted((p for p in parent_dir.rglob("*")
                         if not p.relative_to(parent_dir).parts[0].startswith(".ddrimage-")
                         and p.is_dir() and not p.is_symlink() and not p.is_junction()
                         and p.resolve().is_relative_to(parent_dir)),
                        key=lambda p: str(p).casefold())
if not all_subfolders:
    raise ValueError("母文件夹内没有子文件夹")

folders = (all_subfolders if recursive else
           sorted((p for p in parent_dir.iterdir()
                   if not p.name.startswith(".ddrimage-") and p.is_dir() and not p.is_symlink() and not p.is_junction()),
                  key=lambda p: str(p).casefold()))
correcting = _review is not None and _review.images is not None
if mode == "test" and not correcting:
    folders = folders[:1]  # 严格采用排序后的第一个子文件夹，不自动跳到其他文件夹
jobs = []
for folder in folders:
    images = sorted((p for p in folder.iterdir() if p.is_file() and not p.is_symlink()
                     and p not in _generated_images
                     and p.suffix.lower() in extensions
                     and not p.name.lower().endswith("colony.tif")), key=lambda p: p.name.casefold())
    if correcting:
        images = [path for path in images if _review.includes(path)]
        if not images:
            continue
        for path in images:
            frame = _review.frame_key(path, 0)
            edit = _review.edits.get(frame, {})
            circles = edit.get("circles", _review.frames[frame]["circles"])
            excluded = set(edit.get("excluded_roi", []))
            manual_rois[_review.relative(path)] = [circle for i, circle in enumerate(circles) if i not in excluded]
    elif mode == "test":
        images = images[:1]
        if not images:
            raise ValueError(f"第一个子文件夹没有输入图片：{folder}")
    stems = [p.stem.casefold() for p in images]
    if len(stems) != len(set(stems)):
        raise ValueError(f"{folder}有同前缀不同扩展名图片，会覆盖输出；请先改名")
    jobs.append((folder, images))

# 首图首ROI是全批次的像素尺度标准；若它无法识别则停止，避免使用错误的面积基准。
reference_candidates = [(folder, images[0]) for folder, images in jobs if images]
if not reference_candidates:
    raise ValueError("没有找到可分析的输入图片")
calibration = _review.calibration if _review is not None else None
if calibration is None:
    for _, reference_path in reference_candidates:
        reference_key = reference_path.relative_to(parent_dir).as_posix()
        reference_gray, _ = read_gray8(reference_path)
        reference_circles = detect_rois(reference_gray, reference_key)
        if reference_circles:
            radius = reference_circles[0][2]
            calibration = [radius, radius/min(reference_gray.shape)]
            print(f"面积基准：{reference_key} 的 ROI 1")
            break
    if _review is not None:
        _review.calibration = calibration
reference_radius, reference_radius_fraction = calibration or (None, None)

failed_count = 0
for folder, images in jobs:
    result_folder = ddrimage_output / folder.relative_to(parent_dir)
    result_folder.mkdir(parents=True, exist_ok=True)
    number_csv_path = result_folder / f"{folder.name}_number_colony.csv"
    area_csv_path = result_folder / f"{folder.name}_area_colony.csv"
    rows = []
    for path in images:
        key = path.relative_to(parent_dir).as_posix()
        if _test_preview is not None:
            _test_preview.update(source=str(path), pages=1, colony=True, images=[])
        if correcting and key in manual_rois and not manual_rois[key]:
            rows.append([path.stem, "", "", ""])
            print(f"{key}: 没有保留可统计的 ROI，本图统计留空。")
            continue
        try:
            image_rows, output, overlay, roi_exports = analyze_image(
                path, key, reference_radius, reference_radius_fraction)
            output.save(result_folder / (path.stem + "_colony.tif"), compression="tiff_deflate")
            overlay.save(result_folder / (path.stem + "_roi_check_colony.tif"), compression="tiff_deflate")
            for roi_id, roi_image in roi_exports:
                roi_image.save(result_folder / f"{path.stem}_ROI{roi_id}_colony.tif",
                               compression="tiff_deflate")
            rows.extend(image_rows)
            print(f"{key}: " + ", ".join(f"ROI {r[1]} = {r[2]}" for r in image_rows))
            if _test_preview is not None:
                variants = [("克隆计数与分水岭", f"{path.stem}_colony.tif"),
                            ("原图 ROI 标注", f"{path.stem}_roi_check_colony.tif")]
                variants.extend((f"ROI {roi_id} · 统一尺度区域", f"{path.stem}_ROI{roi_id}_colony.tif")
                                for roi_id, _ in roi_exports)
                _test_preview["images"] = [[title, (result_folder / file).relative_to(ddrimage_output).as_posix()]
                                           for title, file in variants]
        except Exception as exc:
            # 无ROI/读图失败不能记成0，否则会被误解为实验阴性。
            rows.append([path.stem, "", "", ""])
            failed_count += 1
            warnings.warn(f"{key}: {type(exc).__name__}: {exc}")
    with number_csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["image", "ROI", "colony_count"])
        writer.writerows([row[:3] for row in rows])
    with area_csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["image", "ROI", "colony_total_area_px2"])
        writer.writerows([[row[0], row[1], row[3]] for row in rows])
print(f"完成：{len(jobs)}个子文件夹；失败图片：{failed_count}张。")
if failed_count and (_review is None or correcting or not _review.frames):
    raise RuntimeError("部分或全部图像处理失败。详情请见运行日志。")
if failed_count:
    print("未识别的 ROI 已保留为空值，可在矫正窗口中手动圈定。")

# %% [cell 4]
