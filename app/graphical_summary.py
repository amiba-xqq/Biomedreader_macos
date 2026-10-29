"""Cell-inspired graphical-summary grammar, rendered as safe native vector shapes.

Icons are symbolic research illustrations, never inferred protein structures or data.
Only validated model-provided relationships become arrows.
"""
import math
from html import escape

def icon(kind,x,y,color):
    prefix=f'<g transform="translate({x},{y})" stroke-linecap="round" stroke-linejoin="round">'
    if kind in ('cell','nucleus'):
        drawing='<ellipse rx="49" ry="40" fill="#F5FAFC" stroke="#7198AC" stroke-width="3"/><circle r="23" cx="4" cy="-2" fill="#DDE7F4" stroke="#8C8DB3" stroke-width="2"/><path d="M-7,-14 C20,-7 -19,7 11,15 M13,-15 C-14,-2 23,3 -8,16" fill="none" stroke="#7286B6" stroke-width="2.5"/>'
    elif kind=='dna':
        parts=[]
        for offset in range(0,81,5):
            yy=offset-40;xx=21*math.sin(offset*math.pi/40)
            if offset%10==0:parts.append(f'<line x1="{-xx}" y1="{yy}" x2="{xx}" y2="{yy}" stroke="#A6BFD2" stroke-width="2"/>')
        left=' '.join(f'{21*math.sin(v*math.pi/40)},{v-40}' for v in range(81));right=' '.join(f'{-21*math.sin(v*math.pi/40)},{v-40}' for v in range(81))
        drawing=''.join(parts)+f'<polyline points="{left}" fill="none" stroke="#337BA0" stroke-width="4"/><polyline points="{right}" fill="none" stroke="#77A9BF" stroke-width="4"/>'
    elif kind=='protein':
        drawing='<path d="M-39,-7 C-47,-24 -19,-47 -5,-32 C12,-49 44,-22 31,-8 C52,7 26,39 9,29 C-8,48 -39,26 -30,10 C-43,12 -49,0 -39,-7Z" fill="#E7A86D" stroke="#B97941" stroke-width="2.5"/><circle cx="-13" cy="-8" r="8" fill="#F6D5B5"/><circle cx="17" cy="7" r="6" fill="#F6D5B5"/>'
    elif kind=='signal':
        drawing='<circle r="43" fill="#FBF2E6" stroke="#D6AB72" stroke-width="2"/><path d="M6,-32 L-20,5 L-2,5 L-11,32 L23,-7 L5,-7Z" fill="#CC8A43"/>'
    elif kind=='outcome':
        drawing='<circle r="42" fill="#ECF4F0" stroke="#76A38E" stroke-width="2.5"/><path d="M-20,1 L-6,15 L23,-17" fill="none" stroke="#4C8A72" stroke-width="6"/>'
    else:
        drawing=f'<circle r="41" fill="#EAF1F6" stroke="{color}" stroke-width="2.5"/><path d="M-20,-11 C-2,-33 31,-12 22,10" fill="none" stroke="{color}" stroke-width="4"/><polygon points="15,6 25,18 30,3" fill="{color}"/><circle cx="-15" cy="13" r="7" fill="{color}"/>'
    return prefix+drawing+'</g>'

