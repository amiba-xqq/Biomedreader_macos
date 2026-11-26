"""Read, normalize and share visual-only skin packs."""
from dataclasses import dataclass, field
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
        return self.builtins + sorted(custom,key=lambda skin:skin.name.casefold())

    def active(self):
        identifier = self.database.get_settings().get('active_skin','classic')
        return next((skin for skin in self.all() if skin.id == identifier), self.builtins[0])

    def select(self, skin):
        self.database.save_settings({'active_skin':skin.id})

    def receive(self, metadata, read):
        metadata = pack_metadata(metadata)
        self.directory.mkdir(parents=True,exist_ok=True)
        identifier = uuid4().hex
        with TemporaryDirectory(dir=self.directory,prefix='.import-') as temp:
            stage = Path(temp) / identifier
            stage.mkdir()
            wallpaper = decode_image(read(metadata['wallpaper']))
            (stage/'wallpaper.jpg').write_bytes(jpeg(wallpaper,(1920,1200)))
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
            stage.rename(self.directory / identifier)
        return next(skin for skin in self.all() if skin.id == identifier)

    def import_zip(self, path):
        try:
            with ZipFile(path) as archive:
                entries = archive.infolist()
                if len(entries)>160 or sum(item.file_size for item in entries)>MAX_PACK:
                    raise ValueError('皮肤包解压后不得超过 64 MB。')
                names = [item.filename for item in entries]
                if len(set(names)) != len(names):
                    raise ValueError('皮肤包中有重复资源。')
                def read(name, limit=MAX_IMAGE):
                    entry = archive.getinfo(name)
                    if (entry.is_dir() or stat.S_ISLNK(entry.external_attr >> 16)
                            or entry.file_size > limit or entry.flag_bits & 1):
                        raise ValueError('皮肤包中包含无法读取或过大的资源。')
                    return archive.read(entry)
                return self.receive(json.loads(read('theme.json',64*1024).decode('utf-8-sig')),read)
        except (BadZipFile, KeyError, UnicodeError, json.JSONDecodeError, NotImplementedError):
            raise ValueError('请选择包含有效 theme.json 和背景图片的 ZIP 皮肤包。') from None

    def export_zip(self, skin, path):
        if skin.directory is None:
            raise ValueError('请选择自定义皮肤。')
        path = Path(path)
        with TemporaryDirectory(dir=path.parent,prefix='.skin-export-') as temp:
            stage = Path(temp)/'pack.zip'
            with ZipFile(stage,'w',ZIP_DEFLATED) as archive:
                for asset in [skin.directory/'theme.json',skin.wallpaper,*skin.cards]:
                    archive.write(asset,asset.relative_to(skin.directory).as_posix())
            stage.replace(path)

    def delete(self, skin):
        if not skin.directory or not IDENTIFIER.fullmatch(skin.id) or skin.directory != self.directory/skin.id:
            raise ValueError('请选择自定义皮肤。')
        shutil.rmtree(skin.directory)
