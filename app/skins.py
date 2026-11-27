"""Read, normalize and share visual-only skin packs."""
from dataclasses import dataclass, field
from contextlib import contextmanager
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
from tempfile import TemporaryDirectory
from uuid import uuid4
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from PIL import Image, ImageColor, ImageOps, UnidentifiedImageError

BUILTIN = Path(__file__).resolve().parent / 'assets' / 'skins' / 'builtin.json'
HEX = re.compile(r'#[0-9a-fA-F]{6}\Z')
IDENTIFIER = re.compile(r'[0-9a-f]{32}\Z')
MAX_IMAGE = 16 * 1024 * 1024
MAX_PACK = 64 * 1024 * 1024
MAX_CARDS = 32
MAX_BUNDLE = 256 * 1024 * 1024
MAX_BUNDLE_SKINS = 200


@dataclass(frozen=True)
class Skin:
    id: str
    name: str
    accent: str
    sidebar: str
    surface: str
    sidebar_text: str
    selected: str
    selected_text: str
    note: str = ''
    directory: Path | None = None
    cards: tuple = field(default_factory=tuple)

    @property
    def wallpaper(self):
        return self.directory / 'wallpaper.jpg' if self.directory else None


@dataclass(frozen=True)
class SkinPreview:
    name: str
    card_count: int
    thumbnail: bytes


def blend(first, second, weight):
    a, b = ImageColor.getrgb(first), ImageColor.getrgb(second)
    return '#%02x%02x%02x' % tuple(round(x * (1-weight) + y * weight) for x,y in zip(a,b))


def color(value, fallback):
    return value.upper() if isinstance(value, str) and HEX.fullmatch(value) else fallback


def luminance(hex_color):
    rgb = [v/255 for v in ImageColor.getrgb(hex_color)]
    return sum(w*(v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4)
               for w,v in zip((.2126,.7152,.0722), rgb))


def readable_ink(value, background):
    for _ in range(12):
        if (luminance(background)+.05)/(luminance(value)+.05) >= 4.5:
            break
        value = blend(value, '#000000', .18)
    return value


def decode_image(data):
    if len(data) > MAX_IMAGE:
        raise ValueError('单张图片不得超过 16 MB。')
    try:
        with Image.open(BytesIO(data)) as image:
            if (image.format not in {'JPEG','PNG','WEBP','BMP','TIFF'}
                    or image.width*image.height > 30_000_000
                    or getattr(image, 'n_frames', 1) != 1):
                raise ValueError('请选择单帧静态图片，像素总数不超过 3000 万。')
            image = ImageOps.exif_transpose(image).convert('RGBA')
            base = Image.new('RGB', image.size, 'white')
            base.paste(image, mask=image.getchannel('A'))
            return base
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
        raise ValueError('无法读取皮肤图片。') from None


def jpeg(image, limit, quality=88):
    copy = image.copy()
    copy.thumbnail(limit, Image.Resampling.LANCZOS)
    stream = BytesIO()
    copy.save(stream, 'JPEG', quality=quality, optimize=True)
    return stream.getvalue()


def read_image_file(path):
    try:
        with Path(path).open('rb') as stream:
            return stream.read(MAX_IMAGE + 1)
    except OSError:
        raise ValueError(f'无法打开图片「{Path(path).name}」。') from None


def asset_name(value):
    if not isinstance(value,str) or '\\' in value or ':' in value:
        raise ValueError('皮肤包中包含无效的资源路径。')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or path.as_posix() != value or not path.suffix:
        raise ValueError('皮肤包中包含无效的资源路径。')
    return value


def pack_metadata(raw):
    if not isinstance(raw,dict) or raw.get('format',1) != 1:
        raise ValueError('此皮肤包的格式版本不受支持。')
    name = raw.get('name')
    if not isinstance(name,str) or not name.strip() or len(name.strip())>48 or any(ord(c)<32 for c in name):
        raise ValueError('请输入 1–48 个字符的皮肤名称。')
    cards = raw.get('cards',[])
    if not isinstance(cards,list) or len(cards)>MAX_CARDS:
        raise ValueError('一个皮肤包最多包含 32 张卡片背景。')
    palette = raw.get('palette',{})
    if not isinstance(palette,dict):
        raise ValueError('皮肤配色格式无效。')
    accent = color(raw.get('accent'),'#785339')
    return dict(format=1,name=name.strip(),accent=accent,wallpaper=asset_name(raw.get('wallpaper')),
                cards=[asset_name(item) for item in cards],
                palette={k:color(palette.get(k),default) for k,default in {
                    'sidebar_background':blend(accent,'#FFFFFF',.88),
                    'dialog_background':blend(accent,'#FFFFFF',.96),
                    }.items()})


