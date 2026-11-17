"""Editable vector scene with named objects that retain their source image."""
import math
import re
from collections import defaultdict

LAYOUT = {'swap_groups','order_groups','move_group','add_text','add_line','add_rectangle','resize_canvas',
          'add_label','add_box','add_polyline','add_points','add_arrow','rebuild_canvas','place_image','edit_element',
          'add_gradient','add_line_label','transform_annotations'}
ANNOTATIONS_WITH_MARGIN = {'add_text','add_label','add_line','add_polyline','add_line_label'}
ANNOTATION_OPERATIONS = ANNOTATIONS_WITH_MARGIN | {'add_points','add_box','add_rectangle','add_arrow','add_gradient'}

ELEMENT_FIELDS = {
    'text': {'text':2,'angle':5,'color':6,'size':7},
    'image': {'width':2,'height':3},
    'line': {'x2':2,'y2':3,'color':4,'stroke_width':5},
    'rect': {'width':2,'height':3,'color':4,'border_color':5},
    'gradient': {'width':2,'height':3},
    'dot': {'color':2,'radius':3},
}
PROPERTY_ALIASES = {
    'font_size':'size', 'fontsize':'size', 'line_width':'stroke_width', 'linewidth':'stroke_width',
    'text_color':'color', 'fill_color':'color', 'edgecolor':'border_color', 'rotation':'angle',
}


def layout_command(command,values):
    if not isinstance(values,list) or len(values)>60:
        raise ValueError('画布命令参数应为列表。')
    def numeric(items):
        return all(type(n) in (int,float) and math.isfinite(n) and abs(n)<=4000 for n in items)
    def text(value):
        return isinstance(value,str) and 0<len(value)<=160
    def color(value):
        return isinstance(value,str) and re.fullmatch(r'#[0-9a-fA-F]{6}',value)
    valid = False
    if command in ('swap_groups','order_groups'):
        valid = len(values)==2 if command=='swap_groups' else len(values)>1
        valid = valid and all(text(v) for v in values) and len(set(values))==len(values)
    elif command=='edit_element':
        if len(values)==3 and type(values[0]) is int and values[0]>=1 and isinstance(values[1],str):
            index,name,value = values
            name = PROPERTY_ALIASES.get(name,name)
            values = [index,name,value]
            valid = ((name=='text' and text(value)) or (name in ('color','border_color') and color(value)) or
                     (name in ('x','y','width','height','size','angle','radius','x2','y2','stroke_width')
                      and numeric([value])))
    elif command=='move_group':
        valid = len(values)==3 and text(values[0]) and numeric(values[1:])
    elif command in ('resize_canvas','rebuild_canvas'):
        valid = len(values)==2 and numeric(values) and min(values)>=32
    elif command=='place_image':
        valid = len(values)==4 and text(values[0]) and numeric(values[1:]) and values[-1]>0
    elif command=='add_text':
        valid = (len(values) in (3,4) and numeric(values[:2]) and text(values[2]) and
                 (len(values)==3 or (numeric(values[3:]) and abs(values[3])<=360)))
    elif command=='add_line':
        valid = (len(values) in (5,6) and numeric(values[:4]) and color(values[4]) and
                 (len(values)==5 or (numeric(values[5:]) and .25<=values[5]<=8)))
    elif command=='add_line_label':
        valid = (len(values)==10 and numeric([*values[:4],*values[6:]]) and text(values[4])
                 and color(values[5]) and values[2:4]!=values[:2]
                 and .25<=values[6]<=8 and 4<=values[7]<=48
                 and abs(values[8])<=64 and abs(values[9])<=360)
    elif command=='add_rectangle':
        valid = len(values)==5 and numeric(values[:4]) and min(values[2:4])>0 and color(values[-1])
    elif command=='add_gradient':
        valid = (6<=len(values)<=20 and numeric(values[:4]) and min(values[2:4])>0
                 and all(color(item) for item in values[4:]))
    elif command=='add_label':
        valid = (len(values) in (6,7) and numeric([*values[:2],*values[5:]]) and text(values[2])
                 and color(values[3]) and values[4] in ('start','middle','end') and 4<=values[5]<=48
                 and (len(values)==6 or abs(values[6])<=360))
    elif command=='add_box':
        valid = (len(values)==6 and numeric(values[:4]) and min(values[2:4])>0
                 and color(values[4]) and type(values[5]) is bool)
    elif command=='add_polyline':
        if len(values)>=5 and len(values)%2==1:
            valid = numeric(values[:-1]) and color(values[-1])
        elif len(values)>=6:
            valid = numeric([*values[:-2],values[-1]]) and color(values[-2]) and .25<=values[-1]<=8
    elif command=='add_points':
        valid = (len(values)>=4 and len(values)%2==0 and numeric([*values[:-2],values[-1]])
                 and color(values[-2]) and .25<=values[-1]<=20)
    elif command=='add_arrow':
        valid = len(values)==6 and numeric(values[:4]) and text(values[4]) and color(values[5])
    elif command=='transform_annotations':
        valid = (len(values)==5 and isinstance(values[0],list) and len(values[0])==4
                 and numeric(values[0]) and numeric(values[1:])
                 and values[0][0]<values[0][2] and values[0][1]<values[0][3]
                 and all(.2<=factor<=3 for factor in values[3:]))
    if not valid:
        raise ValueError(f'画布命令 {command} 的参数无效。')
    return [command,values]


