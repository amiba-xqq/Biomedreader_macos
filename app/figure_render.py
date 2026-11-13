"""Render the same editable figure objects to PDF and SVG."""
import base64
import io
import math
import os
import re
import statistics
import threading
from collections import defaultdict
from html import escape
from pathlib import Path

from PIL import Image
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from figure_canvas import LayoutScene

# Nature Publishing Group-inspired palette from ggsci pal_npg("nrc").
COLORS = ['#E64B35','#4DBBD5','#00A087','#3C5488','#F39B7F',
          '#8491B4','#91D1C2','#DC0000','#7E6148','#B09C85']
MATPLOTLIB_LOCK = threading.Lock()


def source_image(data):
    with Image.open(io.BytesIO(data)) as original:
        if original.n_frames != 1:
            raise ValueError('请将多页图像拆分成每通道或每胶一张图再导入。')
        if original.mode not in ('RGB', 'L', 'RGBA', 'LA', 'P', '1'):
            raise ValueError('请提供已用统一显示范围导出的 8 位灰度或 RGB 图片。原始高位深文件不在这里自动调整。')
        return original.copy()


def rotate(image, angle):
    return image.rotate(angle, resample=Image.Resampling.NEAREST, expand=True) if angle else image


def crop_image(data, rect, angle=0):
    image = rotate(source_image(data), angle)
    x, y, w, h = rect
    if min(x, y) < 0 or min(w, h) < 1 or x+w > image.width or y+h > image.height:
        raise ValueError('选区超出了图片。请重新选择。')
    return image.crop((x, y, x+w, y+h))


def arial_paths():
    import sys
    if sys.platform=='darwin':
        for directory in (Path('/System/Library/Fonts/Supplemental'),Path('/Library/Fonts')):
            pair = directory/'Arial.ttf',directory/'Arial Bold.ttf'
            if all(path.is_file() for path in pair):
                return pair
        from matplotlib import get_data_path
        fonts = Path(get_data_path())/'fonts/ttf'
        return fonts/'DejaVuSans.ttf',fonts/'DejaVuSans-Bold.ttf'
    fonts = Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'
    return fonts/'arial.ttf',fonts/'arialbd.ttf'


def arial():
    if 'BRArial' not in pdfmetrics.getRegisteredFontNames():
        regular,bold = arial_paths()
        pdfmetrics.registerFont(TTFont('BRArial',str(regular)))
        pdfmetrics.registerFont(TTFont('BRArialBold',str(bold)))


class Figure:
    def __init__(self, width, height, style):
        arial()
        self.width, self.height = width, height
        self.size = style.get('font_size', 7)
        self.stroke = style.get('stroke', 0.7)
        self.buffer = io.BytesIO()
        self.pdf = canvas.Canvas(self.buffer, pagesize=(width, height), pageCompression=1, initialFontName='BRArial')
        self.svg = [f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{width}pt" height="{height}pt" viewBox="0 0 {width} {height}">']
        self.rect(0, 0, width, height, '#FFFFFF')

    def line(self, x, y, xx, yy, color='#171717', width=None):
        stroke = self.stroke if width is None else width
        self.pdf.setStrokeColor(HexColor(color))
        self.pdf.setLineWidth(stroke)
        self.pdf.line(x, self.height-y, xx, self.height-yy)
        self.svg.append(f'<line x1="{x}" y1="{y}" x2="{xx}" y2="{yy}" stroke="{color}" stroke-width="{stroke}"/>')

    def rect(self, x, y, w, h, fill=None, border=None):
        if fill:
            self.pdf.setFillColor(HexColor(fill))
        if border:
            self.pdf.setStrokeColor(HexColor(border))
            self.pdf.setLineWidth(self.stroke)
        self.pdf.rect(x, self.height-y-h, w, h, stroke=bool(border), fill=bool(fill))
        self.svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill or "none"}" stroke="{border or "none"}" stroke-width="{self.stroke}"/>')

    def bar_outline(self,x,y,w,h,color,stroke,positive,open_base):
        if h<=0:
            return
        points = outline_points(x,y,w,h,positive,open_base)
        self.pdf.saveState()
        self.pdf.setStrokeColor(HexColor(color))
        self.pdf.setLineWidth(stroke)
        path = self.pdf.beginPath()
        path.moveTo(points[0][0],self.height-points[0][1])
        for px,py in points[1:]:
            path.lineTo(px,self.height-py)
        self.pdf.drawPath(path,stroke=1,fill=0)
        self.pdf.restoreState()
        coords = ' '.join(f'{px},{py}' for px,py in points)
        self.svg.append(f'<polyline points="{coords}" stroke="{color}" stroke-width="{stroke}" fill="none"/>')

    def gradient(self,x,y,w,h,colors,positions=None):
        bottom = self.height-y-h
        if positions is None:
            positions = [i/(len(colors)-1) for i in range(len(colors))]
        self.pdf.saveState()
        bounds = self.pdf.beginPath()
        bounds.rect(x,bottom,w,h)
        self.pdf.clipPath(bounds,stroke=0,fill=0)
        self.pdf.linearGradient(x,bottom,x+w,bottom,list(map(HexColor,colors)),positions=positions,extend=False)
        self.pdf.restoreState()
        name = f'gradient-{len(self.svg)}'
        stops = ''.join(f'<stop offset="{p}" stop-color="{color}"/>' for p,color in zip(positions,colors))
        self.svg.append(f'<defs><linearGradient id="{name}">{stops}</linearGradient></defs>')
        self.svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="url(#{name})"/>')

    def text(self, x, y, value, anchor='start', bold=False, angle=0, color='#171717', size=None):
        value = str(value)
        size = self.size if size is None else size
        font = 'BRArialBold' if bold else 'BRArial'
        self.pdf.saveState()
        self.pdf.translate(x, self.height-y)
        self.pdf.rotate(-angle)
        self.pdf.setFont(font, size)
        self.pdf.setFillColor(HexColor(color))
        method = {'start':self.pdf.drawString, 'middle':self.pdf.drawCentredString, 'end':self.pdf.drawRightString}
        method[anchor](0, 0, value)
        self.pdf.restoreState()
        self.svg.append(f'<text x="{x}" y="{y}" transform="rotate({angle} {x} {y})" text-anchor="{anchor}" font-family="Arial" font-size="{size}" font-weight="{700 if bold else 400}" fill="{color}">{escape(value)}</text>')

    def dot(self, x, y, color, r=1.5, hollow=False, alpha=1):
        if hollow:
            self.pdf.saveState()
            self.pdf.setStrokeColor(HexColor(color))
            self.pdf.setStrokeAlpha(alpha)
            self.pdf.setLineWidth(.5)
            self.pdf.circle(x,self.height-y,r,stroke=1,fill=0)
            self.pdf.restoreState()
            self.svg.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="none" stroke="{color}" stroke-width=".5" opacity="{alpha}"/>')
            return
        self.pdf.setFillColor(HexColor(color))
        self.pdf.circle(x, self.height-y, r, stroke=0, fill=1)
        self.svg.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{color}"/>')

    def polygon(self,points,fill,border=None):
        p = self.pdf.beginPath()
        p.moveTo(points[0][0],self.height-points[0][1])
        for x,y in points[1:]:
            p.lineTo(x,self.height-y)
        p.close()
        self.pdf.setFillColor(HexColor(fill))
        if border:
            self.pdf.setStrokeColor(HexColor(border))
            self.pdf.setLineWidth(self.stroke)
        self.pdf.drawPath(p,fill=1,stroke=bool(border))
        coords = ' '.join(f'{x},{y}' for x,y in points)
        self.svg.append(f'<polygon points="{coords}" fill="{fill}" stroke="{border or "none"}" stroke-width="{self.stroke}"/>')

    def image(self, x, y, w, h, image):
        buffer = io.BytesIO()
        image.save(buffer, 'PNG')
        raw = buffer.getvalue()
        self.pdf.drawImage(ImageReader(io.BytesIO(raw)), x, self.height-y-h, w, h, mask='auto')
        encoded = base64.b64encode(raw).decode('ascii')
        self.svg.append(f'<image x="{x}" y="{y}" width="{w}" height="{h}" preserveAspectRatio="none" xlink:href="data:image/png;base64,{encoded}"/>')

    def export(self):
        self.pdf.showPage()
        self.pdf.save()
        return {'pdf':self.buffer.getvalue(), 'svg':(''.join(self.svg)+'</svg>').encode('utf-8')}


