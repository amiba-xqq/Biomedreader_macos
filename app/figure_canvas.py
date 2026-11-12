"""Editable vector scene with named objects that retain their source image."""
import math
import re
from collections import defaultdict

LAYOUT = {'swap_groups','order_groups','move_group','add_text','add_line','add_rectangle','resize_canvas',
          'add_label','add_box','add_polyline','add_points','add_arrow','rebuild_canvas','place_image','edit_element'}


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
        valid = (len(values)==3 and type(values[0]) is int and values[0]>=1 and
                 ((values[1] in ('text','color') and text(values[2])) or
                  (values[1] in ('x','y','width','height','size','angle','radius','x2','y2') and numeric(values[2:]))))
    elif command=='move_group':
        valid = len(values)==3 and text(values[0]) and numeric(values[1:])
    elif command in ('resize_canvas','rebuild_canvas'):
        valid = len(values)==2 and numeric(values) and min(values)>=32
    elif command=='place_image':
        valid = len(values)==4 and text(values[0]) and numeric(values[1:]) and values[-1]>0
    elif command=='add_text':
        valid = len(values)==3 and numeric(values[:2]) and text(values[2])
    elif command=='add_line':
        valid = len(values)==5 and numeric(values[:4]) and color(values[-1])
    elif command=='add_rectangle':
        valid = len(values)==5 and numeric(values[:4]) and min(values[2:4])>0 and color(values[-1])
    elif command=='add_label':
        valid = (len(values)==6 and numeric([*values[:2],values[5]]) and text(values[2])
                 and color(values[3]) and values[4] in ('start','middle','end') and 4<=values[5]<=48)
    elif command=='add_box':
        valid = (len(values)==6 and numeric(values[:4]) and min(values[2:4])>0
                 and color(values[4]) and type(values[5]) is bool)
    elif command=='add_polyline':
        valid = len(values)>=5 and len(values)%2==1 and numeric(values[:-1]) and color(values[-1])
    elif command=='add_points':
        valid = (len(values)>=4 and len(values)%2==0 and numeric([*values[:-2],values[-1]])
                 and color(values[-2]) and .25<=values[-1]<=20)
    elif command=='add_arrow':
        valid = len(values)==6 and numeric(values[:4]) and text(values[4]) and color(values[5])
    if not valid:
        raise ValueError(f'画布命令 {command} 的参数无效。')
    return [command,values]


