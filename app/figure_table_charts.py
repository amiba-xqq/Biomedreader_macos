"""Vector plots for numeric axes and two-variable tables."""
import math
import statistics
from bisect import bisect_right
from collections import Counter,defaultdict

from figure_data import number
from figure_render import COLORS, scene_renderer, top_axis, width
from figure_canvas import LayoutScene


def linear_scale(start,end,intervals=5):
    if start==end:
        margin = max(.5,abs(start)*.05)
        start,end = start-margin,end+margin
    trial = (end-start)/intervals
    base = 10**math.floor(math.log10(trial))
    step = next(k*base for k in (1,2,2.5,5,10) if k*base>=trial)
    ticks = [i*step for i in range(math.ceil(start/step),math.floor(end/step)+1)]
    return start,end,ticks


def plot_frame(style,options,series,x_range,y_range,x_label,y_label):
    left,width_pt = 46,180
    plot_height = style.get('plot_height',112)
    legend_width = max(width(group,8)+15 for group in series)
    columns = max(1,int(width_pt/legend_width))
    top = 23+12*math.ceil(len(series)/columns)
    bottom = top+plot_height
    title = style.get('title',options['title'])
    figure_type = scene_renderer(style)
    f = LayoutScene(max(left+width_pt+13,left+legend_width+8,width(title,8)+16),bottom+38,style,figure_type)
    colors = style.get('colors') or COLORS
    palette = {group:colors[i%len(colors)] for i,group in enumerate(series)}
    f.text(left+width_pt/2,11,title,'middle',True)
    for i,(name,color) in enumerate(palette.items()):
        x = left+(i%columns)*legend_width
        y = 21+(i//columns)*12
        f.rect(x,y-5,8,5,color)
        f.text(x+11,y,name)
    xmin,xmax,x_ticks = linear_scale(*x_range)
    ymin,ymax,y_ticks = linear_scale(*y_range)
    def xcoord(value):
        return left+(value-xmin)/(xmax-xmin)*width_pt
    def ycoord(value):
        return bottom-(value-ymin)/(ymax-ymin)*plot_height
    f.line(left,top,left,bottom)
    f.line(left,bottom,left+width_pt,bottom)
    for value in x_ticks:
        x = xcoord(value)
        f.line(x,bottom,x,bottom+3)
        f.text(x,bottom+12,f'{value:.3g}','middle')
    for value in y_ticks:
        y = ycoord(value)
        if style.get('grid') and value!=ymin:
            f.line(left,y,left+width_pt,y,'#D9DEE3')
        f.line(left-3,y,left,y)
        f.text(left-5,y+2,f'{value:.3g}','end')
    f.text(left+width_pt/2,bottom+29,style.get('xlabel',x_label),'middle')
    label_x = max(8,left-9-max((width(f'{value:.3g}',8) for value in y_ticks),default=0))
    f.text(label_x,top+plot_height/2,style.get('ylabel',y_label),'middle',angle=-90)
    return f,palette,xcoord,ycoord


def numeric_chart(values,style,options,*,scene_only=False):
    kind = options['kind']
    if kind in ('xy','xyline'):
        return xy_chart(values,style,options,scene_only=scene_only)
    series = defaultdict(list)
    for row in values:
        group = (row['target']+' · ' if row['target'] else '')+row['group']
        series[group].append(row['value'])
    low,high = min(row['value'] for row in values),max(row['value'] for row in values)
    if kind=='ecdf':
        padding = max(.5,abs(low)*.05) if high==low else (high-low)*.035
        f,palette,x,y = plot_frame(style,options,series,(low-padding,high+padding),(0,1),
                                   options.get('xlabel') or options['ylabel'],'Cumulative fraction')
        for group,observations in series.items():
            cursor,fraction = low-padding,0
            for value,count in sorted(Counter(observations).items()):
                f.line(x(cursor),y(fraction),x(value),y(fraction),palette[group])
                next_fraction = fraction+count/len(observations)
                f.line(x(value),y(fraction),x(value),y(next_fraction),palette[group])
                cursor,fraction = value,next_fraction
            f.line(x(cursor),y(fraction),x(high+padding),y(fraction),palette[group])
        return f if scene_only else f.export()
    if low==high:
        padding = max(.5,abs(low)*.05)
        low,high = low-padding,high+padding
    count = min(40,max(1,math.ceil(math.sqrt(len(values)))))
    edges = [low+(high-low)*i/count for i in range(count+1)]
    bins = {}
    for group,observations in series.items():
        bins[group] = Counter(min(count-1,bisect_right(edges,value)-1) for value in observations)
    peak = max(n for counts in bins.values() for n in counts.values())
    f,palette,x,y = plot_frame(style,options,series,(low,high),(0,peak*1.12),
                               options.get('xlabel') or options['ylabel'],'Count')
    for group,counts in bins.items():
        previous = 0
        for i,(left,right) in enumerate(zip(edges,edges[1:])):
            frequency = counts[i]
            f.line(x(left),y(previous),x(left),y(frequency),palette[group])
            f.line(x(left),y(frequency),x(right),y(frequency),palette[group])
            previous = frequency
        f.line(x(high),y(previous),x(high),y(0),palette[group])
    return f if scene_only else f.export()


def xy_chart(values,style,options,*,scene_only=False):
    if not options.get('gene') or options.get('orientation')=='wide':
        raise ValueError('XY 图需要长表。请在「类别／数值 X 列」中指定数值 X 轴。')
    series = defaultdict(list)
    for row in values:
        x = number(row['target'],f"第 {row['row']} 行 X 值")
        series[row['group']].append((x,row['value']))
    coordinates = [point for points in series.values() for point in points]
    xs,ys = zip(*coordinates)
    def padded(numbers):
        low,high = min(numbers),max(numbers)
        padding = (high-low)*.05 if high>low else max(.5,abs(low)*.05)
        return low-padding,high+padding
    f,palette,x,y = plot_frame(style,options,series,padded(xs),padded(ys),
                               options.get('xlabel') or options['gene'],options['ylabel'])
    for group,points in series.items():
        color = palette[group]
        if options['kind']=='xyline':
            repeated = defaultdict(list)
            for horizontal,vertical in points:
                repeated[horizontal].append(vertical)
            points = [(horizontal,statistics.mean(replicates)) for horizontal,replicates in sorted(repeated.items())]
            for (a,b),(aa,bb) in zip(points,points[1:]):
                f.line(x(a),y(b),x(aa),y(bb),color)
        for horizontal,vertical in points:
            f.dot(x(horizontal),y(vertical),color)
    return f if scene_only else f.export()


def heatmap(values,style,options,*,scene_only=False):
    if not options.get('gene') or options.get('orientation')=='wide':
        raise ValueError('热图需要长表。请指定用于热图行的类别列。')
    cells = defaultdict(list)
    rows,columns = {},{}
    for row in values:
        rows.setdefault(row['target'],None)
        columns.setdefault(row['group'],None)
        cells[row['target'],row['group']].append(row['value'])
    means = {key:statistics.mean(points) for key,points in cells.items()}
    low,high = min(means.values()),max(means.values())
    stops = style.get('colors') or ['#FFF3EE',COLORS[0]]
    if len(stops)==1:
        stops = ['#FFFFFF',*stops]
    colors = [tuple(int(stop[i:i+2],16) for i in (1,3,5)) for stop in stops]
    def shade(fraction):
        position = fraction*(len(colors)-1)
        index = min(int(position),len(colors)-2)
        ratio = position-index
        return '#'+''.join(f'{round(a+(b-a)*ratio):02X}' for a,b in zip(colors[index],colors[index+1]))
    y_title = style.get('ylabel',options.get('ylabel',''))
    left = max(width(row,8) for row in rows)+13+(12 if y_title else 0)
    cell_w = 20
    tilted,rise,label_right,offsets = top_axis(list(columns),left+cell_w/2,cell_w,cell_w-4,style.get('font_size',7))
    cell_h,top = 13,31+rise
    grid_w,grid_h = len(columns)*cell_w,len(rows)*cell_h
    title = style.get('title',options['title'])
    figure_type = scene_renderer(style)
    f = LayoutScene(max(left+grid_w+12,label_right+4,width(title,8)+16),top+grid_h+38,style,figure_type)
    f.text(left+grid_w/2,11,title,'middle',True)
    if y_title:
        f.text(9,top+grid_h/2,y_title,'middle',angle=-90)
    for i,name in enumerate(columns):
        f.text(left+(i+.5)*cell_w,top-5-offsets[i],name,'start' if tilted else 'middle',angle=-45 if tilted else 0)
    for j,target in enumerate(rows):
        y = top+j*cell_h
        f.text(left-4,y+cell_h/2+2,target,'end')
        for i,group in enumerate(columns):
            value = means.get((target,group))
            fill = '#E5E5E5' if value is None else shade((value-low)/(high-low) if high>low else .5)
            f.rect(left+i*cell_w,y,cell_w,cell_h,fill,'#FFFFFF')
    legend_w = min(150,grid_w)
    y = top+grid_h+9
    for i in range(20):
        f.rect(left+i*legend_w/20,y,legend_w/20,6,shade(i/19))
    f.text(left,y+15,f'{low:.3g}')
    f.text(left+legend_w,y+15,f'{high:.3g}','end')
    if len(means)!=len(rows)*len(columns):
        f.text(left,y+25,'Gray: missing')
    return f if scene_only else f.export()