class MatplotlibFigure:
    """The same verified chart geometry rendered using embedded Matplotlib."""
    def __init__(self,width,height,style):
        from matplotlib.figure import Figure as MplFigure
        from matplotlib.font_manager import FontProperties
        from matplotlib.patches import Circle,Polygon,Rectangle
        self.width,self.height = width,height
        self.size,self.stroke = style.get('font_size',7),style.get('stroke',0.7)
        regular,bold = arial_paths()
        pdfmetrics.registerFont(TTFont('BRArial',str(regular)))
        pdfmetrics.registerFont(TTFont('BRArialBold',str(bold)))


class Figure:
    def __init__(self, width, height, style):
        arial()
        self.width, self.height = width, height
        self.size = style.get('font_size', 7)
        self.stroke = style.get('stroke', 0.7)
        self.buffer = io.BytesIO()
        self.pdf = canvas.Canvas(self.buffer, pagesize=(width, height), pageCompression=1, initialFontName='BRArial')
        self.svg = [f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{width}pt" height="{height}pt" viewBox="0 0 {width} {height}">']
        self.rect(0, 0, width, height, '#FFFFFF')

    def line(self, x, y, xx, yy, color='#171717', width=None):
        stroke = self.stroke if width is None else width
        self.pdf.setStrokeColor(HexColor(color))
        self.pdf.setLineWidth(stroke)
        self.pdf.line(x, self.height-y, xx, self.height-yy)
        self.svg.append(f'<line x1="{x}" y1="{y}" x2="{xx}" y2="{yy}" stroke="{color}" stroke-width="{stroke}"/>')

    def rect(self, x, y, w, h, fill=None, border=None):
        if fill:
            self.pdf.setFillColor(HexColor(fill))
        if border:
            self.pdf.setStrokeColor(HexColor(border))
            self.pdf.setLineWidth(self.stroke)
        self.pdf.rect(x, self.height-y-h, w, h, stroke=bool(border), fill=bool(fill))
        self.svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill or "none"}" stroke="{border or "none"}" stroke-width="{self.stroke}"/>')

    def text(self, x, y, value, anchor='start', bold=False, angle=0, color='#171717', size=None):
        value = str(value)
        size = self.size if size is None else size
        font = 'BRArialBold' if bold else 'BRArial'
        self.pdf.saveState()
        self.pdf.translate(x, self.height-y)
        self.pdf.rotate(-angle)
        self.pdf.setFont(font, size)
        self.pdf.setFillColor(HexColor(color))
        method = {'start':self.pdf.drawString, 'middle':self.pdf.drawCentredString, 'end':self.pdf.drawRightString}
        method[anchor](0, 0, value)
        self.pdf.restoreState()
        self.svg.append(f'<text x="{x}" y="{y}" transform="rotate({angle} {x} {y})" text-anchor="{anchor}" font-family="Arial" font-size="{size}" font-weight="{700 if bold else 400}" fill="{color}">{escape(value)}</text>')

    def dot(self, x, y, color, r=1.5, hollow=False, alpha=1):
        if hollow:
            self.pdf.saveState()
            self.pdf.setStrokeColor(HexColor(color))
            self.pdf.setStrokeAlpha(alpha)
            self.pdf.setLineWidth(.5)
            self.pdf.circle(x,self.height-y,r,stroke=1,fill=0)
            self.pdf.restoreState()
            self.svg.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="none" stroke="{color}" stroke-width=".5" opacity="{alpha}"/>')
            return
        self.pdf.setFillColor(HexColor(color))
        self.pdf.circle(x, self.height-y, r, stroke=0, fill=1)
        self.svg.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{color}"/>')

    def polygon(self,points,fill,border=None):
        p = self.pdf.beginPath()
        p.moveTo(points[0][0],self.height-points[0][1])
        for x,y in points[1:]:
            p.lineTo(x,self.height-y)
        p.close()
        self.pdf.setFillColor(HexColor(fill))
        if border:
            self.pdf.setStrokeColor(HexColor(border))
            self.pdf.setLineWidth(self.stroke)
        self.pdf.drawPath(p,fill=1,stroke=bool(border))
        coords = ' '.join(f'{x},{y}' for x,y in points)
        self.svg.append(f'<polygon points="{coords}" fill="{fill}" stroke="{border or "none"}" stroke-width="{self.stroke}"/>')

    def image(self, x, y, w, h, image):
        buffer = io.BytesIO()
        image.save(buffer, 'PNG')
        raw = buffer.getvalue()
        self.pdf.drawImage(ImageReader(io.BytesIO(raw)), x, self.height-y-h, w, h, mask='auto')
        encoded = base64.b64encode(raw).decode('ascii')
        self.svg.append(f'<image x="{x}" y="{y}" width="{w}" height="{h}" preserveAspectRatio="none" xlink:href="data:image/png;base64,{encoded}"/>')

    def export(self):
        self.pdf.showPage()
        self.pdf.save()
        return {'pdf':self.buffer.getvalue(), 'svg':(''.join(self.svg)+'</svg>').encode('utf-8')}