@contextmanager
def open_packs(path):
    """Load the legacy single pack or an indexed archive of skin packs."""
    try:
        with ZipFile(path) as archive:
            entries = archive.infolist()
            names = {entry.filename for entry in entries}
            bundled = 'bundle.json' in names
            if len(names) != len(entries):
                raise ValueError('皮肤包中有重复资源。')
            if (len(entries) > (1 + MAX_BUNDLE_SKINS * (MAX_CARDS + 2) if bundled else 160)
                    or sum(item.file_size for item in entries) > (MAX_BUNDLE if bundled else MAX_PACK)):
                raise ValueError('皮肤包大小或资源数量超出限制。')

            def read(name, limit=MAX_IMAGE):
                entry = archive.getinfo(name)
                if (entry.is_dir() or stat.S_ISLNK(entry.external_attr >> 16)
                        or entry.file_size > limit or entry.flag_bits & 1):
                    raise ValueError('皮肤包中包含无法读取或过大的资源。')
                return archive.read(entry)

            def manifest(name):
                return json.loads(read(name, 64 * 1024).decode('utf-8-sig'))

            def load(prefix):
                metadata = pack_metadata(manifest(prefix + 'theme.json'))
                for asset in [metadata['wallpaper'], *metadata['cards']]:
                    if prefix + asset not in names:
                        raise ValueError(f'皮肤「{metadata["name"]}」缺少背景资源。')
                return metadata, lambda member: read(prefix + member)

            if bundled:
                index = manifest('bundle.json')
                prefixes = index.get('packs') if isinstance(index, dict) and index.get('format') == 1 else None
                if (not isinstance(prefixes,list) or not 1 <= len(prefixes) <= MAX_BUNDLE_SKINS
                        or any(not isinstance(prefix,str) or not re.fullmatch(r'packs/[0-9]{3}/',prefix) for prefix in prefixes)
                        or len(set(prefixes)) != len(prefixes)):
                    raise ValueError('皮肤包合集的索引格式无效。')
            else:
                prefixes = ['']
            yield [load(prefix) for prefix in prefixes]
    except (BadZipFile, KeyError, UnicodeError, NotImplementedError, RuntimeError) as error:
        raise ValueError('请选择包含有效背景图片的 ZIP 皮肤包。') from error


