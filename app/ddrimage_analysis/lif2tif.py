
# %% [cell 0]
from pathlib import Path
# 修改为存放 LIF 文件的文件夹；Windows 路径前保留 r。
input_dir = Path(r"/mnt/NC/image_python_202609/test")

# %% [cell 1]
output_dir = input_dir.with_name(input_dir.name + "_tif")
source_format = "leica"
lif2tif_mode = "merge_and_channels"  # "only_merge" 或 "merge_and_channels"
recursive = False  # True：同时搜索子文件夹
oir_groups = None

# 可选：Fiji 的 luts 文件夹。内置 LUT 无需填写；仅在 LIF 使用额外安装的 LUT 时设置。
# Ubuntu 常见示例：Path.home() / "Fiji.app/luts"
# Windows 常见示例：Path(r"C:\Fiji.app\luts")
fiji_lut_dirs = []

# 可选：从 LAS X 确认的显示参数/自定义 LUT。不设置时按有效位深全范围映射。
# 键：(相对于 input_dir 的 LIF 路径，series 编号，通道编号)，编号从 1 开始。
# 子文件夹分隔符使用 /。例如 ("sample.lif", 1, 2)。
# lut：N×3 的 RGB 表，数值范围 0..255，按低强度到高强度排列。
# 例如 np.loadtxt("exported_lut.csv", delimiter=",")；不要凭染料名称猜测颜色。
# gamma 的定义为 normalized ** (1 / gamma)，需自行核对 LAS X 的约定。
# inverted 覆盖 LIF 的 IsLUTInverted；lut 本身应为未反转的表。
channel_overrides = {
    # ("sample.lif", 1, 2): {"black": 100, "white": 3000, "gamma": 1.0},
    # ("sample.lif", 1, 3): {"lut": [[0, 0, 0], [255, 128, 0]]},
}

# %% [cell 2]
import itertools
import json
import re
import struct
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import tifffile
from liffile import LifFile
from ddrimage_worker import CONVERSION_EXTENSIONS
from ddrimage_oir_groups import oir_files, selected_groups

# 完整复现 ImageJ/Fiji 的内置 LUT。字典值均为从强度 0 到 255 的 256×3 RGB 表。
def _imagej_interpolate(points):
    points = np.asarray(points, dtype=np.float64)
    position = np.arange(256) * (len(points) / 256.0)
    lower = position.astype(int)
    upper = np.minimum(lower + 1, len(points) - 1)
    weight = (position - lower)[:, None]
    return ((1 - weight) * points[lower] + weight * points[upper]).astype(np.uint8)


_ramp = np.arange(256, dtype=np.uint8)
_zero = np.zeros(256, dtype=np.uint8)
_fire = _imagej_interpolate(np.column_stack([
    [0,0,1,25,49,73,98,122,146,162,173,184,195,207,217,229,240,252,255,255,255,255,255,255,255,255,255,255,255,255,255,255],
    [0,0,0,0,0,0,0,0,0,0,0,0,0,14,35,57,79,101,117,133,147,161,175,190,205,219,234,248,255,255,255,255],
    [0,61,96,130,165,192,220,227,210,181,151,122,93,64,35,5,0,0,0,0,0,0,0,0,0,0,0,35,98,160,223,255],
]))
_ice = _imagej_interpolate(np.column_stack([
    [0,0,0,0,0,0,19,29,50,48,79,112,134,158,186,201,217,229,242,250,250,250,250,251,250,250,250,250,251,251,243,230],
    [156,165,176,184,190,196,193,184,171,162,146,125,107,93,81,87,92,97,95,93,93,90,85,69,64,54,47,35,19,0,4,0],
    [140,147,158,166,170,176,209,220,234,225,236,246,250,251,250,250,245,230,230,222,202,180,163,142,123,114,106,94,84,64,26,27],
]))
_hue = (np.arange(256, dtype=np.float32) / np.float32(255)) % 1
_sector = np.floor(_hue * 6).astype(int)
_fraction = _hue * 6 - _sector
_q = np.floor((1 - _fraction) * 255 + 0.5).astype(np.uint8)
_t = np.floor(_fraction * 255 + 0.5).astype(np.uint8)
_spectrum = np.empty((256, 3), dtype=np.uint8)
for _i in range(256):
    _spectrum[_i] = ((255, _t[_i], 0), (_q[_i], 255, 0), (0, 255, _t[_i]),
                     (0, _q[_i], 255), (_t[_i], 0, 255), (255, 0, _q[_i]))[_sector[_i] % 6]