class LayoutScene:
    def __init__(self,width,height,style,renderer):
        self.width,self.height,self.style,self.renderer = width,height,style,renderer
        self.items = []
        self.members = ()
        self.groups = {}
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

    def add(self,kind,args,source_id=None):
        self.items.append((kind,args,self.members,source_id))

    def text(self,x,y,value,anchor='start',bold=False,angle=0):
        self.add('text',(x,y,value,anchor,bold,angle))

    def line(self,*args):
        self.add('line',args)

    def rect(self,*args,**kwargs):
        if kwargs:
            args = (*args[:4],kwargs.get('fill'),kwargs.get('border'))
        self.add('rect',args)

    def image(self,*args,source_id=None,scale_bar=None):
        self.add('image',args,source_id)
        if source_id:
            self.sources[source_id] = (args[4],scale_bar)

    def dot(self,*args):
        self.add('dot',args)

    def polygon(self,*args):
        self.add('polygon',args)

    def metadata(self):
        width,height,offsets,annotations,custom = self.resolve_layout()
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
                    elements.append(dict(id=index,kind=kind,args=data,source_id=source_id))
        return dict(width_pt=width,height_pt=height,images=images,elements=elements,custom=custom,
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
            else:
                annotations.append((operation,values))
        return width,height,offsets,annotations,custom

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
        fields = {'text':{'text':2,'angle':5,'color':6,'size':7},'image':{'width':2,'height':3},
                  'line':{'x2':2,'y2':3,'color':4},'rect':{'width':2,'height':3,'color':4},
                  'dot':{'color':2,'radius':3}}.get(kind)
        if fields is None:
            raise ValueError('该图元类型不支持直接编辑。')
        for name,value in edits:
            column = {'x':0,'y':1,**fields}.get(name)
            if column is None or (name=='color' and not re.fullmatch('#[0-9a-fA-F]{6}',value)):
                raise ValueError('该图元不支持此属性。')
            if name in ('width','height','size','radius') and not 0<value<=4000:
                raise ValueError('图元尺寸必须大于零。')
            if kind=='image' and name in ('width','height'):
                other = 5-column
                args[other] *= value/args[column]
            if column==len(args):
                args.append(value)
            else:
                args[column] = value
        return tuple(args)

    def export(self):
        width,height,offsets,annotations,custom = self.resolve_layout()
        moved = []
        images = []
        edits = self.element_edits(annotations)
        for index,(kind,args,members,_) in enumerate(() if custom else self.items,1):
            args = self.edit_element(kind,args,edits.get(index,[]))
            dx,dy = self.translation(members,offsets)
            if kind=='polygon':
                args = ([(x+dx,y+dy) for x,y in args[0]],*args[1:])
            elif kind=='line':
                args = (args[0]+dx,args[1]+dy,args[2]+dx,args[3]+dy,*args[4:])
            else:
                args = (args[0]+dx,args[1]+dy,*args[2:])
            if kind=='image':
                x,y,w,h = args[:4]
                bounds = (x,y,x+w,y+h)
                if x<-.001 or y<-.001 or x+w>width+.001 or y+h>height+.001 or any(
                        overlaps(bounds,box) for box in images):
                    raise ValueError('新排版会重叠或裁掉实验图片。请扩大画布或调整对象位置。')
                images.append(bounds)
            moved.append((kind,args))
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
        for op,values in annotations:
            if op in ('place_image','edit_element'):
                continue
            for kind,args,bounds in annotation_items(op,values,self.style.get('font_size',7)):
                if min(bounds[:2])<0 or bounds[2]>width or bounds[3]>height:
                    raise ValueError('新标注超出画布。请调整标注坐标。')
                moved.append((kind,args))
        figure = self.renderer(width,height,self.style)
        for kind,args in moved:
            getattr(figure,kind)(*args)
        return figure.export()


def annotation_items(operation,values,font_size):
    x,y = values[:2]
    if operation in ('add_text','add_label'):
        label = values[2]
        color,anchor,size = values[3:] if operation=='add_label' else ('#171717','start',font_size)
        from figure_render import width as measure
        label_width = measure(label,size)
        left = x-label_width*{'start':0,'middle':.5,'end':1}[anchor]
        yield 'text',(x,y,label,anchor,False,0,color,size),(left,y-size,left+label_width,y+size*.3)
    elif operation in ('add_rectangle','add_box'):
        _,_,w,h,color,*fill = values
        yield 'rect',(x,y,w,h,color if fill and fill[0] else None,color),(x-.5,y-.5,x+w+.5,y+h+.5)
    elif operation=='add_points':
        color,radius = values[-2:]
        for px,py in zip(values[:-2:2],values[1:-2:2]):
            yield 'dot',(px,py,color,radius),(px-radius,py-radius,px+radius,py+radius)
    elif operation=='add_polyline':
        points = list(zip(values[:-1:2],values[1:-1:2]))
        for start,end in zip(points,points[1:]):
            yield from annotation_items('add_line',(*start,*end,values[-1]),font_size)
    elif operation=='add_arrow':
        xx,yy,label,color = values[2:]
        angle = math.atan2(yy-y,xx-x)
        for turn in (-.48,.48):
            yield from annotation_items('add_line',(xx,yy,xx-4*math.cos(angle+turn),
                                                    yy-4*math.sin(angle+turn),color),font_size)
        yield from annotation_items('add_line',(x,y,xx,yy,color),font_size)
        yield from annotation_items('add_label',(x,y-2,label,color,'start',font_size),font_size)
    else:
        xx,yy,color = values[2:]
        yield 'line',(x,y,xx,yy,color),(min(x,xx)-.75,min(y,yy)-.75,max(x,xx)+.75,max(y,yy)+.75)


def overlaps(a,b):
    return min(a[2],b[2])-max(a[0],b[0])>1e-6 and min(a[3],b[3])-max(a[1],b[1])>1e-6
