"""Apply model-proposed plotting scripts through a closed command API."""
import ast
import math
import re
import sys
from functools import lru_cache
from pathlib import Path
from figure_data import CHART_TYPES, STAT_TESTS
from figure_canvas import LAYOUT, layout_command
from figure_python import compile_plot_script

COMMANDS = {
    'set_title': ('title',str),
    'set_xlabel': ('xlabel',str),
    'set_ylabel': ('ylabel',str),
    'set_font_size': ('font_size',(6,8)),
    'set_stroke': ('stroke',(.5,1.5)),
    'set_spacing': ('gap',(2,10)),
    'set_palette': ('colors',list),
    'set_gridlines': ('grid',bool),
    'set_plot_height': ('plot_height',(72,300)),
    'use_backend': ('backend',('reportlab','matplotlib')),
    'set_chart_type': ('chart_kind',tuple(CHART_TYPES)),
    'set_statistical_test': ('statistical_test',tuple(STAT_TESTS)),
}
STAT_ONLY = {'set_xlabel','set_ylabel','set_palette','set_gridlines','set_plot_height'}
TABLE_ONLY = {'set_chart_type','set_statistical_test'}
GUIDANCE = """根据用户要求修改软件当前的科研绘图脚本。只输出 JSON，字段 script（Python 绘图脚本字符串）和 note（简述修改）。
软件在本地运行受限 Python 绘图脚本，生成可编辑 PDF／SVG。支持局部变量、列表、索引、数值运算、简单 if、for 循环、range、enumerate、len。
可用 import matplotlib.pyplot as plt、import numpy as np、import matplotlib.colors as mc（须用别名）。
可用 import math as m（sqrt/log/log10/sin/cos）和 import statistics as s（mean/median/stdev）。
这些模块仅开放下列函数。画布坐标是以左上角为原点的 pt，不是统计数据坐标：
plt.text(x,y,"A",color="#222222",ha="left",fontsize=8)，plt.plot([x1,x2],[y1,y2],color="#222222")，
plt.scatter([x1,x2],[y1,y2],color="#222222",radius=1.5)，plt.rectangle(x,y,w,h,color="#222222",fill=False)，
plt.annotate("label",xy=[x,y],xytext=[label_x,label_y],color="#222222")。
np.linspace(start,stop,num=10)、np.clip(value,lower,upper)、mc.to_hex("steelblue")；无需导出文件，软件自动渲染。
canvas_width()、canvas_height()、group_anchor("对象id") 读取当前画布尺寸和锚点，坐标可用公式推导。
例如 x=canvas_width()；resize_canvas([x+32,canvas_height()])；plt.text(x+2,18,"A",fontsize=8)（实际脚本用换行）。
没有文件、网络、系统命令、任意模块或任意对象方法访问。
WB／gel 和 IF 可使用当前项目素材：figure.scene.source_images 是既有选区的 ID，local_images 是项目内已添加的原图 ID。
rebuild_canvas([宽pt,高pt]) 从空白画布重新排版。用 plt.imshow("素材ID",x,y,width) 或
place_image(["素材ID",x,y,width]) 放置原图／现有选区，高度按原比例计算，像素、亮度和对比度保持原样。
from PIL import Image 后可用 Image.size("素材ID") 取得 [像素宽,像素高]。选区已有的比例尺随图保留。
自定义排版后，标签和外框须按需添加。添加的文字和图形不要覆盖实验原图，重排请完整保留用户需要的实验组。
所有模式可调用 set_title("Title"), set_font_size(7), set_stroke(0.7)。
普通表格可调用 set_spacing(3)。WB／gel 的上下条带间距固定为 1 pt，IF 截图的上下及左右间距固定为 1.5 pt。
统计图（qPCR、Excel／CSV）还可调用 use_backend("matplotlib") 或 use_backend("reportlab"),
set_xlabel("X axis"), set_ylabel("Y axis"), set_palette(["#1AB3BC", "#FF7743"]), set_gridlines(True),
set_plot_height(110)。Matplotlib 是软件内置库，优先用它生成统计图。
统计作图还可调用 set_chart_type("box")、set_statistical_test("ranksum")。
图类型可用：bar（柱形）、line（分类折线）、scatter（分组散点）、box（Tukey 箱线）、
violin（小提琴）、histogram（直方图）、ecdf（累积分布）、xy（数值 X 散点）、xyline（数值 X 折线）、
heatmap（热图）、stacked（堆叠柱）。XY 和热图需要长表且有类别/X 列，XY 的 X 必须是数值。
按分组名称和处理条件作图可用 grouped_bar、grouped_separate、grouped_stacked、grouped_points、
grouped_scatter、grouped_aligned、grouped_box、grouped_violin、grouped_line、grouped_range。
统计方法可用：none、pooled（等方差 t）、welch、paired（配对 t）、ranksum（独立样本 Wilcoxon 秩和／Mann–Whitney U）、
signedrank（配对 Wilcoxon 符号秩）、anova（单因素独立样本）、kruskal（独立样本）。
仅当用户明确请求时切换图型或检验方法。只有按同一行配对的宽表或已指定配对编号的长表才允许配对。
若用户只说 Wilcoxon 且未说明配对，询问独立样本秩和或配对符号秩，保留当前统计方法。
字体 6–8 pt、线宽 0.5–1.5 pt、间距 2–10 pt，图表绘图区高度 72–300 pt。
只写本次要改变的调用，未指定的设置会保留。若只需建议，可返回空脚本。
不修改原始数据或像素。明确要求时可修改设置并由软件重新计算统计。
set_setting(["options.error","sd"])、set_setting(["options.comparison_mode","none"]) 修改已有设置。
figure.scene.elements 列出了可编辑图元。edit_element([ID,"text","新标签"])，edit_element([ID,"size",10])，
edit_element([ID,"x",30])，edit_element([ID,"y",50])，edit_element([ID,"width",60])。
也可修改颜色、角度、半径与线段终点 x2/y2。截图改变宽高时维持原始像素和长宽比。
用户要求的方法只能由应用实际计算。不得虚构比例尺、数值或检验结果。
WB／gel 可调用 set_wb_mass([条带编号, 分子量数值, "predicted"])，或使用 "measured" 表示用户明确给出的实测值。
例如 set_wb_mass([1, 42.7, "predicted"]) 会在第 1 条条带右边实际绘出「≈42.7 kDa (pred.)」。
条带编号从 1 开始，见 figure.panels 内的 panel_index。明确要求且有依据时，必须输出对应的命令，不要只建议手工修改。
可以用 clear_wb_mass(1) 清除第 1 条右侧标注。保留其它条带，不改图片像素。
依据构建体或标签分子量推算的值必须用 predicted，note 说明依据。条件或数值不明时不得编造。
通用画布 API 使用左上为原点的 pt 坐标。figure.scene 提供画布尺寸及可操作对象的 id 和锚点。
swap_groups(["channel:pATM","channel:pNBS1"]) 交换任意两个同方向对象。order_groups(["对象1","对象2","对象3"]) 按给定顺序占用这些对象当前的位置。
move_group(["对象id",dx,dy]) 移动整个对象，包括标签、原始像素和比例尺；可对多个对象分别移动。
IF 可调用 set_if_scale(["all",5])，按各处理组已保存的像素／μm 标定给所有图块添加 5 μm 比例尺。
要求既交换通道又加比例尺时，同时输出 swap_groups 和 set_if_scale。缺少标定时不要猜测像素尺寸。
resize_canvas([width,height]) 可扩大画布，add_text([x,y,"文字"])、add_line([x1,y1,x2,y2,"#222222"])、
add_rectangle([x,y,w,h,"#222222"]) 可添加可编辑图元。实验图像保持完整，不能重叠或被新标注覆盖。
prior_revisions 按时间顺序提供历史需求和结果，近期记录包含已执行脚本。figure.scene 是当前已应用修改的画布，
随附 previous-ai-figure.pdf 时可对照上次保存的 AI 图。根据上下文理解「继续」「刚才」等指代，只输出本轮增量。
默认保留之前的脚本排版，需要从默认布局重新设计时先调用 reset_layout()。
如果用户要求交换 IF 的两个通道，即使没有视觉输入，也可根据 figure.scene 中的名称直接调用 swap_groups。
如果可以用这些命令完成修改，请返回实际脚本，不要只给手工操作建议。
区域标记是用户当前所见预览的相对坐标，视觉附件中带橙色虚线框的那张标出了所选区域。
需要变更数据或原始选区时在 note 建议用户进行操作。"""


