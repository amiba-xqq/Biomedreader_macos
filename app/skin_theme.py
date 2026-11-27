"""Apply one skin to the Qt palette, application styles and existing controls."""
from functools import lru_cache
import re

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QFrame, QPushButton, QToolButton

from skins import BUILTIN, blend, readable_ink

ICON_PATHS = {
    'search':'M20 20l-4-4 M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0z',
    'star':'M12 2l3 6 7 1-5 5 1 7-6-3-6 3 1-7-5-5 7-1z',
    'robot':'M5 9h14v10H5z M12 5v4 M3 12v4m18-4v4 M9 13h.1m5.9 0h.1 M10 16h4',
    'skin':'M12 3a9 9 0 1 0 0 18h2a2 2 0 0 0 0-4 2 2 0 1 1 0-4h3a4 4 0 0 0 4-4c-1-4-5-6-9-6z M7 10h.1M10 7h.1m4-.1h.1M6 14h.1',
    'settings':'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M19 5l-2 2M7 17l-2 2',
    'add':'M12 4v16M4 12h16', 'trash':'M4 7h16 M9 7V4h6v3M6 7l1 13h10l1-13 M10 10v7m4-7v7',
    'folder':'M3 5h6l2 3h10v11H3z', 'save':'M4 3h13l3 3v15H4z M8 3v6h8V3 M8 21v-8h8v8',
    'download':'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',
    'upload':'M12 16V4m-5 5 5-5 5 5M4 16v5h16v-5',
    'refresh':'M20 7v5h-5M20 12a8 8 0 1 0-2 5',
    'history':'M4 8a8 8 0 1 1 0 8M3 4v5h5 M12 7v5l4 2',
    'check':'M4 12l5 5L20 6', 'close':'M6 6l12 12M18 6L6 18',
    'edit':'M4 16l-1 5 5-1 12-12-4-4z M14 6l4 4',
    'copy':'M8 7h12v14H8z M16 7V3H4v14h4',
    'play':'M8 4l13 8-13 8z','stop':'M6 6h12v12H6z',
    'pluszoom':'M20 20l-4-4 M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0z M10 7v6m-3-3h6',
    'minuszoom':'M20 20l-4-4 M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0z M7 10h6',
    'fit':'M3 9V3h6m6 0h6v6m0 6v6h-6m-6 0H3v-6',
}
NAV = {'关键词方案':'search','收藏文章':'star','期刊目录':'journal','DDRimage':'image',
       'AI助手':'robot','AI部署':'cloud','反馈':'issue','皮肤':'skin','设置':'settings'}
ACTIONS = (
    ('history',('历史记录','历史结果')), ('minuszoom',('缩小',)), ('pluszoom',('放大',)),
    ('fit',('适合窗口','铺满窗口','恢复大小')), ('folder',('浏览','打开文件夹','打开结果','打开目录','选择文件','选择文件夹','重新关联')),
    ('trash',('删除','移除','清空')), ('upload',('导入','上传')), ('download',('导出','下载')),
    ('copy',('复制',)), ('refresh',('联网刷新','刷新','更新目录','撤销','重做')),
    ('star',('收藏','已收藏')), ('search',('检索','搜索','查找')),
    ('stop',('停止','取消AI','取消 AI')), ('play',('开始运行','全部处理','测试一张','本地预览')),
    ('spark',('AI DIY','生成','AI作图','AI 作图','AI总结')), ('add',('添加','新增','新建','创建')),
    ('save',('保存','应用')), ('edit',('编辑','矫正','修改')), ('settings',('设置','配置')),
    ('check',('确认','验证','检测','完成','确定','OK')), ('close',('关闭','取消','Cancel','Close')),
)


@lru_cache(maxsize=192)
def icon(key, color, active):
    from card_board import FEATURE_CARD_ICONS
    path = ICON_PATHS[key] if key in ICON_PATHS else FEATURE_CARD_ICONS[key]
    result = QIcon()
    for tone, mode, state in (
        (color,QIcon.Mode.Normal,QIcon.State.Off),
        (active,QIcon.Mode.Normal,QIcon.State.On),
        ('#978F89',QIcon.Mode.Disabled,QIcon.State.Off),
    ):
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
               f'<path d="{path}" fill="none" stroke="{tone}" stroke-width="1.6" '
               'stroke-linecap="round" stroke-linejoin="round"/></svg>')
        renderer = QSvgRenderer(QByteArray(svg.encode('ascii')))
        for size in (18,24,32):
            pixmap = QPixmap(size,size)
            pixmap.fill(Qt.GlobalColor.transparent)
            with QPainter(pixmap) as painter:
                renderer.render(painter)
            result.addPixmap(pixmap,mode,state)
    return result


def decorate(button, skin):
    if not isinstance(button,(QPushButton,QToolButton)):
        return
    label = button.text().replace('&','').strip()
    nav = button.objectName() == 'NavButton'
    key = NAV.get(label) if nav else getattr(button,'_skin_icon_key',None)
    if key is None:
        key = next((icon_name for icon_name,words in ACTIONS if label.startswith(words)),None)
    if not key or (not button.icon().isNull() and not getattr(button,'_themed_icon',None)):
        return
    secondary = bool(button.property('secondary'))
    if nav:
        color, active = (readable_ink(skin.accent, '#f2efe9') if skin.wallpaper else skin.sidebar_text), skin.selected_text
    else:
        color = readable_ink(skin.accent, '#FFFFFF') if secondary or isinstance(button,QToolButton) else '#FFFFFF'
        active = color
    marker = (key,color,active)
    if getattr(button,'_themed_icon',None) != marker:
        button.setIcon(icon(*marker))
        button.setIconSize(QSize(17,17))
        button._themed_icon = marker