def horizontal_slots(style,count):
    """One slot per existing group, with extra empty slots after specified groups."""
    gaps = dict(style.get('group_gaps',[]))
    if any(boundary>=count for boundary in gaps):
        raise ValueError(f'当前横向排列有 {count} 项，空位须设在两个现有项目之间。')
    return [index+sum(gap for boundary,gap in gaps.items() if boundary<=index)
            for index in range(count)]


class LayoutScene:
    def __init__(self,width,height,style,renderer):
        self.width,self.height,self.style,self.renderer = width,height,style,renderer
        self.items = []
        self.element_roles = {}
        self.bars = {}
        self.observations = {}
        self.strip_segments = set()
        self.members = ()
        self.groups = {}
        self.horizontal_groups = []
        self.group_caption_centers = {}
        self.sources = {}
        self.raw_sources = {}
        self.source_loader = None

    def load_source(self,key):
        if key in self.sources:
            return self.sources[key]
        if key not in self.raw_sources or self.source_loader is None:
            raise ValueError('图像不在当前作图项目中，或选区已被删除。')
        image = self.source_loader(key)
        self.sources[key] = (image,None)
        return self.sources[key]

    def group(self,name,axis,position):
        self.groups[name] = (axis,position)

    def add(self,kind,args,source_id=None,role=None):
        self.items.append((kind,args,self.members,source_id))
        if role:
            self.element_roles[len(self.items)] = role

    def text(self,x,y,value,anchor='start',bold=False,angle=0):
        self.add('text',(x,y,value,anchor,bold,angle))

    def line(self,*args):
        self.add('line',args)

    def gradient(self,*args):
        self.add('gradient',args)

    def rect(self,*args,**kwargs):
        if kwargs:
            args = (*args[:4],kwargs.get('fill'),kwargs.get('border'))
        self.add('rect',args)

    def strip_segment(self,*args):
        self.strip_segments.add(len(self.items)+1)
        self.rect(*args)

    def bar(self,x,y,w,h,color,positive):
        self.bars[len(self.items)+1] = color,positive
        self.rect(x,y,w,h,color)

    def image(self,*args,source_id=None,scale_bar=None):
        self.add('image',args,source_id)
        if source_id:
            self.sources[source_id] = (args[4],scale_bar)

    def dot(self,*args):
        self.add('dot',args)

    def observation(self,row,*args):
        from figure_data import observation_key
        self.observations[len(self.items)+1] = [observation_key(row),row['source'],row['row'],
                                               row.get('column',1),row['group'],row['target'],row['value'],
                                               row.get('raw_value',row['value'])]
        self.dot(*args)

    def observation_positions(self):
        width,height,offsets,annotations,custom = self.resolve_layout()
        if custom:
            return []
        left,top,right,bottom = self.annotation_margin(width,height,annotations)
        width += left+right
        height += top+bottom
        edits = self.element_edits(annotations)
        result = []
        for index,source in self.observations.items():
            kind,args,members,_ = self.items[index-1]
            x,y,*_ = self.edit_element(kind,args,edits.get(index,[]))
            dx,dy = self.translation(members,offsets)
            result.append([*source,(x+dx+left)/width,(y+dy+top)/height])
        return result

    def polygon(self,*args):
        self.add('polygon',args)

    def metadata(self):
        width,height,offsets,annotations,custom = self.resolve_layout()
        margin = self.annotation_margin(width,height,annotations)
        images = []
        elements = []
        edits = self.element_edits(annotations)
        if custom:
            for operation,values in annotations:
                if operation=='place_image':
                    key,*box,_ = self.placed_image(values)
                    images.append(dict(id=key,rect_pt=box))
        else:
            for index,(kind,args,members,source_id) in enumerate(self.items,1):
                args = self.edit_element(kind,args,edits.get(index,[]))
                dx,dy = self.translation(members,offsets)
                if kind=='image':
                    x,y,w,h = args[:4]
                    images.append(dict(id=source_id,groups=list(members),
                                       rect_pt=[round(n,2) for n in (x+dx,y+dy,w,h)]))
                if kind!='polygon':
                    data = [*args[:4]] if kind=='image' else list(args)
                    data[:2] = [round(data[0]+dx,3),round(data[1]+dy,3)]
                    elements.append(dict(id=index,kind=kind,args=data,source_id=source_id,
                                         editable_properties=['x','y',*ELEMENT_FIELDS[kind]]))
                    if index in self.element_roles:
                        elements[-1]['role'] = self.element_roles[index]
        left,top,right,bottom = margin
        return dict(width_pt=width,height_pt=height,origin_offset_pt=[left,top],
                    preview_size_pt=[width+left+right,height+top+bottom],images=images,elements=elements,custom=custom,
                    horizontal_groups=self.horizontal_groups if not custom else [],
                    annotations=[entry for entry in annotations if entry[0] in ANNOTATION_OPERATIONS],
                    source_images=[dict(id=key,pixels=list(image.size)) for key,(image,_) in self.sources.items()
                                   if key not in self.raw_sources],
                    local_images=[dict(id=key,name=name) for key,name in self.raw_sources.items()],groups=[
            dict(id=name,axis='x' if axis==0 else 'y',anchor_pt=round(position+offsets[name][axis],2),
                 offset_pt=offsets[name]) for name,(axis,position) in self.groups.items() if not custom])

    @staticmethod
    def translation(members,offsets):
        return (sum(offsets[group][0] for group in members),
                sum(offsets[group][1] for group in members))

    def resolve_layout(self):
        offsets = defaultdict(lambda:[0.,0.])
        annotations = []
        width,height = self.width,self.height
        custom = False
        for operation,values in self.style.get('layout_commands',[]):
            if operation in ('swap_groups','order_groups'):
                if any(key not in self.groups for key in values):
                    raise ValueError('要调整的画布对象已不存在。请重新指定图中的对象。')
                axes = {self.groups[key][0] for key in values}
                if len(axes)!=1:
                    raise ValueError('请对同一方向的画布对象进行排序。')
                axis = axes.pop()
                positions = [self.groups[key][1]+offsets[key][axis] for key in values]
                next_positions = positions[::-1] if operation=='swap_groups' else sorted(positions)
                for key,position in zip(values,next_positions):
                    offsets[key][axis] = position-self.groups[key][1]
            elif operation=='move_group':
                key,dx,dy = values
                if key not in self.groups:
                    raise ValueError('要移动的画布对象已不存在。')
                offsets[key][0] += dx
                offsets[key][1] += dy
            elif operation=='rebuild_canvas':
                width,height = values
                custom = True
                annotations.clear()
            elif operation=='resize_canvas':
                width,height = values
                if not custom and (width<self.width or height<self.height):
                    raise ValueError('画布扩展不能裁掉当前实验数据。')
            elif operation=='transform_annotations':
                annotations = self.transform_annotations(annotations,values,offsets,custom)
            else:
                if not custom and operation in ('add_text','add_label') and values[2] in self.group_caption_centers:
                    x = self.group_caption_centers[values[2]]
                    if operation=='add_label':
                        values = [x,*values[1:4],'middle',*values[5:]]
                    else:
                        operation = 'add_label'
                        angle = values[3] if len(values)==4 else 0
                        values = [x,*values[1:3],'#171717','middle',self.style.get('font_size',7),angle]
                annotations.append((operation,values))
        if custom and (self.style.get('group_gaps') or self.style.get('wb_lane_centers')):
            raise ValueError('此图使用自定义画布。请在当前画布按图元坐标对齐，或从软件原图应用组间空位。')
        return width,height,offsets,annotations,custom

    def transform_annotations(self,annotations,values,offsets,custom):
        (left,top,right,bottom),dx,dy,sx,sy = values
        def inside(x,y):
            return left<=x<=right and top<=y<=bottom
        def move(x,y):
            return x+(x-left)*(sx-1)+dx,y+(y-top)*(sy-1)+dy

        font_size = self.style.get('font_size',7)
        changes = []
        previous_edits = self.element_edits(annotations)
        for index,(kind,args,members,_) in enumerate(() if custom else self.items,1):
            if kind!='text':
                continue
            x,y,label,anchor,_,angle,*appearance = self.edit_element(kind,args,previous_edits.get(index,[]))
            if not label.strip():
                continue
            ox,oy = self.translation(members,offsets)
            color,size = appearance or ('#171717',font_size)
            marker = [x+ox,y+oy,label,color,anchor,size,angle]
            new = transform_annotation('add_label',marker,inside,move,font_size)
            if new!=marker:
                changes.extend([('edit_element',[index,'x',new[0]-ox]),('edit_element',[index,'y',new[1]-oy])])
        adjusted = [(op,transform_annotation(op,args,inside,move,font_size)) for op,args in annotations]
        if not changes and adjusted==annotations:
            raise ValueError('框选区域内没有可移动的文字或标注。请重新框选对应的图元。')
        return adjusted+changes

    def placed_image(self,values):
        key,x,y,w = values
        image,scale_bar = self.load_source(key)
        return key,x,y,w,w*image.height/image.width,(image,scale_bar)

    def element_edits(self,annotations):
        edits = defaultdict(list)
        for op,values in annotations:
            if op=='edit_element':
                index,property_name,value = values
                if index>len(self.items):
                    raise ValueError('找不到需要调整的图件元素。')
                edits[index].append((property_name,value))
        return edits

    def edit_element(self,kind,args,edits):
        if not edits:
            return args
        args = list(args)
        if kind=='text':
            args += ['#171717',self.style.get('font_size',7)][len(args)-6:]
        fields = ELEMENT_FIELDS.get(kind)
        if fields is None:
            raise ValueError('该图元类型不支持直接编辑。')
        for name,value in edits:
            name = PROPERTY_ALIASES.get(name,name)
            if kind=='line' and name in ('width','size'):
                name = 'stroke_width'
            elif kind=='dot' and name=='size':
                name = 'radius'
            column = {'x':0,'y':1,**fields}.get(name)
            if column is None:
                raise ValueError(f'图元 {kind} 不支持修改 {name}。请按其可编辑属性调整。')
            if name in ('color','border_color') and not re.fullmatch('#[0-9a-fA-F]{6}',value):
                raise ValueError('图元颜色必须使用六位十六进制颜色。')
            if name=='stroke_width' and not .25<=value<=8:
                raise ValueError('线宽必须为 0.25–8 pt。')
            if name in ('width','height','size','radius') and not 0<value<=4000:
                raise ValueError('图元尺寸必须大于零。')
            if kind=='image' and name in ('width','height'):
                other = 5-column
                args[other] *= value/args[column]
            if column>=len(args):
                defaults = ('#171717',None) if kind=='line' else (None,None)
                args.extend(defaults[len(args)-4:column-3])
            args[column] = value
        return tuple(args)

    def annotation_margin(self,width,height,annotations):
        """Add space for nearby annotations while keeping the plot geometry."""
        left,top,right,bottom = 0,0,width,height
        for operation,values in annotations:
            if operation not in ANNOTATIONS_WITH_MARGIN:
                continue
            for _,_,bounds in annotation_items(operation,values,self.style.get('font_size',7),self.style.get('stroke',.7)):
                if (bounds[2]<-24 or bounds[3]<-24 or bounds[0]>width+24 or bounds[1]>height+24):
                    raise ValueError('标注离当前图件太远。请使用预览标记对应的画布坐标。')
                left,top = min(left,bounds[0]),min(top,bounds[1])
                right,bottom = max(right,bounds[2]),max(bottom,bounds[3])
        margin = tuple(math.ceil(n+2) if n>.001 else 0 for n in (-left,-top,right-width,bottom-height))
        if any(margin) and (max(margin)>96 or width+margin[0]+margin[2]>4000 or height+margin[1]+margin[3]>4000):
            raise ValueError('标注需要扩展的留白太大。请调整标注坐标。')
        return margin

    def export(self):
        width,height,offsets,annotations,custom = self.resolve_layout()
        moved = []
        strip = []
        strip_index = None
        images = []
        edits = self.element_edits(annotations)
        bar_width = self.style.get('bar_outline_width')
        legacy_outlines = {}
        if bar_width and self.bars and not custom:
            for position,(op,values) in enumerate(annotations):
                if op=='add_rectangle' and len(values)==5:
                    key = tuple(round(n,2) for n in values[:4])
                    legacy_outlines[key] = position,values[4]
        replaced = set()
        for index,(kind,args,members,_) in enumerate(() if custom else self.items,1):
            args = self.edit_element(kind,args,edits.get(index,[]))
            dx,dy = self.translation(members,offsets)
            if kind=='polygon':
                args = ([(x+dx,y+dy) for x,y in args[0]],*args[1:])
            elif kind=='line':
                args = (args[0]+dx,args[1]+dy,args[2]+dx,args[3]+dy,*args[4:])
            else:
                args = (args[0]+dx,args[1]+dy,*args[2:])
            if index in self.strip_segments and self.style.get('x_strip_gradient'):
                if strip_index is None:
                    strip_index = len(moved)
                strip.append(args)
                continue
            if kind=='image':
                x,y,w,h = args[:4]
                bounds = (x,y,x+w,y+h)
                if x<-.001 or y<-.001 or x+w>width+.001 or y+h>height+.001 or any(
                        overlaps(bounds,box) for box in images):
                    raise ValueError('新排版会重叠或裁掉实验图片。请扩大画布或调整对象位置。')
                images.append(bounds)
            moved.append((kind,args))
            if bar_width and index in self.bars:
                x,y,w,h,*fill = args
                color,positive = self.bars[index]
                legacy = legacy_outlines.get(tuple(round(n,2) for n in (x,y,w,h)))
                if legacy:
                    position,color = legacy
                    replaced.add(position)
                moved.append(('bar_outline',(x,y,w,h,color,bar_width,positive,self.style.get('bar_outline_open',False))))
        if strip:
            moved[strip_index:strip_index] = strip_gradients(strip)
        for op,values in annotations:
            if op!='place_image':
                continue
            if not self.sources and not self.raw_sources:
                raise ValueError('当前作图项目没有可使用的图像。')
            _,x,y,w,h,(image,bar) = self.placed_image(values)
            bounds = (x,y,x+w,y+h)
            if min(x,y)<0 or x+w>width+.001 or y+h>height+.001:
                raise ValueError('放置的原图超出画布。请调整图像位置、宽度或画布尺寸。')
            images.append(bounds)
            moved.append(('image',(x,y,w,h,image)))
            if bar:
                left,top,length,thickness = bar
                moved.append(('rect',(x+left*w,y+top*h,length*w,thickness*h,'#FFFFFF')))
        if custom and not images:
            raise ValueError('新图像画布至少需要放置一个本地原图或选区。')
        left,top,right,bottom = self.annotation_margin(width,height,annotations)
        for position,(op,values) in enumerate(annotations):
            if position in replaced or op in ('place_image','edit_element'):
                continue
            for kind,args,bounds in annotation_items(op,values,self.style.get('font_size',7),self.style.get('stroke',.7)):
                if bounds[0]<-left-.001 or bounds[1]<-top-.001 or bounds[2]>width+right+.001 or bounds[3]>height+bottom+.001:
                    raise ValueError('新标注超出画布。请调整标注坐标。')
                moved.append((kind,args))
        figure = self.renderer(width+left+right,height+top+bottom,self.style)
        for kind,args in moved:
            if left or top:
                if kind=='polygon':
                    args = ([(x+left,y+top) for x,y in args[0]],*args[1:])
                elif kind=='line':
                    args = (args[0]+left,args[1]+top,args[2]+left,args[3]+top,*args[4:])
                else:
                    args = (args[0]+left,args[1]+top,*args[2:])
            getattr(figure,kind)(*args)
        return figure.export()