def _legacy_graphical_svg(data):
    nodes=(data.get('nodes') or [dict(id='unknown',label='材料不足，需查原文')])[:6]
    groups={'context':[],'mechanism':[],'outcome':[]}
    for index,node in enumerate(nodes):
        phase=node.get('phase')
        if phase not in groups:phase='context' if index==0 else 'outcome' if index==len(nodes)-1 else 'mechanism'
        groups[phase].append(node)
    phases=[('context','RESEARCH CONTEXT','#F1F6FA','#346C8A'),('mechanism','KEY MECHANISM','#F0F7F5','#467E79'),('outcome','BIOLOGICAL OUTCOME','#FBF4EB','#AC7542')]
    panels=[];positions={};colors={}
    for col,(phase,title,background,color) in enumerate(phases):
        x=26+col*398;panels.append(f'<rect x="{x}" y="70" width="352" height="422" rx="22" fill="{background}"/><text x="{x+176}" y="111" text-anchor="middle" font-size="20" font-weight="700" fill="{color}">{title}</text>')
        entries=groups[phase];slot=318/max(1,len(entries))
        for row,node in enumerate(entries):positions[node['id']]=(x+176,140+slot*(row+.5));colors[node['id']]=color
        if not entries:panels.append(f'<text x="{x+176}" y="300" text-anchor="middle" font-size="22" fill="#8E9FA6">Not specified</text>')
    arrows=[]
    for edge in data.get('edges',[]):
        if edge.get('from') not in positions or edge.get('to') not in positions:continue
        x1,y1=positions[edge['from']];x2,y2=positions[edge['to']];angle=math.atan2(y2-y1,x2-x1)
        if abs(x2-x1)<1:
            route=x1+85;tx=x2+54;ty=y2
            label=escape(edge.get('label','')[:20]);middle=(y1+y2)/2
            arrows.append(f'<polyline points="{x1+52},{y1} {route},{y1} {route},{y2} {tx},{ty}" fill="none" stroke="#647F8C" stroke-width="3"/><polygon points="{tx},{ty} {tx+12},{ty-6} {tx+12},{ty+6}" fill="#647F8C"/><rect x="{route-70}" y="{middle-24}" width="140" height="30" rx="8" fill="white"/><text x="{route}" y="{middle-3}" text-anchor="middle" font-size="20" fill="#46616E">{label}</text>')
            continue
        trim=60;sx=x1+math.cos(angle)*trim;sy=y1+math.sin(angle)*trim;tx=x2-math.cos(angle)*trim;ty=y2-math.sin(angle)*trim
        a=(tx-13*math.cos(angle-.4),ty-13*math.sin(angle-.4));b=(tx-13*math.cos(angle+.4),ty-13*math.sin(angle+.4))
        arrows.append(f'<line x1="{sx}" y1="{sy}" x2="{tx}" y2="{ty}" stroke="#647F8C" stroke-width="3"/><polygon points="{tx},{ty} {a[0]},{a[1]} {b[0]},{b[1]}" fill="#647F8C"/>')
        label=escape(edge.get('label','')[:20]);lx=(sx+tx)/2+(80 if abs(x2-x1)<1 else 0);ly=(sy+ty)/2-17
        arrows.append(f'<text x="{lx}" y="{ly}" text-anchor="middle" font-size="20" fill="#46616E">{label}</text>')
    symbols=[]
    for node in nodes:
        x,y=positions[node['id']];symbols.append(icon(node.get('visual','process'),x,y,colors[node['id']]))
        label=node['label'];lines=[label[i:i+17] for i in range(0,min(51,len(label)),17)]
        for row,line in enumerate(lines):symbols.append(f'<text x="{x}" y="{y+66+row*27}" text-anchor="middle" font-size="25" font-weight="600" fill="#243C48">{escape(line)}</text>')
    return '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="560" viewBox="0 0 1200 560"><rect width="1200" height="560" fill="white"/><g font-family="Microsoft YaHei UI,Arial"><text x="28" y="37" font-size="25" font-weight="700" fill="#263F4B">GRAPHICAL SUMMARY</text>'+''.join(panels+arrows+symbols)+'<text x="28" y="536" font-size="17" fill="#6D818C">AI-generated conceptual illustration / 非论文原图，请核对原文；图标仅表示概念，不代表实测结构。</text></g></svg>'


def wrapped(value,width):
    """Wrap CJK and Latin labels by display width, with no clipped text."""
    import unicodedata
    lines=[];line='';units=0
    for char in str(value or ''):
        size=2 if unicodedata.east_asian_width(char) in ('W','F') else 1
        if char=='\n' or units+size>width:
            if line:lines.append(line)
            line='';units=0
            if char=='\n':continue
        line+=char;units+=size
    if line:lines.append(line)
    return lines