_rgb332 = np.column_stack([_ramp & 0xe0, (_ramp.astype(int) << 3) & 0xe0,
                           (_ramp.astype(int) << 6) & 0xc0]).astype(np.uint8)
_redgreen = np.zeros((256, 3), dtype=np.uint8)
_redgreen[:128, 0] = np.arange(128, dtype=np.uint8) * 2
_redgreen[128:, 1] = (np.arange(128, 256) * 2).astype(np.uint8)
_gray = np.column_stack([_ramp, _ramp, _ramp])
STANDARD_COLORS = {
    'red': np.column_stack([_ramp, _zero, _zero]),
    'green': np.column_stack([_zero, _ramp, _zero]),
    'blue': np.column_stack([_zero, _zero, _ramp]),
    'cyan': np.column_stack([_zero, _ramp, _ramp]),
    'magenta': np.column_stack([_ramp, _zero, _ramp]),
    'yellow': np.column_stack([_ramp, _ramp, _zero]),
    'fire': _fire, 'ice': _ice, 'spectrum': _spectrum,
    '3-3-2 rgb': _rgb332, 'redgreen': _redgreen, 'red/green': _redgreen,
    'grays': _gray, 'gray': _gray, 'grey': _gray, 'greys': _gray,
    'white': _gray, 'black': _gray,
}


def _load_fiji_lut(name, directories):
    """读取 Fiji 支持的 768-byte、NIH Image 或 3/4 列文本 LUT。"""
    wanted = {f'{name}.lut'.casefold(), f'{name.replace(" ", "_")}.lut'.casefold()}
    for directory in directories or []:
        directory = Path(directory).expanduser()
        if not directory.is_dir():
            continue
        path = next((p for p in directory.iterdir()
                     if p.is_file() and p.name.casefold() in wanted), None)
        if path is None:
            continue
        raw = path.read_bytes()
        if len(raw) == 768:
            values = np.frombuffer(raw, dtype=np.uint8)
            return np.column_stack([values[:256], values[256:512], values[512:]]), path
        if len(raw) >= 32 and raw[:4] == b'ICOL':
            count = struct.unpack('>H', raw[6:8])[0]
            if 2 <= count <= 256 and len(raw) >= 32 + 3 * count:
                values = np.frombuffer(raw[32:32 + 3 * count], dtype=np.uint8)
                table = np.column_stack([values[:count], values[count:2*count], values[2*count:]])
                return _imagej_interpolate(table), path
        if len(raw) == 970:  # 与 ImageJ 的 970-byte raw LUT 回退规则一致。
            values = np.frombuffer(raw[:768], dtype=np.uint8)
            return np.column_stack([values[:256], values[256:512], values[512:]]), path
        for delimiter in (',', None):
            try:
                table = np.genfromtxt(path, delimiter=delimiter, dtype=float)
            except (OSError, ValueError, UnicodeError):
                continue
            if table.ndim == 2 and table.shape[1] in (3, 4) and 256 <= len(table) <= 258:
                row = 1 if len(table) > 256 else 0
                col = 1 if table.shape[1] == 4 else 0
                table = table[row:row + 256, col:col + 3]
                if np.isfinite(table).all() and 0 <= table.min() and table.max() <= 255:
                    return table, path
        raise ValueError(f'Fiji LUT 文件格式无效：{path}')
    return None, None