def read_script(script,mode,scene=None,source_size=None):
    wb_commands = {'set_wb_mass','clear_wb_mass'}
    operations = compile_plot_script(script,COMMANDS.keys()|wb_commands|LAYOUT|
                                     {'reset_layout','set_setting','set_if_scale'},scene,source_size)
    style = {}
    for command,args in operations:
        if command=='reset_layout' and not args:
            style['layout_commands'] = []
            style['reset_layout'] = True
            continue
        if len(args)!=1 or command=='reset_layout':
            raise ValueError('绘图脚本参数数量不正确，已保留上次图件。')
        value = args[0]
        if command=='set_if_scale':
            if (mode!='if' or not isinstance(value,list) or len(value)!=2 or value[0]!='all'
                    or type(value[1]) not in (int,float) or not 0<value[1]<=100000):
                raise ValueError('IF 比例尺命令格式为 set_if_scale(["all",长度 μm])。')
            style['if_scale_all'] = value[1]
            continue
        if command=='set_setting':
            if not isinstance(value,list) or len(value)!=2 or not isinstance(value[0],str):
                raise ValueError('set_setting 需要设置路径及新值。')
            style.setdefault('setting_changes',[]).append(value)
            continue
        if command in LAYOUT:
            if command in ('rebuild_canvas','place_image') and mode not in ('wb','if'):
                raise ValueError('原图排版命令适用于 WB／gel 与 IF 图。')
            style.setdefault('layout_commands',[]).append(layout_command(command,value))
            continue
        if command in wb_commands:
            index,label = wb_annotation(command,value,mode)
            style.setdefault('wb_masses',{})[index] = label
            continue
        if command in STAT_ONLY and mode not in ('table','qpcr'):
            raise ValueError('当前模式不支持脚本中的统计绘图操作，已保留上次图件。')
        if command in TABLE_ONLY and mode not in ('table','qpcr'):
            raise ValueError('AI 切换统计图型或检验仅适用于统计图。')
        key,rule = COMMANDS[command]
        if rule is str:
            valid = isinstance(value,str) and len(value)<=100
            value = ' '.join(value.split()) if valid else value
        elif rule is list:
            valid = isinstance(value,list) and 0<len(value)<=32 and all(
                isinstance(v,str) and re.fullmatch('#[0-9a-fA-F]{6}',v) for v in value)
        elif rule is bool:
            valid = type(value) is bool
        elif isinstance(rule[0],str):
            valid = value in rule
        else:
            valid = type(value) in (int,float) and rule[0]<=value<=rule[1] and math.isfinite(value)
        if not valid:
            raise ValueError(f'绘图脚本中的 {command} 参数无效，已保留上次图件。')
        style[key] = value
    return style


