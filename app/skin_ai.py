"""AI DIY prepares a visual brief, then renders through an explicitly private route."""
import json
from pathlib import Path
import re

from PIL import Image, ImageOps
from ai_analysis import AnalysisEngine
from ai_cancel import check_cancelled
from ai_store import AIStore
import image_routes
from skins import MAX_IMAGE, color, decode_image, jpeg, pack_metadata

IMAGE_LABELS = {**image_routes.ROUTES,'openai':'OpenAI API · GPT Image','codex':'Codex 本机 · GPT Image'}
EXCLUSIONS = '排除色情、毒品、暴力、犯罪、血腥和政治元素，包括相关符号与人物。即使原始要求包含这些元素，也只保留其它视觉要求。'
PLAN = ('为桌面软件设计视觉皮肤。根据用户文字要求和参考配色，返回单个 JSON 对象，字段为 prompt 和 accent。'
        'prompt 是不超过 600 字的宽幅背景插画描述；只描述画面中应出现的元素，不在描述中复述排除规则。'
        'accent 是适合用白字的深色主题色，格式 #RRGGBB。'
        '请为桌面图标和文字留出安静的空间，主体宜靠右。'
        + EXCLUSIONS)
IMAGE_INSTRUCTIONS = ('生成一张桌面主题背景图，返回图像文件。用构图和留白为软件界面保留可读性。'
                      + EXCLUSIONS)
RESTRICTED_WORDS = re.compile(
    r'色情|裸露|淫秽|裸[体照]|性行为|毒品|吸毒|贩毒|海洛因|冰毒|可卡因|大麻|'
    r'暴力|殴打|枪击|砍杀|虐待|处决|犯罪|入室盗窃|抢劫|绑架|杀人|'
    r'血腥|血泊|喷血|残肢|断肢|肢解|政治|政党|竞选|选举海报|'
    r'\b(?:porn(?:ography)?|nudit[y]|nude|cocaine|heroin|methamphetamine|drug trafficking|'
    r'violent|violence|murder|assault|robbery|crime|gore|gory|dismemberment|political campaign|politician)\b',
    re.IGNORECASE,
)


def private_config(store, provider):
    if provider in image_routes.ROUTES:
        cfg = image_routes.config(store.database,provider,'personal')
        ready = cfg['enabled'] and cfg['key']
    else:
        cfg = store.config(provider,'personal')
        ready = AIStore.channel_ready(cfg)
    if not ready or cfg.get('route')!='personal':
        raise ValueError('AI DIY 生图仅支持个人 API 直连和 Codex 本机连接。请在「AI部署」开启此个人通道；课题组授权不能用于生成皮肤图片。')
    return cfg


def reference_colors(paths):
    """Extract swatches only. No reference object, pixel layout or text is sent."""
    palettes = []
    for path in paths:
        check_cancelled()
        try:
            with Path(path).open('rb') as stream:
                image = decode_image(stream.read(MAX_IMAGE+1))
        except OSError:
            raise ValueError(f'无法打开参考图「{Path(path).name}」。请重新添加。') from None
        image.thumbnail((128,128),Image.Resampling.LANCZOS)
        quantized = image.quantize(colors=5)
        palette = quantized.getpalette()
        swatches = ['#%02X%02X%02X' % tuple(palette[index*3:index*3+3])
                    for _,index in sorted(quantized.getcolors(),reverse=True)]
        palettes.append(' '.join(swatches))
    return ' / '.join(palettes)


def generate_pack(store, text_provider, text_route, image_provider, name, request, paths):
    cfg = private_config(store,image_provider)
    palette = reference_colors(paths)
    engine = AnalysisEngine(store,text_provider,route=text_route)
    reply = engine.call(PLAN, json.dumps({"内容要求":request,"参考配色":palette},ensure_ascii=False))
    try:
        block = re.search(r'\{.*\}', reply, re.S)
        plan = json.loads(block.group()) if block else {}
        prompt = plan.get('prompt','') if isinstance(plan,dict) else ''
        if not isinstance(prompt,str) or not prompt.strip():
            raise ValueError
    except ValueError:
        raise ValueError('文字模型没有返回可用的绘图提示，请调整要求后重试。') from None
    check_cancelled()
    fresh = private_config(store,image_provider)
    if fresh != cfg:
        raise ValueError('生图账户或配置已变化，请重新发起任务。')
    parts = re.split(r'[。！？\n；;]+',prompt)
    safe_description = '。'.join(part.strip() for part in parts if part.strip() and not RESTRICTED_WORDS.search(part))
    if not safe_description:
        safe_description = '用干净的柔和渐变与自然光影设计背景，主体靠右，中央留白。'
    prompt = (IMAGE_INSTRUCTIONS+'\n'+safe_description[:640]+'\n参考配色：'+palette).strip()[:1000]
    if image_provider in image_routes.ROUTES:
        raw = image_routes.generate_on_channel(store.database,image_provider,prompt,
                {'images':[]},lambda:False,None,True,cfg)
    else:
        renderer = AnalysisEngine(store,image_provider,route='personal')
        raw = renderer.draw_pixels(prompt,{'mode':'images','images':[],'project':True},IMAGE_INSTRUCTIONS)
    check_cancelled()
    wallpaper = decode_image(raw)
    wallpaper = ImageOps.fit(wallpaper,(1536,960),Image.Resampling.LANCZOS,centering=(.58,.48))
    accent = color(plan.get('accent'),'#785339')
    files = {'wallpaper.jpg':jpeg(wallpaper,(1536,960))}
    cards = []
    for y in (0,320,560):
        for x in (0,420,1000):
            member = f'cards/{len(cards):02}.jpg'
            region = wallpaper.crop((x,y,x+536,y+400))
            files[member] = jpeg(region,(600,400),86)
            cards.append(member)
    meta = pack_metadata(dict(name=name,accent=accent,wallpaper='wallpaper.jpg',cards=cards))
    return meta,files