def safe_name(value):
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value)).strip(' .')
    if not text:
        raise ValueError(f'图片名称无法用作文件名：{value!r}')
    if text.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                                      *(f'COM{i}' for i in range(1, 10)),
                                      *(f'LPT{i}' for i in range(1, 10))}:
        text = '_' + text
    return text


def xml_bool(value):
    value = str(value).strip().lower()
    if value in {'0', 'false', ''}:
        return False
    if value in {'1', 'true'}:
        return True
    raise ValueError(f'无法解释 IsLUTInverted={value!r}')


def channel_settings(image, relative_file, series_id, overrides, lut_dirs=None):
    # liffile 按 BytesInc 排序通道；XML 必须以同样方式排序，避免颜色错配。
    nodes = sorted(image.xml_element.findall(
        './Data/Image/ImageDescription/Channels/ChannelDescription'),
        key=lambda node: int(node.attrib['BytesInc']))
    count = image.sizes.get('C', 1)
    if len(nodes) != count:
        raise ValueError(f'通道元数据数量 {len(nodes)} 与图像通道数 {count} 不一致')
    result = []
    for index, node in enumerate(nodes, 1):
        attr = node.attrib
        override = dict(overrides.get((relative_file, series_id, index), {}))
        unexpected = set(override) - {'lut', 'black', 'white', 'gamma', 'inverted'}
        if unexpected:
            raise ValueError(f'未知覆盖参数：{unexpected}')
        lut_name = attr.get('LUTName', '').strip()
        if 'lut' in override:
            lut = np.asarray(override['lut'], dtype=np.float64)
            source = 'user_override'
        else:
            lut = STANDARD_COLORS.get(lut_name.casefold())
            if lut is not None:
                lut = lut.copy()
                source = f'Fiji_builtin:{lut_name}'
            else:
                lut, lut_path = _load_fiji_lut(lut_name, lut_dirs)
                source = f'Fiji_file:{lut_path}' if lut_path else None
            if lut is None:
                raise ValueError(
                    f'C{index}: 无法安全还原 LUTName={lut_name!r}；'
                    f'请设置 fiji_lut_dirs，或为 {(relative_file, series_id, index)} 提供原始 LUT 表。')
        if (lut.ndim != 2 or lut.shape[1] != 3 or len(lut) < 2
                or not np.isfinite(lut).all() or lut.min() < 0 or lut.max() > 255):
            raise ValueError('lut 必须为 N×3、N>=2、范围 0..255 的有限 RGB 数值')
        bits = int(attr['Resolution'])
        data_type = int(attr['DataType'])
        if data_type != 0 and not {'black', 'white'} <= override.keys():
            raise ValueError('非整数强度通道必须显式设置 black 和 white')
        if not 1 <= bits <= 64:
            raise ValueError(f'不支持的有效位深：{bits}')
        black = float(override.get('black', 0))
        white = float(override.get('white', (1 << bits) - 1))
        gamma = float(override.get('gamma', 1))
        if not np.isfinite([black, white, gamma]).all() or white <= black or gamma <= 0:
            raise ValueError('要求有限 white > black，且 gamma > 0')
        inverted = xml_bool(override.get('inverted', attr.get('IsLUTInverted', '0')))
        result.append(dict(channel=index, lut_name=lut_name, source=source,
                           lut=lut.tolist(), inverted=inverted, bits=bits,
                           black=black, white=white, gamma=gamma))
    return result


def render_channel(plane, settings):
    """返回浮点 RGB 0..255；最后写文件时才量化到 8 位。"""
    plane = np.asarray(plane)
    if plane.ndim != 2 or not np.isfinite(plane).all():
        raise ValueError('要求二维且不含 NaN/Inf 的强度图像')
    value = np.clip((plane.astype(np.float64) - settings['black']) /
                    (settings['white'] - settings['black']), 0, 1)
    value = value ** (1.0 / settings['gamma'])
    if settings['inverted']:
        value = 1.0 - value
    lut = np.asarray(settings['lut'], dtype=np.float64)
    position = value * (len(lut) - 1)
    lower = np.floor(position).astype(np.intp)
    upper = np.minimum(lower + 1, len(lut) - 1)
    weight = (position - lower)[..., None]
    return lut[lower] * (1 - weight) + lut[upper] * weight