def transform_annotation(operation,args,inside,move,font_size):
    if operation not in ANNOTATION_OPERATIONS:
        return args
    if operation=='add_points':
        points = zip(args[:-2:2],args[1:-2:2])
        coordinates = [v for x,y in points for v in (move(x,y) if inside(x,y) else (x,y))]
        return [*coordinates,*args[-2:]]
    if operation in ('add_text','add_label','add_rectangle','add_box','add_gradient'):
        x,y = args[:2]
        _,_,(x0,y0,x1,y1) = next(annotation_items(operation,args,font_size))
        cx,cy = (x0+x1)/2,(y0+y1)/2
        if inside(cx,cy):
            tx,ty = move(cx,cy)
            return [x+(tx-cx),y+(ty-cy),*args[2:]]
        return args
    coordinate_end = len(args)-(1 if len(args)%2 else 2) if operation=='add_polyline' else 4
    points = list(zip(args[:coordinate_end:2],args[1:coordinate_end:2]))
    if all(inside(x,y) for x,y in points):
        return [*[v for x,y in points for v in move(x,y)],*args[coordinate_end:]]
    return args


def strip_gradients(segments):
    # Existing element edits can position the original rectangles individually.
    rows = defaultdict(list)
    for x,y,w,h,color,*_ in segments:
        rows[y,h].append((x,w,color))
    for (y,h),row in rows.items():
        row.sort(key=lambda segment:segment[0]+segment[1]/2)
        left = min(x for x,_,_ in row)
        width = max(x+w for x,w,_ in row)-left
        colors = [color for _,_,color in row]
        stops = [(x+w/2-left)/width for x,w,_ in row]
        yield 'gradient',(left,y,width,h,[colors[0],*colors,colors[-1]],[0.,*stops,1.])