class SkinStore:
    def __init__(self, database):
        self.database = database
        self.directory = database.path.parent / 'skins'
        self.builtins = [Skin(**item) for item in json.loads(BUILTIN.read_text(encoding='utf-8-sig'))]

    def all(self):
        custom = []
        if self.directory.is_dir():
            for path in sorted(self.directory.iterdir()):
                if not IDENTIFIER.fullmatch(path.name) or not path.is_dir() or path.is_symlink():
                    continue
                try:
                    raw = pack_metadata(json.loads((path/'theme.json').read_text(encoding='utf-8')))
                    if not (path/'wallpaper.jpg').is_file():
                        continue
                except (OSError, ValueError):
                    continue
                accent = readable_ink(raw['accent'],'#FFFFFF')
                sidebar = raw['palette']['sidebar_background']
                surface = blend(raw['palette']['dialog_background'],'#FFFFFF',.69)
                selected = blend(accent,'#FFFFFF',.79)
                custom.append(Skin(path.name,raw['name'],accent,sidebar,surface,
                    readable_ink(accent,sidebar),selected,readable_ink(accent,selected),
                    '自定义皮肤',path,tuple(path/f for f in raw['cards'] if (path/f).is_file())))
        try:
            order = json.loads(self.database.get_settings().get('skin_pack_order','[]'))
        except ValueError:
            order = []
        positions = {value:index for index,value in enumerate(order) if isinstance(value,str)} if isinstance(order,list) else {}
        return self.builtins + sorted(custom,key=lambda skin:(positions.get(skin.id,len(positions)),skin.name.casefold()))

    def reorder(self, identifiers):
        self.database.save_settings({'skin_pack_order':json.dumps(identifiers)})

    def active(self):
        identifier = self.database.get_settings().get('active_skin','classic')
        return next((skin for skin in self.all() if skin.id == identifier), self.builtins[0])

    def select(self, skin):
        self.database.save_settings({'active_skin':skin.id})

    def receive(self, metadata, read):
        return self.receive_many([(metadata,read)])[0]

    def receive_many(self, packs):
        self.directory.mkdir(parents=True,exist_ok=True)
        with TemporaryDirectory(dir=self.directory,prefix='.import-') as temp:
            staged = []
            for metadata,read in packs:
                metadata = pack_metadata(metadata)
                stage = Path(temp) / uuid4().hex
                stage.mkdir()
                (stage/'wallpaper.jpg').write_bytes(jpeg(decode_image(read(metadata['wallpaper'])),(1920,1200)))
                metadata['wallpaper'] = 'wallpaper.jpg'
                images = metadata['cards']
                (stage/'cards').mkdir()
                metadata['cards'] = []
                for i,member in enumerate(images):
                    filename = f'cards/{i:02}.jpg'
                    (stage/filename).write_bytes(jpeg(decode_image(read(member)),(640,400)))
                    metadata['cards'].append(filename)
                (stage/'theme.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
                (stage/'arrow.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="10" viewBox="0 0 16 10"><path d="M3 3l5 4 5-4" fill="none" stroke="{accent}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>'.format(accent=readable_ink(metadata['accent'],'#FFFFFF')),encoding='ascii')
                staged.append(stage)
            installed = []
            try:
                for stage in staged:
                    installed.append(stage.rename(self.directory / stage.name))
            except OSError:
                for path in installed:
                    shutil.rmtree(path)
                raise
        by_id = {skin.id:skin for skin in self.all()}
        return [by_id[path.name] for path in installed]

    def preview_zip(self,path):
        with open_packs(path) as packs:
            return [SkinPreview(meta['name'],len(meta['cards']),jpeg(decode_image(read(meta['wallpaper'])),(384,240),85))
                    for meta,read in packs]

    def import_selected(self,path,indices):
        with open_packs(path) as packs:
            chosen = set(indices)
            if not chosen or not chosen.issubset(range(len(packs))):
                raise ValueError('请选择要缓存的皮肤包。')
            return self.receive_many(pack for index,pack in enumerate(packs) if index in chosen)

    def import_zip(self, path):
        with open_packs(path) as packs:
            if len(packs) != 1:
                raise ValueError('请在导入窗口选择要缓存的皮肤。')
            return self.receive(*packs[0])

    def export_zip(self, skin, path):
        self.export_selected([skin],path)

    def export_selected(self, skins, path):
        if not 1 <= len(skins) <= MAX_BUNDLE_SKINS or any(skin.directory is None for skin in skins):
            raise ValueError('请选择要导出的自定义皮肤。')
        packs = [[skin.directory/'theme.json',skin.wallpaper,*skin.cards] for skin in skins]
        if sum(asset.stat().st_size for pack in packs for asset in pack)>MAX_BUNDLE:
            raise ValueError('所选皮肤包超过 256 MB，请分批导出。')
        path = Path(path)
        with TemporaryDirectory(dir=path.parent,prefix='.skin-export-') as temp:
            stage = Path(temp)/'pack.zip'
            with ZipFile(stage,'w',ZIP_DEFLATED) as archive:
                prefixes = [f'packs/{index:03}/' for index in range(len(skins))] if len(skins)>1 else ['']
                if len(skins)>1:
                    archive.writestr('bundle.json',json.dumps({'format':1,'packs':prefixes}))
                for skin,assets,prefix in zip(skins,packs,prefixes):
                    for asset in assets:
                        archive.write(asset,prefix + asset.relative_to(skin.directory).as_posix())
            stage.replace(path)

    def delete(self, skin):
        if not skin.directory or not IDENTIFIER.fullmatch(skin.id) or skin.directory != self.directory/skin.id:
            raise ValueError('请选择自定义皮肤。')
        shutil.rmtree(skin.directory)