def to_uint8(rgb):
    return np.rint(np.clip(rgb, 0, 255)).astype(np.uint8)


def output_names(image, mode):
    name = safe_name(image.name)
    names = [f'{name}.tif']
    if mode == 'merge_and_channels':
        names += [f'{name}_channel{c + 1}.tif' for c in range(image.sizes.get('C', 1))]
    return names


def export_series(image, folder, relative_file, series_id, mode, overrides, lut_dirs=None,
                  *, source_format="leica"):
    if mode not in {'only_merge', 'merge_and_channels'}:
        raise ValueError('lif2tif_mode 只能是 only_merge 或 merge_and_channels')
    sizes = dict(image.sizes)
    if 'S' in sizes or tuple(sizes)[-2:] != ('Y', 'X') or '?' in sizes:
        raise ValueError(f'此脚本不支持该维度布局：{sizes}（要求最内层为 Y,X，无 S）')
    settings = (image.settings if source_format == "olympus"
                else channel_settings(image, relative_file, series_id, overrides, lut_dirs))
    names = output_names(image, mode)
    outer_axes = [axis for axis in sizes if axis not in {'C', 'Y', 'X'}]
    page_count = 1
    for axis in outer_axes:
        page_count *= sizes[axis]
    if page_count < 1:
        raise ValueError('图像没有可导出的平面')
    # 每页 RGB，顺序为 outer_axes 的笛卡尔积，最右侧维度变化最快。
    # 页面描述保存维度索引和渲染信息，无需旁置 XML/JSON 文件。
    metadata = dict(source=relative_file, series=series_id, name=image.name,
                    sizes=sizes, mode=mode, channels=settings,
                    page_axes=outer_axes, page_count=page_count,
                    merge='additive RGB, clipped at 255', output='uint8 RGB TIFF')
    if source_format == "olympus":
        metadata["source_format"] = "OIR"
    bigtiff = page_count * (sizes['Y'] * sizes['X'] * 3 + 65536) >= 2**32 - 2**25
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if any((folder / name).exists() for name in names):
        raise FileExistsError('存在同名输出，已停止以避免覆盖')
    # liffile 2025.x removed LifImage.frame().  Read the series once (using a
    # read-only memory map where possible) and select planes by the dimension
    # labels reported by LifImage.dims.  This also avoids assuming a fixed
    # C/Z/T ordering for Leica files.
    data = image.asarray(out='memmap')
    dims = tuple(image.dims)
    if data.shape != tuple(sizes[axis] for axis in dims):
        raise ValueError(f'图像数据形状与维度元数据不一致：{data.shape}, {dims}, {sizes}')
    with ExitStack() as stack:
        writers = [stack.enter_context(tifffile.TiffWriter(folder / name, bigtiff=bigtiff))
                   for name in names]
        for page_index, indices in enumerate(itertools.product(*(range(sizes[a]) for a in outer_axes))):
            selection = dict(zip(outer_axes, indices))
            page_info = dict(page_index=page_index, indices_zero_based=selection)
            if page_index == 0:
                page_info.update(metadata)
            description = json.dumps(page_info, ensure_ascii=True)
            merged = np.zeros((sizes['Y'], sizes['X'], 3), dtype=np.float64)
            for channel, setting in enumerate(settings):
                frame_selection = dict(selection)
                if 'C' in sizes:
                    frame_selection['C'] = channel
                index = tuple(slice(None) if axis in {'Y', 'X'}
                              else frame_selection[axis] for axis in dims)
                plane = np.asarray(data[index])
                if plane.shape != (sizes['Y'], sizes['X']):
                    raise ValueError(f'二维帧大小不符：{plane.shape}')
                rgb = render_channel(plane, setting)
                merged += rgb
                if mode == 'merge_and_channels':
                    writers[channel + 1].write(
                        to_uint8(rgb), photometric='rgb', metadata=None,
                        description=json.dumps(dict(page_info, exported_channel=channel + 1),
                                               ensure_ascii=True))
            writers[0].write(to_uint8(merged), photometric='rgb', metadata=None,
                             description=description)
    return len(names)