def theme_palette(palette, skin):
    if skin.id == 'classic':
        return palette
    values = {
        'Window':skin.surface,'WindowText':'#252827','Base':'#FFFFFF',
        'AlternateBase':blend(skin.accent,'#FFFFFF',.96),'Text':'#252827',
        'Button':blend(skin.accent,'#FFFFFF',.91),'ButtonText':'#252827',
        'Highlight':skin.selected,'HighlightedText':skin.selected_text,
        'Accent':skin.accent,'Link':skin.accent,'LinkVisited':skin.accent,
        'ToolTipBase':'#FFFFFF','ToolTipText':'#292727','PlaceholderText':'#685E5A',
    }
    for group in (QPalette.ColorGroup.Active,QPalette.ColorGroup.Inactive,QPalette.ColorGroup.Disabled):
        for role, value in values.items():
            if group == QPalette.ColorGroup.Disabled and role in ('Text','WindowText','ButtonText'):
                value = '#706A66'
            palette.setColor(group,getattr(QPalette.ColorRole,role),QColor(value))
    return palette


def shade(skin, weight):
    return blend(skin.accent,'#FFFFFF',weight)


@lru_cache(maxsize=16)
def tone_map(skin):
    if skin.id == 'classic':
        return {}
    groups = {
        skin.accent: 'F28B25 E88225 F28C20 F28C28 F59A40 EB8729 EF942F EF8B26 C96812 A94F0D A74F0E 9E4E10 BD5B15 D96516 F28C25 B45309',
        blend(skin.accent,'#000000',.16): 'E98220 D77416 CC6810 DE7A1B CF863F E58B37 B45D13 BC631A 82400D E49550',
        shade(skin,.81): 'EDB780 F6B36B F2BE88 EACBB0 E4BFA0 E2C4A8 E8D5C4 D5AA83 E69C5A EDAB70 FAD9B5 FFE0BE',
        shade(skin,.90): 'FFF0DE FFF0DD FFE9D2 FFEBD3 FFF3E0 FFF3E6 F7F1EC F5F1EC EFEAE4 ECE5DD E9DED1 E3DBD2 D3C9BF',
        shade(skin,.96): 'FFF7ED FFF7EE FFF8EE FCFAF7 FFFDFA FFFAF3 FFF9EE',
        skin.surface: 'F7F8FA FFFDFC',
        '#333238': '30251D 402B1B 55351E',
        '#595359': '8B7768 827C76 756557 75685F 674C38 675445 744A2A',
    }
    return {legacy:replacement for replacement,legacy_colors in groups.items() for legacy in legacy_colors.split()}


def recolor(style, skin):
    mapping = tone_map(skin)
    return re.sub(r'#([0-9a-fA-F]{6})(?![0-9a-fA-F])',
                  lambda m: mapping.get(m[1].upper(),m[0]),style) if mapping else style


def qss_url(path):
    return 'url("' + path.as_posix().replace('"','\\"') + '")'


def application_style(base, skin):
    if skin.id == 'classic':
        return base
    style = recolor(base,skin)
    style += f"""
      QFrame#Sidebar {{ background:{skin.sidebar}; border-right:1px solid {shade(skin,.75)}; }}
      QLabel#Brand {{ color:{skin.sidebar_text}; }}
      QPushButton#NavButton {{ color:{skin.sidebar_text}; background:transparent; }}
      QPushButton#NavButton:hover {{ background:{blend(skin.sidebar,skin.selected,.32)}; border-color:{blend(skin.sidebar,skin.selected,.60)}; }}
      QPushButton#NavButton:checked, QPushButton#NavButton:pressed {{
          color:{skin.selected_text}; background:{skin.selected}; border-color:{skin.accent};
      }}
      QLabel#PageTitle, QLabel#SectionTitle {{ color:{skin.accent}; }}
      QPushButton:disabled {{ color:#6f6864; background:{shade(skin,.92)}; border-color:{shade(skin,.85)}; }}
      QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
          background:{skin.accent}; border:3px solid {shade(skin,.79)};
      }}
      QCheckBox::indicator:checked {{ border-radius:3px; }}
      QRadioButton::indicator:checked {{ border-radius:7px; }}
    """
    arrow = skin.directory/'arrow.svg' if skin.directory else BUILTIN.parent/f'{skin.id}.svg'
    if arrow.is_file():
        style += f'QComboBox::down-arrow, QComboBox::down-arrow:disabled, QDateEdit::down-arrow, QDateTimeEdit::down-arrow {{ image:{qss_url(arrow)}; }}'
    if skin.wallpaper:
        style += f'''QWidget[skinTransparent="true"] {{ background:transparent; }}
            QFrame#Sidebar {{ background:transparent; border:none; }}
            QLabel#Brand, QPushButton#NavButton {{ color:{readable_ink(skin.accent,'#f2efe9')}; }}
            QPushButton#NavButton:checked {{ color:{skin.selected_text}; }}'''
    return style


def apply_theme(interface, skin):
    app = QApplication.instance()
    app.biomed_skin = skin
    if hasattr(app,'_light_theme_controller'):
        app._light_theme_controller.enforce()
    interface.apply()
    interface.window.centralWidget().update()