def wb_annotation(command,value,mode):
    if mode!='wb':
        raise ValueError('分子量标注命令仅用于 WB／gel。')
    if command=='clear_wb_mass' and type(value) is int and value>0:
        return value,''
    if command=='set_wb_mass' and isinstance(value,list) and len(value)==3:
        index,mass,basis = value
        if type(index) is int and index>0 and type(mass) in (int,float) and .01<=mass<=20000:
            if basis=='predicted':
                return index,f'≈{mass:g} kDa (pred.)'
            if basis=='measured':
                return index,f'{mass:g} kDa'
    raise ValueError('WB 条带编号、分子量或依据类型无效。')


@lru_cache(maxsize=4)
def renderer_source(mode):
    if getattr(sys,'frozen',False):
        path = Path(sys._MEIPASS)/'figure_scripts'/'figure_render.py'
    else:
        path = Path(__file__).with_name('figure_render.py')
    source = path.read_text(encoding='utf8')
    name = {'wb':'wb_scene','if':'if_scene','qpcr':'chart','table':'chart'}[mode]
    function = next(node for node in ast.parse(source).body if isinstance(node,ast.FunctionDef) and node.name==name)
    return ast.get_source_segment(source,function)


def renderer_bundle():
    folder = Path(sys._MEIPASS)/'figure_scripts' if getattr(sys,'frozen',False) else Path(__file__).parent
    return {name:(folder/name).read_text(encoding='utf8')
            for name in ('figure_render.py','figure_canvas.py','figure_data.py','figure_table_charts.py','wb_density.py')}