class OlympusSeries:
    """Adapt the OIR reader to the same plane and channel layout as Leica."""

    def __init__(self, reader, name):
        self._reader = reader
        self.name = name
        self.sizes = {"C" if axis == "S" else axis: length for axis, length in reader.sizes.items()}
        self.dims = tuple(self.sizes)
        rgb = "S" in reader.sizes and reader.sizes["S"] == 3
        if "S" in reader.sizes and not rgb:
            raise ValueError("该 OIR 的颜色样本数不受支持。")
        bits = reader.bitspersample
        if reader.dtype.kind != "u" or not 1 <= bits <= reader.dtype.itemsize * 8:
            raise ValueError("该 OIR 的强度位深不受支持。")
        if self.dims[-2:] != ("Y", "X"):
            raise ValueError(f"无法将该 OIR 维度导出为 TIFF：{self.dims}")

        # In the pinned reader this is the acquisition order used by asarray.
        # The raw channel list also includes inactive detectors and reference channels.
        channels = {channel.id: channel for channel in reader.channels}
        guids = reader._get_ordered_channel_guids()
        if len(guids) != self.sizes.get("C", 1):
            raise ValueError("OIR 图像通道数与元数据不一致。")
        self.settings = []
        for index, guid in enumerate(guids, 1):
            channel = channels.get(guid)
            wavelengths = ((channel.start_wavelength, channel.end_wavelength) if channel else (None, None))
            valid = [wavelength for wavelength in wavelengths
                     if wavelength is not None and np.isfinite(wavelength) and wavelength > 0]
            center = sum(valid) / len(valid) if valid else None
            if rgb:
                color = ("red", "green", "blue")[index - 1]
            elif center is not None:
                color = "blue" if center < 490 else "green" if center < 565 else "red"
            elif channel and "dapi" in channel.name.casefold():
                color = "blue"
            else:
                color = "gray"
            self.settings.append(dict(
                channel=index, lut_name=color, source="OIR RGB" if rgb else "OIR channel metadata",
                channel_name=channel.name if channel else f"CH{index}", channel_guid=guid,
                wavelength_nm=list(wavelengths), lut=STANDARD_COLORS[color].tolist(),
                inverted=False, bits=bits, black=0., white=float((1 << bits) - 1), gamma=1.,
            ))

    def asarray(self, *, out):
        # Oirfile can fill missing planes with zeros. Report these acquisitions
        # rather than exporting synthetic dark signal as a measurement.
        frames = self._reader._pixel_map
        expected_planes = self._reader.size // (self.sizes["Y"] * self.sizes["X"])
        bytes_per_plane = self.sizes["Y"] * self.sizes["X"] * self._reader.dtype.itemsize
        if len(frames) != expected_planes or any(
            sum(length for _, _, length in blocks) < bytes_per_plane for blocks in frames.values()
        ):
            raise ValueError("OIR 存在缺失或不完整的图像平面。请检查原图及同目录的分卷文件。")
        return self._reader.asarray(out=out)