def annotation_items(operation,values,font_size,stroke=.7):
    x,y = values[:2]
    if operation in ('add_text','add_label'):
        label = values[2]
        color,anchor,size = values[3:6] if operation=='add_label' else ('#171717','start',font_size)
        angle = values[6] if len(values)==7 else values[3] if len(values)==4 else 0
        from figure_render import width as measure
        label_width = measure(label,size)
        left = -label_width*{'start':0,'middle':.5,'end':1}[anchor]
        cosine,sine = math.cos(math.radians(angle)),math.sin(math.radians(angle))
        corners = [(x+dx*cosine-dy*sine, y+dx*sine+dy*cosine)
                   for dx in (left,left+label_width) for dy in (-size,size*.3)]
        xs,ys = zip(*corners)
        yield 'text',(x,y,label,anchor,False,angle,color,size),(min(xs),min(ys),max(xs),max(ys))
    elif operation=='add_line_label':
        xx,yy,label,color,line_width,size,offset,angle = values[2:]
        yield from annotation_items('add_line',(x,y,xx,yy,color,line_width),font_size,stroke)
        midpoint = (x+xx)/2,(y+yy)/2
        normal = (-1,0) if abs(yy-y)>abs(xx-x) else (0,-1)
        tx,ty = (point+offset*direction for point,direction in zip(midpoint,normal))
        yield from annotation_items('add_label',(tx,ty,label,color,'middle',size,angle),font_size,stroke)
    elif operation in ('add_rectangle','add_box'):
        _,_,w,h,color,*fill = values
        yield 'rect',(x,y,w,h,color if fill and fill[0] else None,color),(x-.5,y-.5,x+w+.5,y+h+.5)
    elif operation=='add_gradient':
        _,_,w,h,*colors = values
        yield 'gradient',(x,y,w,h,colors),(x,y,x+w,y+h)
    elif operation=='add_points':
        color,radius = values[-2:]
        for px,py in zip(values[:-2:2],values[1:-2:2]):
            yield 'dot',(px,py,color,radius),(px-radius,py-radius,px+radius,py+radius)
    elif operation=='add_polyline':
        tail = 1 if len(values)%2 else 2
        coordinates,appearance = values[:-tail],values[-tail:]
        points = zip(coordinates[::2],coordinates[1::2])
        start = next(points)
        for end in points:
            yield from annotation_items('add_line',(*start,*end,*appearance),font_size,stroke)
            start = end
    elif operation=='add_arrow':
        xx,yy,label,color = values[2:]
        angle = math.atan2(yy-y,xx-x)
        for turn in (-.48,.48):
            yield from annotation_items('add_line',(xx,yy,xx-4*math.cos(angle+turn),
                                                    yy-4*math.sin(angle+turn),color),font_size,stroke)
        yield from annotation_items('add_line',(x,y,xx,yy,color),font_size,stroke)
        yield from annotation_items('add_label',(x,y-2,label,color,'start',font_size),font_size)
    else:
        xx,yy,color,*explicit = values[2:]
        line_width = explicit[0] if explicit else stroke
        half = line_width/2
        yield 'line',(x,y,xx,yy,color,line_width),(min(x,xx)-half,min(y,yy)-half,max(x,xx)+half,max(y,yy)+half)


def overlaps(a,b):
    return min(a[2],b[2])-max(a[0],b[0])>1e-6 and min(a[3],b[3])-max(a[1],b[1])>1e-6