def graphical_svg(data):
    """Evidence-rich three-phase diagram; all geometry and SVG are app generated."""
    nodes=(data.get('nodes') or [dict(id='unknown',label='材料不足，需查原文')])[:12];groups={p:[] for p in ('context','mechanism','outcome')}
    for i,node in enumerate(nodes):
        phase=node.get('phase');phase=phase if phase in groups else 'context' if i==0 else 'outcome' if i==len(nodes)-1 else 'mechanism';groups[phase].append(node)
    # Text-dependent cell height keeps detailed annotations legible and unclipped.
    cols={p:2 if len(rows)>4 else 1 for p,rows in groups.items()}
    row_height=max(174,max((len(wrapped(n.get('label',''),14 if cols[p]==2 else 30))*25+len(wrapped(n.get('detail',''),21 if cols[p]==2 else 35))*22+(92 if cols[p]==2 else 56) for p,rows in groups.items() for n in rows),default=174))
    row_count=max(2,max(math.ceil(len(groups[p])/cols[p]) for p in groups));panel_height=84+row_count*row_height
    edges=[edge for edge in data.get('edges',[]) if edge.get('from') in {n['id'] for n in nodes} and edge.get('to') in {n['id'] for n in nodes}][:20]
    legend_rows=[]
    for i,edge in enumerate(edges,1):
        names={n['id']:n['label'] for n in nodes};legend_rows.extend(wrapped(f"[{i}] {names[edge['from']]} → {names[edge['to']]}：{edge.get('label') or '原文所述关系'}",100))
    height=panel_height+210+len(legend_rows)*25;parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="{height}" viewBox="0 0 1600 {height}"><rect width="1600" height="{height}" fill="white"/><defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10Z" fill="#547C89"/></marker></defs><g font-family="Microsoft YaHei UI,Arial"><text x="30" y="33" font-size="18" fill="#50717F" letter-spacing="2">BIOMED READER / GRAPHICAL SUMMARY</text>']
    title=data.get('title') or '研究背景 · 分子机制 · 生物学结果'
    for index,line in enumerate(wrapped(title,100)):parts.append(f'<text x="30" y="{69+index*31}" font-size="27" font-weight="700" fill="#183B49">{escape(line)}</text>')
    # Reserve title height separately if a long title occupies multiple lines.
    top=90+max(0,len(wrapped(title,100))-1)*31;height+=top-90
    parts[0]=parts[0].replace(f'height="{height-(top-90)}"',f'height="{height}"').replace(f'1600 {height-(top-90)}',f'1600 {height}')
    phases=[('context','01  RESEARCH CONTEXT','#EEF4F8','#397896'),('mechanism','02  MOLECULAR MECHANISM','#EDF5F1','#438274'),('outcome','03  BIOLOGICAL OUTCOME','#FCF2E8','#AF7946')];positions={};cards=[]
    for col,(phase,label,bg,color) in enumerate(phases):
        x=26+col*524;parts.append(f'<rect x="{x}" y="{top}" width="500" height="{panel_height}" rx="22" fill="{bg}"/><text x="{x+24}" y="{top+39}" font-size="20" font-weight="700" fill="{color}">{label}</text>')
        count=cols[phase];cw=(456-(count-1)*14)/count
        for i,node in enumerate(groups[phase]):
            nx=x+22+(i%count)*(cw+14);ny=top+62+(i//count)*row_height;ch=row_height-16;positions[node['id']]=(nx,ny,cw,ch)
            cards.append(f'<rect x="{nx}" y="{ny}" width="{cw}" height="{ch}" rx="14" fill="white" stroke="{color}" stroke-opacity=".28"/>')
            if count==1:
                cards.append(f'<g transform="translate({nx+62},{ny+65}) scale(.72)">'+icon(node.get('visual','process'),0,0,color)+'</g>');tx=nx+116;tw=30
            else:
                cards.append(f'<g transform="translate({nx+30},{ny+31}) scale(.40)">'+icon(node.get('visual','process'),0,0,color)+'</g>');tx=nx+58;tw=14
            yy=ny+30
            for line in wrapped(node.get('label',''),tw):cards.append(f'<text x="{tx}" y="{yy}" font-size="21" font-weight="700" fill="#254651">{escape(line)}</text>');yy+=25
            if count==2:tx=nx+16;yy=max(yy+8,ny+77)
            else:yy+=10
            for line in wrapped(node.get('detail',''),35 if count==1 else 21):cards.append(f'<text x="{tx}" y="{yy}" font-size="18" fill="#617781">{escape(line)}</text>');yy+=22
        if not groups[phase]:parts.append(f'<text x="{x+250}" y="{top+panel_height/2}" text-anchor="middle" font-size="22" fill="#84969D">材料未提供 / Not specified</text>')
    badges=[]
    for i,edge in enumerate(edges,1):
        a=positions[edge['from']];b=positions[edge['to']]
        if abs(a[0]-b[0])>300:
            right=b[0]>a[0];sx=a[0]+(a[2] if right else 0);sy=a[1]+a[3]/2;tx=b[0]+(0 if right else b[2]);ty=b[1]+b[3]/2;middle=(sx+tx)/2
            parts.append(f'<path d="M{sx},{sy} C{middle},{sy} {middle},{ty} {tx},{ty}" fill="none" stroke="#547C89" stroke-width="2.5" stroke-opacity=".8" marker-end="url(#arrow)"/>')
            bx=sx+(12 if right else -12);by=sy
        else:
            sx=a[0]+a[2]-12;sy=a[1]+a[3];tx=b[0]+b[2]-12;ty=b[1];parts.append(f'<path d="M{sx},{sy} L{tx},{ty}" fill="none" stroke="#547C89" stroke-width="2.5" marker-end="url(#arrow)"/>');bx=sx;by=sy+8
        badges.append(f'<circle cx="{bx}" cy="{by}" r="8" fill="white" stroke="#547C89"/><text x="{bx}" y="{by+4}" text-anchor="middle" font-size="11" font-weight="700" fill="#345563">{i}</text>')
    parts.extend(cards+badges);yy=top+panel_height+34
    for line in legend_rows:parts.append(f'<text x="35" y="{yy}" font-size="18" fill="#496674">{escape(line)}</text>');yy+=25
    parts.append(f'<text x="35" y="{height-30}" font-size="17" fill="#7C8E95">AI-generated conceptual illustration / 非论文原图；仅呈现材料支持的关系，虚线及“假设”需实验验证。</text></g></svg>')
    return ''.join(parts)


def graphical_svg(data):
    from mechanism_scene import render
    return render(data)