def run_batch(input_dir, output_dir, mode, recursive=False, overrides=None, lut_dirs=None, *,
              source_format="leica", oir_groups=None):
    if mode not in {'only_merge', 'merge_and_channels'}:
        raise ValueError('lif2tif_mode 只能是 only_merge 或 merge_and_channels')
    input_dir, output_dir = Path(input_dir).resolve(), Path(output_dir).resolve()
    if not input_dir.is_dir():
        raise FileNotFoundError(f'输入文件夹不存在：{input_dir}')
    candidates = input_dir.rglob('*') if recursive else input_dir.iterdir()
    extensions = CONVERSION_EXTENSIONS[source_format]
    files = sorted((p for p in candidates if p.is_file() and p.suffix.lower() in extensions),
                   key=lambda p: p.as_posix().casefold())
    if not files:
        raise FileNotFoundError(f'未找到所选格式的文件：{input_dir}')
    if source_format == "olympus" and oir_groups is not None:
        groups = selected_groups(oir_groups, oir_files(input_dir, recursive))
        print(f"已设置 {len(groups)} 个 OIR 分组，跳过 {len(files)-sum(len(items) for _, items in groups)} 个未分组文件。")
    else:
        stems = [p.stem.casefold() for p in files]
        if len(stems) != len(set(stems)):
            raise ValueError('存在同名前缀的原图；请分组设置或先重命名源文件')
        groups = [(path.stem, [path]) for path in files]
    existing = [output_dir / name for name, _ in groups if (output_dir / name).exists()]
    if existing:
        raise FileExistsError(f'输出子文件夹已存在；请换一个空输出目录或先移走旧结果：{existing}')
    output_dir.mkdir(parents=True, exist_ok=True)
    report = []
    for group_index, (name, paths) in enumerate(groups, 1):
        target = output_dir / name
        print(f'[{group_index}/{len(groups)}] {name} · {len(paths)} 个原始文件', flush=True)
        try:
            file_report, used_names = [], set()
            with TemporaryDirectory(prefix='.lif_export_', dir=output_dir) as temp:
                staging = Path(temp) / 'images'
                staging.mkdir()
                for path in paths:
                    relative = path.relative_to(input_dir).as_posix()
                    print(f'  {relative}', flush=True)
                    with ExitStack() as stack:
                        if source_format == "olympus":
                            from oirfile import OirFile

                            reader = stack.enter_context(OirFile(path, multifile=True))
                            serial = 1
                            stem = path.stem
                            while True:
                                image = OlympusSeries(reader, stem)
                                names = {filename.casefold() for filename in output_names(image, mode)}
                                if not names & used_names:
                                    break
                                serial += 1
                                stem = f"{path.stem}__{serial}"
                            images = [image]
                        else:
                            images = list(stack.enter_context(LifFile(path)).images)
                        if not images:
                            raise ValueError('文件没有可读取的图像 series')
                        names = [filename.casefold() for image in images for filename in output_names(image, mode)]
                        if len(names) != len(set(names)) or used_names.intersection(names):
                            raise ValueError('图片名称或通道后缀产生重名')
                        used_names.update(names)
                        for series_id, image in enumerate(images, 1):
                            count = export_series(image, staging, relative, series_id, mode,
                                                  overrides or {}, lut_dirs, source_format=source_format)
                            file_report.append(dict(file=relative, series=series_id, name=image.name,
                                                    status='ok', count=count, folder=str(target)))
                            print(f'  {image.name}: {count} 个 TIFF 文件', flush=True)
                staging.rename(target)
                report.extend(file_report)
        except Exception as exc:
            report.append(dict(file=name, status='error', error=str(exc)))
            print(f'  转换失败，未发布此分组：{exc}', flush=True)
    failed = sum(row['status'] == 'error' for row in report)
    print(f'结束：成功 {len(report) - failed} 个图像系列，失败 {failed} 个原始文件。输出：{output_dir}')
    if failed:
        print('详细错误保存在 Notebook 的 report 变量中。')
    return output_dir, report

# %% [cell 3]
result_dir, report = run_batch(
    input_dir=input_dir,
    output_dir=output_dir,
    mode=lif2tif_mode,
    recursive=recursive,
    overrides=channel_overrides,
    lut_dirs=fiji_lut_dirs,
    source_format=source_format,
    oir_groups=oir_groups,
)

if not report or any(row['status'] == 'error' for row in report):
    raise RuntimeError('部分或全部图像转换失败。详情请见运行日志。')

# %% [cell 4]