class MatplotlibFigure:
    """The same verified chart geometry rendered using embedded Matplotlib."""
    def __init__(self,width,height,style):
        from matplotlib.figure import Figure as MplFigure
        from matplotlib.font_manager import FontProperties
        from matplotlib.patches import Circle,Polygon,Rectangle
        self.width,self.height = width,height
        self.size,self.stroke = style.get('font_size',7),style.get('stroke',0.7)
        regular,bold = arial_paths()
        self.font = FontProperties(fname=str(regular),family='Arial',size=self.size)
        self.bold = FontProperties(fname=str(bold),family='Arial',weight='bold',size=self.size)
        self.rectangle,self.circle = Rectangle,Circle
        self.poly = Polygon
        self.fig = MplFigure(figsize=(width/72,height/72),facecolor='#FFFFFF')
        self.axes = self.fig.add_axes((0,0,1,1))
        self.axes.set(xlim=(0,width),ylim=(height,0))
        self.axes.set_autoscale_on(False)
        self.axes.set_axis_off()

    def line(self,x,y,xx,yy,color='#171717',width=None):
        self.axes.plot((x,xx),(y,yy),color=color,linewidth=self.stroke if width is None else width,solid_capstyle='butt',
                       zorder=.5 if color=='#D9DEE3' else 2)

    def rect(self,x,y,w,h,fill=None,border=None):
        self.axes.add_patch(self.rectangle((x,y),w,h,facecolor=fill or 'none',edgecolor=border or 'none',
                                           linewidth=self.stroke))

    def bar_outline(self,x,y,w,h,color,stroke,positive,open_base):
        if h>0:
            xs,ys = zip(*outline_points(x,y,w,h,positive,open_base))
            self.axes.plot(xs,ys,color=color,linewidth=stroke,solid_capstyle='butt',solid_joinstyle='miter',zorder=1.5)

    def text(self,x,y,value,anchor='start',bold=False,angle=0,color='#171717',size=None):
        font = self.bold if bold else self.font
        if size is not None:
            font = font.copy()
            font.set_size(size)
        self.axes.text(x,y,str(value),ha={'start':'left','middle':'center','end':'right'}[anchor],
                       va='baseline',rotation=-angle,rotation_mode='anchor',
                       fontproperties=font,color=color,parse_math=False)

    def image(self,x,y,w,h,image):
        pixels = image if image.mode in ('RGB','RGBA') else image.convert('RGBA')
        self.axes.imshow(pixels,extent=(x,x+w,y+h,y),aspect='auto',interpolation='none',zorder=.8)

    def dot(self,x,y,color,r=1.5,hollow=False,alpha=1):
        self.axes.add_patch(self.circle((x,y),r,facecolor='none' if hollow else color,
                                       edgecolor=color if hollow else 'none',linewidth=.5,alpha=alpha,
                                       zorder=3 if hollow else 1))

    def polygon(self,points,fill,border=None):
        self.axes.add_patch(self.poly(points,closed=True,facecolor=fill,edgecolor=border or 'none',linewidth=self.stroke))

    def export(self):
        import matplotlib
        from matplotlib.backends.backend_pdf import FigureCanvasPdf
        from matplotlib.backends.backend_svg import FigureCanvasSVG
        pdf,svg = io.BytesIO(),io.BytesIO()
        with MATPLOTLIB_LOCK,matplotlib.rc_context({'svg.fonttype':'none','pdf.fonttype':42,
                                                   'pdf.use14corefonts':False,'image.composite_image':False}):
            FigureCanvasPdf(self.fig).print_pdf(pdf)
            FigureCanvasSVG(self.fig).print_svg(svg)
        return {'pdf':pdf.getvalue(),'svg':svg.getvalue()}


def outline_points(x,y,w,h,positive,open_base):
    base,top = (y+h,y) if positive else (y,y+h)
    points = [(x,base),(x,top),(x+w,top),(x+w,base)]
    return points if open_base else [*points,points[0]]


def scene_renderer(style,continuous_gradient=False):
    needs_gradient = continuous_gradient or any(op=='add_gradient' for op,_ in style.get('layout_commands',[]))
    return MatplotlibFigure if style.get('backend')=='matplotlib' and not needs_gradient else Figure


def width(value, size=7):
    return pdfmetrics.stringWidth(str(value), 'BRArial', size)


def top_axis(labels,first,step,available,font_size):
    """Reserve headroom and right margin for labels anchored above their columns."""
    lengths = [width(label,font_size) for label in labels]
    tilted = bool(lengths and max(lengths)>available)
    offsets = [0]*len(labels)
    if not tilted:
        return False,0,0,offsets
    rise = max(lengths)/math.sqrt(2)
    lanes = min(len(labels),max(1,math.ceil((font_size+2)*math.sqrt(2)/step)))
    offsets = [(i%lanes)*math.ceil(rise+font_size+3) for i in range(len(labels))]
    right = max(first+i*step+length/math.sqrt(2)+font_size for i,length in enumerate(lengths))
    return tilted,math.ceil(rise+max(offsets)+3),math.ceil(right),offsets


def scale_for(pixels, target, resolution):
    return min(target, 72*pixels/resolution)


def wb_figure(panels, files, style):
    return wb_scene(panels,files,style).export()


