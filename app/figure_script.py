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
    'set_x_time_axis': ('x_time_axis',bool),
    'set_x_color_strip': ('x_color_strip',bool),
    'set_x_strip_gradient': ('x_strip_gradient',bool),
    'set_x_category_order': ('x_category_order','names'),
    'set_group_gaps': ('group_gaps','group gaps'),
    'set_wb_lane_centers': ('wb_lane_centers','lane centers'),
    'set_bar_outline_width': ('bar_outline_width',(.25,8)),
    'set_bar_outline_open_base': ('bar_outline_open',bool),
    'set_font_size': ('font_size',(6,8)),
    'set_wb_density_font_size': ('wb_density_font_size',(4,8)),
    'set_stroke': ('stroke',(.5,1.5)),
    'set_spacing': ('gap',(2,10)),
    'set_palette': ('colors',list),
    'set_gridlines': ('grid',bool),
    'set_plot_height': ('plot_height',(72,300)),
    'use_backend': ('backend',('reportlab','matplotlib')),
    'set_chart_type': ('chart_kind',tuple(CHART_TYPES)),
    'set_statistical_test': ('statistical_test',tuple(STAT_TESTS)),
}
STAT_ONLY = {'set_xlabel','set_ylabel','set_palette','set_gridlines','set_plot_height',
             'set_x_time_axis','set_x_color_strip','set_x_strip_gradient','set_x_category_order',
             'set_bar_outline_width','set_bar_outline_open_base'}
