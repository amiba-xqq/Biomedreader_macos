"""Evidence-linked molecular scene, not a chain of text boxes."""
import math,re,colorsys
from html import escape

def safe_color(value,fallback,light=False):
    if not isinstance(value,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',value):value=fallback
    rgb=tuple(int(value[i:i+2],16)/255 for i in (1,3,5));h,s,v=colorsys.rgb_to_hsv(*rgb)
    rgb=colorsys.hsv_to_rgb(h,min(s,.58),max(v,.35))
    if light:rgb=tuple(.88+.12*c for c in rgb)
    return '#'+''.join(f'{round(c*255):02X}' for c in rgb)

def appearance(data):
    raw=data.get('palette');colors=[safe_color(c,'#88AAA0') for c in raw if isinstance(c,str) and re.fullmatch(r'#[0-9a-fA-F]{6}',c)] if isinstance(raw,list) else []
    return (colors[:8] or ['#D78D88','#87B7C0','#D8B07E','#9AADD1','#9FBCA1','#BE9DAF'],safe_color(data.get('background'),'#FFFFFF',True),data.get('style')=='flat')

def glyph(kind,color):
    from graphical_summary import icon
    if kind in ('protein','complex'):
        shape='<path d="M-47,-8 C-58,-31 -25,-59 -6,-39 C14,-61 55,-30 39,-10 C61,12 29,49 9,36 C-14,59 -51,29 -39,10 C-54,13 -61,0 -47,-8Z" fill="url(#protein)" stroke="#B96760" stroke-width="2"/><ellipse cx="-13" cy="-15" rx="18" ry="12" fill="white" opacity=".45"/><circle cx="22" cy="13" r="8" fill="#EAB09D"/>'
        if kind=='complex':shape='<g transform="translate(-18,-9) scale(.72)">'+shape+'</g><g transform="translate(27,18) scale(.62)">'+shape.replace('url(#protein)','#EE9C42')+'</g>'
        return shape
    if kind=='condensate':
        pieces=['<circle r="61" fill="url(#droplet)" stroke="#E99D91" stroke-width="2"/>']
        for i in range(13):
            a=i*2.4;r=12+(i%4)*10;x=math.cos(a)*r;y=math.sin(a)*r;pieces.append(f'<circle cx="{x}" cy="{y}" r="{4+i%3}" fill="'+('#F16D63' if i%2 else '#56C7D0')+'" opacity=".85"/>')
        return ''.join(pieces)
    if kind=='rna':return '<path d="M-46,24 C-55,-24 -9,-45 4,-13 C18,17 -24,36 -8,51 C18,70 47,13 43,-36" fill="none" stroke="#56B9BD" stroke-width="7" stroke-linecap="round"/><path d="M-25,-13 L-14,-7 M5,-11 L17,-9 M-8,35 L4,37 M29,10 L42,14" stroke="#DE9450" stroke-width="4"/>'
    return icon('dna' if kind=='chromatin' else kind,0,0,color).replace('#E7A86D',color).replace('#337BA0','#56C7D0').replace('#77A9BF','#EE9C42').replace('#FBF2E6','#FDE8DF')

def render(data):
    from graphical_summary import wrapped
    nodes=(data.get('nodes') or [dict(id='unknown',label='材料不足，需查原文',visual='process')])[:10]
    palette,background,flat=appearance(data)
    groups={name:[] for name in ('context','mechanism','outcome')}
    for index,node in enumerate(nodes):
        phase=node.get('phase');phase=phase if phase in groups else 'context' if index==0 else 'outcome' if index==len(nodes)-1 else 'mechanism';groups[phase].append(node)
    max_rows=max(math.ceil(len(items)/(2 if len(items)>3 else 1)) for items in groups.values());height=max(1000,350+max_rows*240)
    positions={}
    for phase,items in groups.items():
        count=len(items);cols=2 if count>3 else 1;rows=math.ceil(count/cols)
        for index,node in enumerate(items):
            x={'context':250,'mechanism':800,'outcome':1350}[phase]+((index%cols)-.5)*210 if cols==2 else {'context':250,'mechanism':800,'outcome':1350}[phase]
            y=height/2-20 if rows==1 else 240+(index//cols)*(height-490)/max(1,rows-1)
            positions[node['id']]=(x,y)
    parts=['<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1000" viewBox="0 0 1600 1000"><defs><radialGradient id="protein" cx="32%" cy="23%" r="80%"><stop offset="0" stop-color="#FFF4EA"/><stop offset=".42" stop-color="#F99584"/><stop offset="1" stop-color="#E76860"/></radialGradient><radialGradient id="droplet" cx="36%" cy="28%"><stop stop-color="#FFF6EE"/><stop offset="1" stop-color="#F4B3A9" stop-opacity=".62"/></radialGradient></defs><rect width="1600" height="1000" fill="white"/><g font-family="Arial,Microsoft YaHei"><text x="45" y="49" font-size="22" letter-spacing="1" fill="#58666F">GRAPHICAL SUMMARY</text>']
    title=data.get('title') or '论文机制概念示意图'
    for row,line in enumerate(wrapped(title,95)[:2]):parts.append(f'<text x="45" y="{91+row*34}" font-size="29" fill="#354650" font-weight="600">{escape(line)}</text>')
    # The model selects the palette and soft/flat illustration style. Only
    # validated local colors are rendered; no generated SVG/HTML is executed.
    definitions=''.join(f'<radialGradient id="chosen{i}" cx="32%" cy="23%" r="80%"><stop offset="0" stop-color="{safe_color(color,color,True)}"/><stop offset="1" stop-color="{color}"/></radialGradient>' for i,color in enumerate(palette))
    parts[0]=re.sub(r'<defs>.*?</defs>','<defs>'+definitions+'</defs>',parts[0],count=1).replace('<rect width="1600" height="1000" fill="white"','<rect width="1600" height="1000" fill="'+background+'"')
    visuals={node.get('visual') for node in nodes}
    if visuals&{'cell','nucleus'}:parts.append('<ellipse cx="800" cy="470" rx="660" ry="325" fill="#EDE8EF" fill-opacity=".7" stroke="#A5AEB4" stroke-width="3"/><ellipse cx="800" cy="470" rx="300" ry="275" fill="#E6EDF2" stroke="#C2CCD2" stroke-width="2"/>')
    else:parts.append('<ellipse cx="800" cy="475" rx="590" ry="315" fill="#E6EDF2" fill-opacity=".7"/><ellipse cx="900" cy="480" rx="285" ry="250" fill="#FDE8DF" fill-opacity=".6"/>')
    edges=[edge for edge in data.get('edges',[]) if edge.get('from') in positions and edge.get('to') in positions and edge['from']!=edge['to']][:12]
    for edge in edges:
        x1,y1=positions[edge['from']];x2,y2=positions[edge['to']];distance=math.hypot(x2-x1,y2-y1)
        if distance<150:continue
        ux,uy=(x2-x1)/distance,(y2-y1)/distance;sx,sy=x1+ux*78,y1+uy*78;ex,ey=x2-ux*80,y2-uy*80;middle=(sx+ex)/2
        label=str(edge.get('label') or '');hypothesis=bool(re.search(r'假设|hypothe|propos',label,re.I));inhibit=bool(re.search(r'抑制|inhibit|block',label,re.I));dash=' stroke-dasharray="7 6"' if hypothesis else '';color='#C37570' if inhibit else '#7398A5'
        parts.append(f'<path d="M{sx},{sy} C{middle},{sy} {middle},{ey} {ex},{ey}" fill="none" stroke="{color}" stroke-width="3.5"{dash}/>')
        if inhibit:parts.append(f'<path d="M{ex-uy*12},{ey+ux*12} L{ex+uy*12},{ey-ux*12}" stroke="{color}" stroke-width="4"/>')
        else:parts.append(f'<polygon points="{ex},{ey} {ex-ux*16-uy*7},{ey-uy*16+ux*7} {ex-ux*16+uy*7},{ey-uy*16-ux*7}" fill="{color}"/>')
        if hypothesis:parts.append(f'<text x="{middle}" y="{(sy+ey)/2-13}" text-anchor="middle" font-size="17" fill="#8E7771">假设</text>')
    for index in range(1,len(parts)):
        for old,new in [('#EDE8EF',safe_color(palette[0],palette[0],True)),('#E6EDF2',safe_color(palette[1%len(palette)],palette[0],True)),('#FDE8DF',safe_color(palette[-1],palette[-1],True))]:parts[index]=parts[index].replace(old,new)
    for index,node in enumerate(nodes):
        x,y=positions[node['id']];color=palette[index%len(palette)];art=glyph(node.get('visual','process'),color)
        art=art.replace('url(#protein)',color if flat else 'url(#chosen'+str(index%len(palette))+')').replace('url(#droplet)',safe_color(color,color,True))
        for old,new in [('#56C7D0',palette[(index+1)%len(palette)]),('#EE9C42',palette[(index+2)%len(palette)]),('#F16D63',color),('#56B9BD',color),('#DE9450',palette[(index+1)%len(palette)]),('#B96760','#686D71'),('#E99D91',color),('#EAB09D',safe_color(color,color,True))]:art=art.replace(old,new)
        parts.append(f'<g transform="translate({x},{y}) scale(1.2)">'+art+'</g>')
        lines=wrapped(str(node.get('label') or ''),22)[:2]
        for row,line in enumerate(lines):parts.append(f'<text x="{x}" y="{y+100+row*27}" text-anchor="middle" font-size="24" font-weight="600" fill="#354650">{escape(line)}</text>')
    model=str(data.get('generation_model') or '')
    parts.append(f'<text x="45" y="{height-52}" font-size="19" fill="#788891">AI-generated conceptual illustration · 非论文原图 · 仅呈现原文支持的机制与概念</text>')
    if model:parts.append(f'<text x="45" y="{height-21}" font-size="17" fill="#788891">'+escape('本次生成模型：'+model[:120])+'</text>')
    parts.append('</g></svg>');return ''.join(parts).replace('height="1000"','height="'+str(height)+'"',1).replace('viewBox="0 0 1600 1000"','viewBox="0 0 1600 '+str(height)+'"',1).replace('width="1600" height="1000" fill="white"','width="1600" height="'+str(height)+'" fill="white"',1)