def wb_scene(panels, files, style, density=None):
    arial()
    gap = 1
    images = [crop_image(files[p['file']], p['rect'], p.get('angle', 0)) for p in panels]
    sizes = [im.size for im in images]
    shared_width = scale_for(min(w for w,h in sizes), 160, 300)
    labels = [p['label'] for p in panels]
    left = max(26, max(width(v, 8) for v in labels)+10)
    right = max(8, max(width(p.get('mw', ''), 8) for p in panels)+13)
    lane_labels = panels[0].get('lanes', [])
    if density and density.get('enabled'):
        from wb_density import panel_density
        if len(lane_labels)<2 or any(p.get('lanes') and p['lanes']!=lane_labels for p in panels):
            raise ValueError('各条带须使用同一套按泳道排列的标签。请在「相对灰度」中重新配置。')
        measurements = [panel_density(panel,image,lane_labels,density) for panel,image in zip(panels,images)]
        below = density['display']=='below'
    else:
        measurements,below = [],False
    cell = shared_width/max(1,len(lane_labels))
    tilted,rise,label_right,offsets = top_axis(lane_labels,left+cell/2,cell,cell-2,style.get('font_size',7))
    title = style.get('title','')
    y = (13+rise if lane_labels else 8)+(13 if title else 0)
    header = y
    # Same displayed horizontal span. The original pixels and the aspect ratio of each strip are retained.
    heights = [shared_width*h/w for w,h in sizes]
    row_heights = list(heights)
    offsets_by_row = []
    side_width = 0
    side_height = 0
    side_row_heights = []
    label_extent = max((width(label,6) for label in lane_labels),default=0)
    label_spacing = max(14.,min(28.,label_extent+5))
    if measurements:
        if below:
            for i,((ratios,rects,_),(pixels_w,_)) in enumerate(zip(measurements,sizes)):
                marks,lanes = wb_value_marks(ratios,rects,shared_width/pixels_w)
                offsets_by_row.append(marks)
                row_heights[i] += 4+8*lanes
        else:
            side_width = max(100+label_extent/math.sqrt(2)+label_spacing*len(lane_labels),
                             max(width(label+' · Relative intensity',6) for label in labels)
                             +max(17,label_extent/math.sqrt(2)+2)+10)
            side_row_heights = [max(h,53+label_extent/math.sqrt(2)) for h in heights]
            side_height = sum(side_row_heights)+gap*(len(panels)-1)
    renderer = scene_renderer(style)
    canvas_width = max(left+shared_width+right+side_width,label_right+4,width(title,8)+16)
    figure = LayoutScene(canvas_width,header+max(side_height,sum(row_heights)+gap*(len(panels)-1))+8,style,renderer)
    if title:
        figure.text(figure.width/2,10,title,'middle',True)
    for i, name in enumerate(lane_labels):
        x = left+(i+0.5)*shared_width/len(lane_labels)
        figure.text(x,header-4-offsets[i],name,'start' if tilted else 'middle',angle=-45 if tilted else 0)
    side_y = header
    for i,(panel, im, h) in enumerate(zip(panels, images, heights),1):
        middle = y+h/2
        name = 'panel:'+str(panel.get('uid',i))
        figure.group(name,1,middle)
        figure.members = (name,)
        figure.text(left-4, middle+2, panel['label'], 'end')
        figure.image(left, y, shared_width, h, im,source_id='crop:'+name)
        figure.rect(left, y, shared_width, h, border='#171717')
        if panel.get('mw'):
            figure.line(left+shared_width, middle, left+shared_width+5, middle)
            figure.text(left+shared_width+7, middle+2, panel['mw'])
        if measurements and below:
            for x,depth,value in offsets_by_row[i-1]:
                figure.add('text',(left+x,y+h+8+depth*8,value,'middle',False,0,'#171717',6))
        elif measurements:
            figure.members = ()
            wb_density_side(figure,left+shared_width+right+5,side_y,im,lane_labels,
                            measurements[i-1],label_spacing,label_extent,panel['label'])
            side_y += side_row_heights[i-1]+gap
        y += row_heights[i-1]+gap
    return figure


def wb_value_marks(ratios,rectangles,scale):
    from wb_density import roi_bounds
    marks,last = [],[]
    for ratio,roi in zip(ratios,rectangles):
        x,_,w,_ = roi_bounds(roi)
        center = (x+w/2)*scale
        label = f'{ratio:.2f}'
        half = width(label,6)/2
        for level,right in enumerate(last):
            if center-half>=right+2:
                last[level] = center+half
                break
        else:
            level = len(last)
            last.append(center+half)
        marks.append((center,level,label))
    return marks,len(last)


def wb_density_side(f,x,y,image,labels,measurements,slot,extent,target):
    from wb_density import roi_bounds
    ratios,rectangles,backgrounds = measurements
    group_x = x+max(17,extent/math.sqrt(2)+2)
    span = max(1,max(ratios))
    base = y+40
    top = y+10
    right = group_x+slot*len(labels)
    f.add('text',(group_x,y+6,target+' · Relative intensity','start',False,0,'#171717',6))
    f.line(group_x,top,group_x,base)
    f.line(group_x,base,right,base)
    for i,(label,ratio) in enumerate(zip(labels,ratios)):
        center = group_x+(i+.5)*slot
        height = (base-top-8)*ratio/span
        f.rect(center-slot*.3,base-height,slot*.6,height,COLORS[i%len(COLORS)])
        f.add('text',(center,base-height-2,f'{ratio:.2f}','middle',False,0,'#171717',6))
        tilted = width(label,6)>slot-3
        f.add('text',(center,base+(5 if tilted else 8),label,'end' if tilted else 'middle',
                      False,-45 if tilted else 0,'#171717',6))
    schematic_x = right+14
    scale = min(64/image.width,26/image.height)
    schematic_y = y+11
    f.add('text',(schematic_x,y+6,'Measurement regions','start',False,0,'#171717',6))
    f.image(schematic_x,schematic_y,image.width*scale,image.height*scale,image)
    for color,boxes in (('#29A7C7',[box for regions in backgrounds for box in regions]),('#F79724',rectangles)):
        for roi in boxes:
            if isinstance(roi,dict) and roi.get('outline'):
                for a,b,c,d in roi['outline']:
                    f.line(schematic_x+a*scale,schematic_y+b*scale,schematic_x+c*scale,schematic_y+d*scale,color)
            elif isinstance(roi,dict) and roi.get('points'):
                points = [*roi['points'],roi['points'][0]]
                for (a,b),(c,d) in zip(points,points[1:]):
                    f.line(schematic_x+a*scale,schematic_y+b*scale,schematic_x+c*scale,schematic_y+d*scale,color)
            else:
                bx,by,bw,bh = roi_bounds(roi)
                f.rect(schematic_x+bx*scale,schematic_y+by*scale,bw*scale,bh*scale,border=color)
    f.add('text',(schematic_x,base+8,'Band','start',False,0,'#BD610F',6))
    f.add('text',(schematic_x+25,base+8,'Background','start',False,0,'#12758B',6))


def if_figure(panels, files, style):
    return if_scene(panels,files,style).export()


def if_scale_pixels(panel):
    if 'scale_target' not in panel:
        return panel.get('scale_px',0)
    if panel['scale_target'] is None:
        return 0
    calibration = panel.get('scale_calibration',0)
    pixels_per_um = panel.get('scale_pixels_per_um',1/calibration if calibration else 0)
    return panel.get('scale_length',0)*pixels_per_um