TABLE_ONLY = {'set_chart_type','set_statistical_test'}
GUIDANCE = """根据用户要求修改软件当前的科研绘图脚本。只输出 JSON，字段 script（Python 绘图脚本字符串）和 note（简述修改）。
软件在本地运行受限 Python 绘图脚本，生成可编辑 PDF／SVG。支持局部变量、列表、索引、数值运算、简单 if、for 循环、range、enumerate、len。
可用 import matplotlib.pyplot as plt、import numpy as np、import matplotlib.colors as mc（须用别名）。
可用 import math as m（sqrt/log/log10/sin/cos）和 import statistics as s（mean/median/stdev）。
这些模块仅开放下列函数。画布坐标是以左上角为原点的 pt，不是统计数据坐标：
plt.text(x,y,"A",color="#222222",ha="left",fontsize=8,rotation=90)，plt.plot([x1,x2],[y1,y2],color="#222222",linewidth=1)，
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
自定义排版后，标签和外框须按需添加。用户指定在图内的位置时，可在独立矢量层添加线和文字。重排请完整保留用户需要的实验组。
所有模式可调用 set_title("Title"), set_font_size(7), set_stroke(0.7)。
普通表格可调用 set_spacing(3)。WB／gel 的上下条带间距固定为 1 pt，IF 截图的上下及左右间距固定为 1.5 pt。
统计图（qPCR、Excel／CSV）还可调用 use_backend("matplotlib") 或 use_backend("reportlab"),
set_xlabel("X axis"), set_ylabel("Y axis"), set_palette(["#1AB3BC", "#FF7743"]), set_gridlines(True),
set_plot_height(110)。Matplotlib 是软件内置库，优先用它生成统计图。
分组折线图可调用 set_x_time_axis(True)，直接用分组中的数值时间作横轴，按实际间隔定位并去掉刻度的基因名前缀。
再调用 set_x_color_strip(True)，在刻度下绘制与每个时间点对齐的分段色条。
对已启用的横轴色条，调用 set_x_strip_gradient(True) 直接换成无分界的矢量渐变。
横轴类别重排调用 set_x_category_order(["ATM","BRCA1","BRCA2","PALB2"])。重排整组数据、重复点及统计标注。
四种模式的横向分类布局均可调用 set_group_gaps([[5,1]])，在左起第 5 项之后保留 1 个空位。
figure.scene.horizontal_groups 给出从 1 开始的编号、标签、中心坐标。多处空位可写成 [[2,1],[5,1]]，用 [] 清除。
WB／gel 中空位是原裁剪图里的空泳道。调整时保持源像素、标签顺序及灰度数值，顶端标签和灰度注释使用同一组泳道中心。
若用户或标定提供确切的不等距泳道中心，可调用 set_wb_lane_centers([0.06,0.15,0.24])，数值是相对条带宽度的横坐标，按左至右列出全部有标签的泳道，空泳道不列入。不要把空位加成样本或计算灰度。
例如两个 rep 均含 WT、D1、D2、D3、D4，且中间有一个空泳道，应使用 set_group_gaps([[5,1]])。
IF 横向空位留在整列之间。qPCR、Excel／CSV 的分类图在分类或样本之间保留空位，数值轴仍按实际数值定位。
柱体描边直接调用 set_bar_outline_width(2) 和 set_bar_outline_open_base(True) 去掉零基线一侧描边，保留已有填色和原边框颜色。
使用当前图上的类别原名。可仅指定一部分，其余类别按原顺序排在后面。数值时间轴始终按时间排序。
通用画布可以用 add_gradient([x,y,width,height,"#D8E8F5","#263F71"]) 放置横向矢量渐变，可添加多个色标。
例如 set_x_time_axis(True)、set_x_color_strip(True)、set_xlabel("G0 release time (h)")、
set_ylabel("Relative BRCA1-FL mRNA level")（每个调用单独一行）。这组命令保留系列、重复点、误差线及统计数值。
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
figure.scene.elements 的 editable_properties 列出了每个图元可修改的属性。文字图元用 edit_element([ID,"size",5]) 缩小字号，
用 edit_element([ID,"text","新标签"]) 改字。线段用 edit_element([ID,"stroke_width",1]) 改线宽。
按图元种类选择 ID，不要将字号应用到图片、线段或外框。可同时针对已有文字改字号，并在框选区用 add_label 和 add_line 添加新标注。
WB／gel 中要求条带下方的相对灰度数字缩小并排成一行，使用 set_wb_density_font_size(4.5)；不要逐个编辑带 wb_density_value 标记的数字。
若同一句还要求标注蛋白大小，同时另写一行 set_wb_mass，软件会按条带当前位置放置右侧短线和文字，不要把字号或文字设置用在条带图片或边框上。
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
add_rectangle([x,y,w,h,"#222222"]) 可添加可编辑图元。原始实验图像完整保留，不叠放两个实验图块。
单条线宽可用 add_line([x1,y1,x2,y2,"#222222",1])，竖排标注可用 add_label([x,y,"分组说明","#222222","middle",7,-90])。
add_label 最后一个参数为顺时针旋转角度，x/y 是旋转锚点；竖排标注用 middle 放在线段中点左侧。已有自定义画布直接追加标注，无需重建。
四种模式均可用 add_line_label([x1,y1,x2,y2,"文字","#222222",线宽pt,字号pt,偏移pt,角度]) 一次添加线和文字。
横线配横排字：add_line_label([20,30,60,30,"横排文字","#222222",1,7,4,0])，正偏移把字放在线上方。
竖线配竖排字：add_line_label([20,30,20,70,"竖排文字","#222222",1,7,4,-90])，正偏移把字放在左侧；负偏移放到另一侧。
用户要求在框选区加类似标注时，请用 marked_region_pt 推导端点坐标；这些绘图命令不会更改统计数据、灰度比值或源图像。
有框选区时，脚本可用 left,top,right,bottom = marked_region() 直接读它的坐标，不必猜像素或手抄数值。
需移动、靠拢已经绘制的标注时，四种模式都可用 transform_annotations([marked_region(),0,-3,1,0.64])。
参数依次为选区、向右位移 pt、向下位移 pt、横向间距倍数、纵向间距倍数。示例把框选的文字和处理矩阵圆点一起上移并缩小行距，保留列的横坐标。
该命令适用于已有文字和 AI 标注，保留字号、点大小与颜色。实验图像、数据散点及原有统计线保持原位。
横线、竖线及横排、竖排文字可放在画布内用户指定的位置。靠近边缘时可自动增加白边。figure.scene 的尺寸及元素位置使用命令坐标。
marked_region_pt 给出已转换的命令坐标 [左,上,右,下]，已扣除新增白边偏移；按该矩形定位画线与文字。
prior_revisions 按时间顺序提供历史需求和结果，最后一次可继续使用的记录包含已执行脚本。figure.scene 是当前画布，
随附 previous-ai-figure.pdf 时可对照上次保存的 AI 图。根据上下文理解「继续」「刚才」等指代，只输出本轮增量。
默认保留之前的脚本排版，需要从默认布局重新设计时先调用 reset_layout()。
如果用户要求交换 IF 的两个通道，即使没有视觉输入，也可根据 figure.scene 中的名称直接调用 swap_groups。
如果可以用这些命令完成修改，请返回实际脚本，不要只给手工操作建议。
区域标记是用户当前所见预览的相对坐标，视觉附件中带橙色虚线框的那张标出了所选区域。
Excel／CSV 散点图中，用户明确要求排除框选点时，由软件按散点位置识别原始观测行列并重新计算。
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
        if command in ('set_wb_density_font_size','set_wb_lane_centers') and mode!='wb':
            raise ValueError('泳道定位和相对灰度字号命令仅用于 WB／gel。')
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
        elif rule=='names':
            valid = (isinstance(value,list) and 1<=len(value)<=60 and all(
                isinstance(name,str) and 0<len(name.strip())<=160 for name in value)
                and len(set(value))==len(value))
        elif rule=='group gaps':
            valid = (isinstance(value,list) and all(isinstance(gap,list) and len(gap)==2
                     and type(gap[0]) is int and 1<=gap[0]<=10000
                     and type(gap[1]) is int and 1<=gap[1]<=10 for gap in value)
                     and len({gap[0] for gap in value})==len(value)
                     and sum(gap[1] for gap in value)<=30)
        elif rule=='lane centers':
            valid = (isinstance(value,list) and all(type(x) in (int,float) and 0<x<1 for x in value)
                     and all(left<right for left,right in zip(value,value[1:])))
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
