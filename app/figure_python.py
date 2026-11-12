"""Evaluate the bounded Python subset used for model-generated figure revisions."""
import ast
import math
import operator
import statistics


LIBRARIES = {
    'numpy': 'np',
    'matplotlib.pyplot': 'plt',
    'matplotlib.colors': 'colors',
    'math': 'math',
    'statistics': 'statistics',
}
ARITHMETIC = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
}
COMPARISONS = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
    ast.Gt: operator.gt, ast.GtE: operator.ge,
}
PLOT = {
    'text': 'add_label', 'plot': 'add_polyline', 'scatter': 'add_points',
    'rectangle': 'add_box', 'annotate': 'add_arrow',
    'title': 'set_title', 'xlabel': 'set_xlabel', 'ylabel': 'set_ylabel',
    'imshow': 'place_image',
}


def safe_value(value):
    if type(value) in (bool,type(None)):
        return value
    if type(value) in (int,float) and abs(value)<=1e9 and math.isfinite(value):
        return value
    if isinstance(value,str) and len(value)<=400:
        return value
    if isinstance(value,(list,tuple)) and len(value)<=60:
        return value
    if isinstance(value,dict) and len(value)<=60 and all(isinstance(key,str) for key in value):
        return value
    raise ValueError('脚本中的参数超出绘图范围。')


def numeric(value):
    if type(value) not in (int,float):
        raise ValueError('绘图坐标运算只接受数值。')
    return value


def linspace(start,stop,num=10):
    from numpy import linspace as calculate
    if type(num) is not int or not 1<=num<=60:
        raise ValueError('np.linspace 最多支持 60 个坐标。')
    return calculate(numeric(start),numeric(stop),num).tolist()


def clip(value,lower,upper):
    from numpy import clip as calculate
    return float(calculate(numeric(value),numeric(lower),numeric(upper)))


def to_hex(color):
    from matplotlib.colors import to_hex as convert
    if isinstance(color,(list,tuple)):
        if len(color)!=3 or any(not 0<=numeric(part)<=1 for part in color):
            raise ValueError('RGB 颜色须为三个 0–1 数值。')
    elif not isinstance(color,str):
        raise ValueError('颜色须为名称、十六进制色值或 RGB。')
    return convert(color)


def points(xs,ys):
    if not isinstance(xs,(list,tuple)) or not isinstance(ys,(list,tuple)) or len(xs)!=len(ys):
        raise ValueError('X、Y 坐标须为长度相同的列表。')
    return [item for pair in zip(xs,ys) for item in pair]


PLOT_ARGS = {
    'text': lambda x,y,s,color='#171717',ha='left',fontsize=7: [
        x,y,s,to_hex(color),{'left':'start','center':'middle','right':'end'}[ha],fontsize],
    'plot': lambda x,y,color='#171717': [*points(x,y),to_hex(color)],
    'scatter': lambda x,y,color='#171717',radius=1.5: [*points(x,y),to_hex(color),radius],
    'rectangle': lambda x,y,w,h,color='#171717',fill=False: [x,y,w,h,to_hex(color),fill],
    'annotate': lambda text,xy,xytext,color='#171717': [*xytext,*xy,text,to_hex(color)],
    'imshow': lambda source,x,y,width: [source,x,y,width],
}
LIBRARY_CALLS = {
    'np': {'linspace': linspace, 'clip': clip},
    'colors': {'to_hex': to_hex},
    'math': {'sqrt':math.sqrt,'log':math.log,'log10':math.log10,'sin':math.sin,'cos':math.cos},
    'statistics': {'mean':statistics.mean,'median':statistics.median,'stdev':statistics.stdev},
}