def if_scene(panels, files, style):
    arial()
    pixels_w,pixels_h = panels[0]['rect'][2:]
    gap = 1.5
    channels = list(dict.fromkeys(name for panel in panels for name in panel['channels']))
    point_scale = min(72/300,80/max(pixels_w,pixels_h))
    tile_w,tile_h = pixels_w*point_scale,pixels_h*point_scale
    left = max(28, max(width(s,8) for s in channels)+10)
    column = tile_w
    headings = [panel['label'] for panel in panels]
    tilted,rise,label_right,offsets = top_axis(headings,left+column/2,column+gap,column-2,style.get('font_size',7))
    title = style.get('title','')
    top = 17+rise+(13 if title else 0)
    renderer = scene_renderer(style)
    canvas_width = max(left+(column+gap)*len(panels)-gap+8,label_right+4,width(title,8)+16)
    figure = LayoutScene(canvas_width,top+(tile_h+gap)*len(channels)-gap+8,style,renderer)
    if title:
        figure.text(figure.width/2,10,title,'middle',True)
    for row, channel in enumerate(channels):
        name = 'channel:'+channel
        middle = top+row*(tile_h+gap)+tile_h/2
        figure.group(name,1,middle)
        figure.members = (name,)
        figure.text(left-4,middle+2,channel,'end')
    for i, panel in enumerate(panels):
        if tuple(panel['rect'][2:])!=(pixels_w,pixels_h):
            raise ValueError('IF 每组必须使用同样的选区宽高。')
        x = left+i*(column+gap)
        group = 'group:'+str(panel.get('uid',i+1))
        figure.group(group,0,x+column/2)
        figure.members = (group,)
        figure.text(x+column/2,top-4-offsets[i],headings[i],'start' if tilted else 'middle',angle=-45 if tilted else 0)
        for row, name in enumerate(channels):
            if name not in panel['channels']:
                continue
            file_id = panel['channels'][name]
            figure.members = (group,'channel:'+name)
            im = crop_image(files[file_id],panel['rect'],panel.get('angle',0))
            ix, iy = x, top+row*(tile_h+gap)
            target = panel.get('scale_target')
            legacy_target = 'scale_target' not in panel and file_id==list(panel['channels'].values())[-1]
            pixels = if_scale_pixels(panel) if target in ('all',file_id) or legacy_target else 0
            scale = None
            if pixels:
                if pixels>=pixels_w:
                    raise ValueError('比例尺长于选区。请检查标定值或增大选区。')
                length = pixels*point_scale
                inset = min(1.7,(tile_w-length)/2)
                end = ix+tile_w-inset
                thickness = min(1.2,tile_h/6)
                bar_x = end-length
                bar_y = iy+tile_h-thickness-min(1.2,tile_h/12)
                scale = ((bar_x-ix)/tile_w,(bar_y-iy)/tile_h,length/tile_w,thickness/tile_h)
            figure.image(ix,iy,tile_w,tile_h,im,source_id='crop:'+group+':'+name,scale_bar=scale)
            if scale:
                figure.rect(bar_x,bar_y,length,thickness,'#FFFFFF')
    return figure


SIGNIFICANCE = {'n.s.':'p ≥ 0.05','*':'p < 0.05','**':'p < 0.01','***':'p < 0.001','****':'p < 0.0001'}


def significance_symbol(p):
    return '*'*sum(p<cutoff for cutoff in (.05,.01,.001,.0001)) or 'n.s.'


def bracket_layout(tests,positions,font_size):
    spans = []
    for row in tests:
        target,a,b = (row[key] for key in ('target','group_a','group_b'))
        p = comparison_p(row)
        if p is None or (target,a) not in positions or (target,b) not in positions:
            continue
        x,xx = sorted((positions[target,a],positions[target,b]))
        if xx-x<1:
            continue
        symbol = significance_symbol(p)
        half_label = width(symbol,font_size)/2
        middle = (x+xx)/2
        spans.append((x,xx,symbol,min(x,middle-half_label)-2,max(xx,middle+half_label)+2))
    rows = []
    brackets = []
    for x,xx,symbol,start,end in sorted(spans,key=lambda span:(span[1]-span[0],span[0])):
        for level,intervals in enumerate(rows):
            if all(end<before or start>after for before,after in intervals):
                break
        else:
            level = len(rows)
            rows.append([])
        rows[level].append((start,end))
        brackets.append((x,xx,symbol,level))
    return brackets,len(rows)


def comparison_p(row):
    return row.get('p_adjusted') if row.get('comparison')=='posthoc' else row.get('p_unadjusted')


def draw_bracket(figure,x,xx,y,symbol):
    figure.line(x,y+4,x,y,'#000000',.5)
    figure.line(x,y,xx,y,'#000000',.5)
    figure.line(xx,y,xx,y+4,'#000000',.5)
    figure.text((x+xx)/2,y-2,symbol,'middle')


def significance_legend(symbols):
    return '   '.join(f'{symbol} {definition}' for symbol,definition in SIGNIFICANCE.items() if symbol in symbols)


def comparison_panel(scene,tests):
    """Show selected pairs below plots without separate group positions on X."""
    pairs = [(row,significance_symbol(p)) for row in tests if row.get('comparison')!='omnibus'
             and (p:=comparison_p(row)) is not None]
    if not pairs:
        return
    scene.height += 10
    symbols = set()
    for row,symbol in pairs:
        first,second = row['group_a'],row['group_b']
        target = row['target']
        x = max(30,width(first,8)/2+10)
        xx = x+max(48,(width(first,8)+width(second,8))/2+12)
        scene.width = max(scene.width,xx+width(second,8)/2+12,width(target,8)+20)
        scene.height += 13
        y = scene.height
        draw_bracket(scene,x,xx,y,symbol)
        scene.text(x,y+13,first,'middle')
        scene.text(xx,y+13,second,'middle')
        if target:
            scene.text(9,y+24,target)
        scene.height += 28 if target else 18
        symbols.add(symbol)
    legend = significance_legend(symbols)
    scene.width = max(scene.width,width(legend,8)+20)
    scene.text(9,scene.height+8,legend)
    scene.height += 17


def chart(values, style, options, tests, *, scene_only=False):
    arial()
    from figure_data import GROUPED_CHARTS
    kind = options['kind']
    grouped_kind = kind
    factors = any(row.get('treatment') for row in values)
    enrichment_bars = (options.get('geometry')=='qpcr' and options.get('analysis')=='enrichment'
                       and GROUPED_CHARTS.get(kind,('',kind))[1]=='bar' and kind!='grouped_separate')
    if enrichment_bars and (len({row['sample_group'] for row in values})<=1
                            or all(row['sample_group']==row['treatment'] for row in values)):
        factors = False
    series_labels = ({row['group']:row['treatment'] or row['sample_group'] or row['group'] for row in values}
                     if enrichment_bars and not factors else {})
    if kind in GROUPED_CHARTS:
        if not factors and not options.get('matrix_grouped') and not enrichment_bars:
            raise ValueError('请先为每个样本设置分组和处理条件。')
        kind = GROUPED_CHARTS[kind][1]
    transposed_matrix = options.get('matrix_grouped') and grouped_kind=='grouped_separate'
    if transposed_matrix:
        values = [{**row,'target':row['group'],'group':row['target']} for row in values]
    if kind in ('histogram','ecdf','xy','xyline','heatmap'):
        from figure_table_charts import numeric_chart,heatmap
        render = heatmap if kind=='heatmap' else numeric_chart
        scene = render(values,style,options,scene_only=True)
        if options.get('comparison_mode')=='manual':
            comparison_panel(scene,tests)
        return scene if scene_only else scene.export()
    cell_positions = {}
    block_targets = {}
    if factors:
        chart_rows = []
        target_order,category_order = {},{}
        for row in values:
            major,minor = row['sample_group'],row['treatment']
            if grouped_kind=='grouped_separate':
                major,minor = minor,major
            target = row['target']
            target_order.setdefault(target,len(target_order))
            category_order.setdefault(major,len(category_order))
            block = (target,major)
            block_targets[block] = f'{target} · {major}' if target else major
            cell_positions[target,row['group']] = (block,minor)
            chart_rows.append({**row,'target':block,'group':minor})
        values = sorted(chart_rows,key=lambda row:(target_order[row['target'][0]],category_order[row['target'][1]]))
    grouped = defaultdict(lambda: defaultdict(list))
    observations = defaultdict(list)
    order = []
    for row in values:
        grouped[row['target']][row['group']].append(row['value'])
        if kind=='scatter' and options.get('geometry')=='table' and 'file_index' in row:
            observations[row['target'],row['group']].append(row)
        if row['group'] not in order:
            order.append(row['group'])
    irradiation = {re.sub(r'\s+','',label.casefold()):name for name,label in series_labels.items()}
    if enrichment_bars and not factors and len(order)==2 and irradiation.keys()=={'noir','ir'}:
        order = [irradiation['noir'],irradiation['ir']]
    if options['control'] in order:
        order.remove(options['control'])
        order.insert(0, options['control'])
    time_points = {}
    if style.get('x_time_axis'):
        if kind!='line':
            raise ValueError('数值时间轴适用于折线图。')
        try:
            time_points = {key:float(key[1] if factors else key or next(iter(series)))
                           for key,series in grouped.items()}
        except (ValueError,TypeError):
            raise ValueError('时间轴需要分组名称为数值小时，请核对样本分组。') from None
        if not all(map(math.isfinite,time_points.values())):
            raise ValueError('时间轴需要有限数值。')
        grouped = dict(sorted(grouped.items(),key=lambda item:time_points[item[0]]))
    # A table with no series column displays group labels on the axis.
    if list(grouped) == ['']:
        grouped = {key:{key: vals} for key, vals in grouped[''].items()}
        observations = {(group,group):rows for (_,group),rows in observations.items()}
        if time_points:
            try:
                time_points = {key:float(key) for key in grouped}
            except ValueError:
                raise ValueError('时间轴需要各组名称为数值小时。') from None
            if not all(map(math.isfinite,time_points.values())):
                raise ValueError('时间轴需要有限数值。')
            grouped = dict(sorted(grouped.items(),key=lambda item:time_points[item[0]]))
        single = True
    else:
        single = False
    categories = style.get('x_category_order')
    if categories and not time_points:
        rank = {name:index for index,name in enumerate(categories)}
        present = {block_targets.get(key,key) for key in grouped}
        missing = set(rank)-present
        if missing:
            raise ValueError('横轴找不到以下类别：'+'、'.join(sorted(missing))+'。请核对图上的名称。')
        grouped = dict(sorted(grouped.items(),key=lambda entry:rank.get(block_targets.get(entry[0],entry[0]),len(rank))))
    reference_colors = ({irradiation['noir']:'#227E9B',irradiation['ir']:'#B73E24'}
                        if len(order)==2 and irradiation.keys()=={'noir','ir'} else {})
    colors = style.get('colors') or ([reference_colors[name] for name in order] if reference_colors else COLORS)
    error_mode = options.get('error', 'sem')
    if kind in ('box','violin','stacked','range'):
        error_mode = 'none'
    if kind=='stacked' and (single or any(row['value']<0 for row in values)):
        raise ValueError('堆叠柱图需要长表的类别列，且所有数值须非负。')
    stats = {}
    for target, groups in grouped.items():
        for group, points in groups.items():
            deviation = statistics.stdev(points) if len(points)>1 else 0
            error = deviation/math.sqrt(len(points)) if error_mode=='sem' else deviation if error_mode=='sd' else 0
            stats[target,group] = statistics.mean(points), error
    totals = [sum(stats[target,g][0] for g in groups) for target,groups in grouped.items()] if kind=='stacked' else []
    vmax = max([0,*[mean+err for mean,err in stats.values()],*[row['value'] for row in values],*totals])
    vmin = min(0, *(mean-err for mean,err in stats.values()), *(row['value'] for row in values))
    span = vmax-vmin or 1
    vmax += span*0.23
    if vmin < 0:
        vmin -= span*0.06
    left, plot_height = 45, style.get('plot_height',102)
    manual = options.get('comparison_mode')=='manual'
    annotate_pairs = manual or ((factors or options.get('geometry')=='qpcr' or options.get('matrix_grouped'))
                                and options.get('comparison_mode')=='all')
    shared_center = single or kind=='stacked' or (kind=='line' and (time_points or not annotate_pairs))
    count = 1 if shared_center else len(order)
    compact = not factors and not options.get('matrix_grouped') and (
        options.get('geometry')=='qpcr' and kind=='bar' and not enrichment_bars
        or single and kind in ('bar','scatter') and options.get('geometry')=='table')
    show_targets = not compact or (not single and len(grouped)>1)
    title = style.get('title',options['title'] or (next(iter(grouped)) if compact and not single and len(grouped)==1 else ''))
    target_labels = {key:(f'{time_points[key]:g}' if time_points else block_targets.get(key,key)) for key in grouped}
    target_width = max(width(label,8) for label in target_labels.values())
    ticks_depth = 12
    angled = False
    # Match the qPCR reference for all categorical plots. A treatment slot is
    # 1.63 bar widths; the outer margins are 0.32 and target separation 1.5.
    target_room = (1.63 if single else 1.63*count+1)*11
    target_tilt = target_width+6>target_room and show_targets
    target_rise = math.ceil(target_width/math.sqrt(2))+4 if target_tilt else 0
    bar = 11. if target_tilt or compact and (single or len(grouped)==1) else max(11.,(target_width+6)/(target_room/11))
    slot = (1.45 if enrichment_bars and not factors else 1.63)*bar
    group_width = bar+(count-1)*slot
    gap = style.get('gap',1.5*bar) if kind!='bar' else 1.5*bar
    step = slot if single else group_width+gap
    if enrichment_bars and not factors:
        step = max(step,target_width+5)
    chart_width = .64*bar+group_width+(len(grouped)-1)*step
    first_center = .32*bar+group_width/2
    if target_tilt:
        left = max(left,target_rise+5-first_center)
    if compact:
        label_width = max(width(group,8) for group in order)
        angled = label_width>slot-3
        if angled:
            ticks_depth = math.ceil(12+label_width/math.sqrt(2))
            left = max(left,math.ceil(label_width/math.sqrt(2)+9))
    centers = {key:left+first_center+step*i for i,key in enumerate(grouped)}
    if time_points:
        per_series = defaultdict(list)
        for key in grouped:
            per_series[key[0] if factors else ''].append(key)
        origin = left+first_center
        for keys in per_series.values():
            times = sorted({time_points[key] for key in keys})
            interval = min((b-a for a,b in zip(times,times[1:])),default=1)
            scale = step*(len(times)-1)/(times[-1]-times[0]) if len(times)>1 else step
            scale = max(scale,min(step,12.)/interval)
            for key in keys:
                centers[key] = origin+(time_points[key]-times[0])*scale
            origin = centers[keys[-1]]+step
        chart_width = max(centers.values())-left+first_center
    entries = [] if single or compact else [(series_labels.get(g,g),colors[i%len(colors)]) for i,g in enumerate(order)]
    legend_w = max((width(g,8)+15 for g,_ in entries), default=0)
    legend_columns = max(1,int(chart_width/max(1,legend_w)))
    top = 19+12*math.ceil(len(entries)/legend_columns)
    note = {'sem':'Mean ± SEM','sd':'Mean ± SD','none':''}[error_mode]
    pairwise = [row for row in tests if row.get('comparison')!='omnibus']
    positions = {}
    for i,(target,groups) in enumerate(grouped.items()):
        center = centers[target]
        for j,group in enumerate([target] if single else order):
            if group in groups:
                x = center if shared_center else center+(j-(count-1)/2)*slot
                positions[('' if single else target),group] = x
    test_positions = {key:positions[cell] for key,cell in cell_positions.items()} if factors else positions
    if transposed_matrix:
        test_positions = {(group,target):x for (target,group),x in positions.items()}
    brackets,levels = bracket_layout(pairwise,test_positions,style.get('font_size',7)) if annotate_pairs and kind!='stacked' else ([],0)
    mark_tests = []
    if not factors and not annotate_pairs and not single and kind in ('bar','line','scatter'):
        for row in pairwise:
            p = row.get('p_adjusted') if row['comparison']=='posthoc' else row['p_unadjusted']
            if p is not None and p<.05 and options['control'] in (row['group_a'],row['group_b']):
                mark_tests.append((row,p))
    significance = ''
    if mark_tests:
        correction = ('Tukey HSD' if options.get('test')=='anova' else 'Holm adjusted'
                      if options.get('test')=='kruskal' else 'unadjusted')
        significance = f'* p < 0.05 vs. control ({correction})'
    used = {symbol for _,_,symbol,_ in brackets}
    notes = [line for line in (note,significance,significance_legend(used)) if line]
    canvas_width = max(left+chart_width+11,width(title,8)+16,left+legend_w+8,
                       16+max((width(line,8) for line in notes),default=0))
    xlabel = style.get('xlabel',options.get('xlabel',''))
    target_offset = ticks_depth+11 if compact else 11
    xlabel_offset = ((target_offset if show_targets else ticks_depth)+13 if compact else 28)+target_rise
    color_strip = style.get('x_color_strip',False)
    continuous_strip = color_strip and style.get('x_strip_gradient',False)
    strip = ('#d8e8f5','#b5d5ee','#86bce5','#619ed1','#467fb8','#375f96','#263f71')
    strip_edges = list(centers.values()) if color_strip else []
    if color_strip:
        xlabel_offset += 14
    note_offset = (xlabel_offset if xlabel else xlabel_offset-9)+11
    renderer = scene_renderer(style,continuous_strip)
    bracket_space = (levels*13+2) if levels else 0
    if levels:
        vmax -= span*.185
    f = LayoutScene(canvas_width,top+bracket_space+plot_height+note_offset+4+10*len(notes),style,renderer)
    plot_top = top+bracket_space
    base = plot_top+plot_height
    ycoord = lambda n: base-(n-vmin)/(vmax-vmin)*plot_height
    f.text(left+chart_width/2, 10, title, 'middle', True)
    for i,(g,color) in enumerate(entries):
        x = left+(i%legend_columns)*legend_w
        y = 20+(i//legend_columns)*12
        f.rect(x,y-5,8,5,color)
        f.text(x+11,y,g)
    f.line(left,plot_top,left,base)
    f.line(left,ycoord(0),left+chart_width,ycoord(0))
    tick_step = (vmax-vmin)/5
    exponent = 10**math.floor(math.log10(tick_step))
    tick_step = next(m*exponent for m in (1,2,2.5,5,10) if m*exponent>=tick_step)
    for i in range(math.ceil(vmin/tick_step), math.floor(vmax/tick_step)+1):
        value = i*tick_step
        y = ycoord(value)
        if style.get('grid') and i:
            f.line(left,y,left+chart_width,y,'#D9DEE3')
        f.line(left-3,y,left,y)
        f.text(left-5,y+2,f'{value:.3g}','end')
    y_label_x = left-9-max((width(f'{i*tick_step:.3g}',8) for i in
                          range(math.ceil(vmin/tick_step),math.floor(vmax/tick_step)+1)),default=0)
    ylabel = style.get('ylabel',options['ylabel'])
    if options.get('matrix_percent') and not ylabel.endswith(('%','%)','％','％）')):
        ylabel = ylabel.rstrip()+' (%)' if ylabel.strip() else '%'
    f.text(max(8,y_label_x),(plot_top+base)/2,ylabel,'middle',angle=-90)
    if xlabel:
        f.text(left+chart_width/2,base+xlabel_offset,xlabel,'middle')
    for i,note_line in enumerate(notes):
        f.text(8,base+note_offset+10*i,note_line)
    strip_y = ticks_depth+3 if compact and len(grouped)==1 else target_offset+target_rise+3
    previous = {}
    previous_target = None
    for i,(target,groups) in enumerate(grouped.items()):
        if factors and target[0]!=previous_target:
            previous.clear()
            previous_target = target[0]
        center = centers[target]
        stack = 0
        if show_targets:
            if enrichment_bars and not factors:
                f.line(center,base,center,base+3)
            f.text(center,base+target_offset,target_labels[target],'end' if target_tilt else 'middle',angle=-45 if target_tilt else 0)
        if color_strip:
            color = strip[round(i*(len(strip)-1)/max(1,len(grouped)-1))]
            before = (strip_edges[i-1]+center)/2 if i else left
            after = (center+strip_edges[i+1])/2 if i+1<len(strip_edges) else left+chart_width
            f.strip_segment(before+.4,base+strip_y,max(.1,after-before-.8),4,color)
        for j, group in enumerate([target] if single else order):
            if group not in groups:
                previous.pop(group,None)
                continue
            color = colors[order.index(group)%len(colors)]
            x = positions[('' if single else target),group]
            if compact:
                f.line(x,base,x,base+3)
                f.text(x,base+(8 if angled else 12),group,'end' if angled else 'middle',angle=-45 if angled else 0)
            mean,err = stats[target,group]
            y = ycoord(mean)
            if kind=='bar':
                f.bar(x-bar/2,min(y,ycoord(0)),bar,abs(y-ycoord(0)),color,mean>=0)
            elif kind=='range':
                low,high = min(groups[group]),max(groups[group])
                if low==high:
                    f.line(x-bar/2,y,x+bar/2,y,color)
                else:
                    f.rect(x-bar/2,ycoord(high),bar,ycoord(low)-ycoord(high),color)
            elif kind=='stacked':
                bottom = ycoord(stack)
                stack += mean
                f.rect(center-bar/2,ycoord(stack),bar,bottom-ycoord(stack),color)
            elif kind=='box':
                box_plot(f,x,bar/2,groups[group],color,ycoord)
            elif kind=='violin':
                violin_plot(f,x,bar/2,groups[group],color,ycoord)
            elif kind=='line':
                series = '' if single else group
                color = colors[0] if single else color
                if series in previous:
                    f.line(*previous[series],x,y,color)
                previous[series] = (x,y)
                f.dot(x,y,color)
            else:
                sources = observations.get((target,group))
                for k,v in enumerate(groups[group]):
                    hollow = options.get('geometry')=='qpcr' and options.get('show_replicates',False)
                    jitter = 0 if grouped_kind=='grouped_aligned' else ((k%7)-3)*0.8
                    point = (x+jitter,ycoord(v),'#000000' if hollow else color,1.25,hollow,.7 if hollow else 1)
                    if sources:
                        f.observation(sources[k],*point)
                    else:
                        f.dot(*point)
            if err:
                high,low = ycoord(mean+err),ycoord(mean-err)
                f.line(x,high,x,low,color)
                for point in (high,low):
                    f.line(x-2,point,x+2,point,color)
            if (grouped_kind=='grouped_points' or options.get('show_replicates') and kind in ('bar','line')
                    and len(groups[group])>1):
                count_points = len(groups[group])
                spread = min(bar*.55,1.8*(count_points-1))
                for point,value in enumerate(groups[group]):
                    offset = spread*(point/max(1,count_points-1)-.5)
                    f.dot(x+offset,ycoord(value),'#000000',1.3,True,.7)
            if kind=='scatter' and options.get('geometry')=='table':
                f.line(x-bar*.39,y,x+bar*.39,y,'#000000',1)
    for x,xx,symbol,level in brackets:
        draw_bracket(f,x,xx,plot_top-4-level*13,symbol)
    for row,p in mark_tests:
        target, first, second = (row[k] for k in ('target','group_a','group_b'))
        a,b = positions[target,first],positions[target,second]
        mean,err = stats[target,second if first==options['control'] else first]
        # Place each mark above the comparison group. The CSV holds exact pairwise p values.
        x = b if first==options['control'] else a
        y = ycoord(mean+err)-4
        if len(order)==2 and kind!='line':
            y = min(ycoord(sum(stats[target,g])) for g in (first,second))-5
            f.line(a,y+3,a,y)
            f.line(a,y,b,y)
            f.line(b,y,b,y+3)
            x = (a+b)/2
        f.text(x,y-2,'***' if p<.001 else '**' if p<.01 else '*','middle')
    if annotate_pairs and kind=='stacked':
        comparison_panel(f,tests)
    return f if scene_only else f.export()


def box_plot(f,x,half,points,color,ycoord):
    if len(points)==1:
        f.dot(x,ycoord(points[0]),color)
        return
    q1,median,q3 = statistics.quantiles(points,n=4,method='inclusive')
    iqr = q3-q1
    low = min(value for value in points if value>=q1-1.5*iqr)
    high = max(value for value in points if value<=q3+1.5*iqr)
    f.line(x,ycoord(high),x,ycoord(q3),color)
    f.line(x,ycoord(q1),x,ycoord(low),color)
    for value in (low,high):
        f.line(x-half/2,ycoord(value),x+half/2,ycoord(value),color)
    f.rect(x-half,ycoord(q3),half*2,ycoord(q1)-ycoord(q3),'#FFFFFF',color)
    f.line(x-half,ycoord(median),x+half,ycoord(median),color)
    for value in points:
        if value<low or value>high:
            f.dot(x,ycoord(value),color)


def violin_plot(f,x,half,points,color,ycoord):
    low,high = min(points),max(points)
    if low==high:
        f.dot(x,ycoord(low),color)
        return
    bandwidth = statistics.stdev(points)*len(points)**(-.2)
    levels = [low+(high-low)*i/40 for i in range(41)]
    density = [sum(math.exp(-.5*((level-point)/bandwidth)**2) for point in points) for level in levels]
    peak = max(density)
    spans = [half*height/peak for height in density]
    left = [(x-span,ycoord(level)) for level,span in zip(levels,spans)]
    right = [(x+span,ycoord(level)) for level,span in zip(levels,spans)]
    f.polygon(left+right[::-1],color)
    middle = ycoord(statistics.median(points))
    f.line(x-half*.35,middle,x+half*.35,middle,'#FFFFFF')