class PlotScript:
    def __init__(self,commands,scene,source_size):
        self.commands = commands
        self.values = {}
        self.aliases = {}
        self.scene = scene or {}
        self.source_size = source_size
        self.output = []
        self.steps = 0
        self.backend = None

    def step(self):
        self.steps += 1
        if self.steps>2400:
            raise ValueError('绘图脚本的运算次数过多。')

    def emit(self,name,args):
        if name=='use_backend':
            if args==self.backend:
                return
            self.backend = args
        if len(self.output)>=96:
            raise ValueError('绘图脚本产生的操作过多。')
        self.output.append((name,args))

    def statements(self,nodes):
        for node in nodes:
            self.step()
            if isinstance(node,ast.Import):
                for item in node.names:
                    if item.name not in LIBRARIES or not item.asname or item.asname.startswith('_'):
                        raise ValueError('绘图脚本只支持带别名的内置绘图库导入。')
                    self.aliases[item.asname] = LIBRARIES[item.name]
            elif isinstance(node,ast.ImportFrom) and node.level==0 and node.module=='PIL' and len(node.names)==1:
                item = node.names[0]
                if item.name!='Image' or (item.asname and item.asname.startswith('_')):
                    raise ValueError('Pillow 仅开放当前项目图像的尺寸读取。')
                self.aliases[item.asname or 'Image'] = 'PIL.Image'
            elif isinstance(node,ast.Assign) and len(node.targets)==1:
                self.assign(node.targets[0],self.value(node.value))
            elif isinstance(node,ast.For) and not node.orelse:
                values = self.value(node.iter)
                if not isinstance(values,(list,tuple)):
                    raise ValueError('绘图循环需要坐标列表。')
                for value in values:
                    self.assign(node.target,value)
                    self.statements(node.body)
            elif isinstance(node,ast.If):
                self.statements(node.body if self.value(node.test) else node.orelse)
            elif isinstance(node,ast.Expr) and isinstance(node.value,ast.Call):
                self.value(node.value)
            else:
                raise ValueError('脚本含有未支持的 Python 绘图操作。')

    def assign(self,target,value):
        if isinstance(target,ast.Name) and not target.id.startswith('_') and target.id not in self.aliases:
            self.values[target.id] = value
        elif isinstance(target,ast.Tuple) and isinstance(value,(list,tuple)) and len(target.elts)==len(value):
            for name,item in zip(target.elts,value):
                self.assign(name,item)
        else:
            raise ValueError('绘图脚本只能为局部变量赋值。')

    def value(self,node):
        self.step()
        if isinstance(node,ast.Constant):
            return safe_value(node.value)
        if isinstance(node,ast.Name) and node.id in self.values:
            return self.values[node.id]
        if isinstance(node,(ast.List,ast.Tuple)):
            return safe_value([self.value(item) for item in node.elts])
        if isinstance(node,ast.Dict) and all(key is not None for key in node.keys):
            return safe_value({self.value(key):self.value(value) for key,value in zip(node.keys,node.values)})
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
            return safe_value(numeric(self.value(node.operand))*(-1 if isinstance(node.op,ast.USub) else 1))
        if isinstance(node,ast.BinOp) and type(node.op) in ARITHMETIC:
            left,right = self.value(node.left),self.value(node.right)
            if isinstance(node.op,ast.Add) and type(left) is str and type(right) is str:
                return safe_value(left+right)
            return safe_value(ARITHMETIC[type(node.op)](numeric(left),numeric(right)))
        if isinstance(node,ast.Subscript):
            values,index = self.value(node.value),self.value(node.slice)
            if isinstance(values,(list,tuple,str)) and type(index) is int:
                return values[index]
        if isinstance(node,ast.Compare) and len(node.ops)==1 and type(node.ops[0]) in COMPARISONS:
            return COMPARISONS[type(node.ops[0])](self.value(node.left),self.value(node.comparators[0]))
        if isinstance(node,ast.Call):
            return self.call(node)
        raise ValueError('脚本使用了未支持的绘图表达式。')

    def call(self,node):
        if any(key.arg is None for key in node.keywords):
            raise ValueError('脚本不能展开关键字参数。')
        args = [self.value(arg) for arg in node.args]
        kwargs = {key.arg:self.value(key.value) for key in node.keywords}
        if isinstance(node.func,ast.Name) and not kwargs:
            name = node.func.id
            if name in self.commands:
                self.emit(name,args)
                return None
            if name=='range' and 1<=len(args)<=3 and all(type(arg) is int for arg in args):
                result = range(*args)
                if len(result)<=60:
                    return list(result)
            if name=='enumerate' and len(args)==1 and isinstance(args[0],(list,tuple)):
                return list(enumerate(args[0]))
            if name=='len' and len(args)==1 and isinstance(args[0],(list,tuple,str)):
                return len(args[0])
            if name in ('canvas_width','canvas_height') and not args:
                return self.scene['width_pt' if name=='canvas_width' else 'height_pt']
            if name=='group_anchor' and len(args)==1:
                return next(group['anchor_pt'] for group in self.scene['groups'] if group['id']==args[0])
        if isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Name):
            library = self.aliases.get(node.func.value.id)
            name = node.func.attr
            if library=='PIL.Image' and name=='size' and self.source_size and len(args)==1 and not kwargs:
                return self.source_size(args[0])
            if library=='plt' and name in PLOT:
                self.emit('use_backend',['matplotlib'])
                self.emit(PLOT[name],[PLOT_ARGS[name](*args,**kwargs)] if name in PLOT_ARGS else args)
                if kwargs and name not in PLOT_ARGS:
                    raise ValueError('该绘图操作只接受位置参数。')
                return None
            function = LIBRARY_CALLS.get(library,{}).get(name)
            if function:
                return safe_value(function(*args,**kwargs))
        raise ValueError('脚本只能调用已注册的本地绘图函数。')


def compile_plot_script(script,commands,scene=None,source_size=None):
    if not isinstance(script,str) or len(script)>8192:
        raise ValueError('模型返回的绘图脚本无效或过长，已保留上次图件。')
    try:
        root = ast.parse(script,mode='exec')
        compiler = PlotScript(commands,scene,source_size)
        compiler.statements(root.body)
    except (SyntaxError,RecursionError,TypeError,KeyError,IndexError,StopIteration,ArithmeticError):
        raise ValueError('绘图脚本的语法、坐标或参数无效，已保留上次图件。') from None
    return compiler.output
