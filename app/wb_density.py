"""Background-corrected band quantification for explicitly selected WB/gel ROIs."""
import hashlib


def image_signature(image):
    return f'{image.mode}:{image.width}x{image.height}:'+hashlib.sha256(image.tobytes()).hexdigest()


def background_regions(rect,height):
    rect = roi_bounds(rect)
    x,y,w,h = rect
    step = max(1,h//10)
    depth = max(2,h//4)
    before = max(0,y-step-depth)
    end_before = max(0,y-step)
    start_after = min(height,y+h+step)
    after = min(height,start_after+depth)
    return [(x,top,w,bottom-top) for top,bottom in ((before,end_before),(start_after,after))
            if bottom>top]


def roi_bounds(roi):
    return roi['rect'] if isinstance(roi,dict) else roi


def empty_band(x,right,height):
    width = max(1,min(right-x,round((right-x)*.20)))
    depth = max(1,min(height,round(height*.14)))
    return dict(rect=[x+max(0,(right-x-width)//2),(height-depth)//2,width,depth],empty=True)


def automatic_bands(image,count,polarity):
    import numpy as np
    from scipy.ndimage import find_objects,label
    data = np.asarray(image.convert('L'))
    h,w = data.shape
    if count>w:
        raise ValueError('泳道数超过图像宽度，请扩大选区。')
    result = []
    for index in range(count):
        x,xx = round(index*w/count),round((index+1)*w/count)
        lane = data[:,x:xx]
        if polarity=='dark':
            lane = 255-lane
        hist = np.bincount(lane.ravel(),minlength=256)
        left = hist.cumsum()
        total = left[-1]
        first = (hist*np.arange(256)).cumsum()
        valid = (left>0)&(left<total)
        scores = np.zeros(256)
        counts = left[valid].astype(float)
        scores[valid] = (first[-1]*counts-first[valid]*float(total))**2/(counts*(total-counts))
        if not scores.any():
            result.append(empty_band(x,xx,h))
            continue
        threshold = int(scores.argmax())
        components,_ = label(lane>threshold)
        areas = np.bincount(components.ravel())
        areas[0] = 0
        component = int(areas.argmax())
        yy,local_x = find_objects(components)[component-1]
        rect = [x+local_x.start,yy.start,local_x.stop-local_x.start,yy.stop-yy.start]
        mask = components[yy,local_x]==component
        seed = np.argwhere(mask)[0].tolist()
        edges = []
        padded = np.pad(mask,1)
        for offset_y,offset_x,a,b,c,d in ((0,1,0,0,1,0),(1,0,0,0,0,1),
                                           (2,1,0,1,1,1),(1,2,1,0,1,1)):
            outside = padded[offset_y:offset_y+mask.shape[0],offset_x:offset_x+mask.shape[1]]
            edges.extend([rect[0]+int(ix)+a,rect[1]+int(iy)+b,rect[0]+int(ix)+c,rect[1]+int(iy)+d]
                         for iy,ix in np.argwhere(mask & ~outside))
        result.append(dict(rect=rect,threshold=threshold,seed=seed,outline=edges)
                      if min(rect[2:])>=2 else empty_band(x,xx,h))
    return result


def measure_bands(image,rectangles,polarity,control):
    import numpy as np
    pixels = np.asarray(image.convert('L'),dtype=np.float64)
    height,width = pixels.shape
    intensities,backgrounds = [],[]
    for roi in rectangles:
        rect = roi_bounds(roi)
        if not rect or len(rect)!=4:
            raise ValueError('请为每个泳道圈选一个目标条带。')
        x,y,w,h = rect
        empty = isinstance(roi,dict) and roi.get('empty')
        if min(x,y)<0 or min(w,h)<(1 if empty else 2) or x+w>width or y+h>height:
            raise ValueError('条带选区须在原图范围内，并包含有效像素。')
        if empty:
            intensities.append(0.)
            backgrounds.append([])
            continue
        regions = background_regions(rect,height)
        if not regions:
            raise ValueError('条带上下没有留出局部背景。请扩大原选区或缩小条带框。')
        background = sum(pixels[yy:yy+hh,xx:xx+ww].sum() for xx,yy,ww,hh in regions)
        background /= sum(ww*hh for _,_,ww,hh in regions)
        sample = pixels[y:y+h,x:x+w]
        if isinstance(roi,dict) and roi.get('points'):
            from PIL import Image,ImageDraw
            mask = Image.new('1',(w,h))
            ImageDraw.Draw(mask).polygon([(xx-x,yy-y) for xx,yy in roi['points']],fill=1)
            sample = sample[np.asarray(mask)]
        elif isinstance(roi,dict) and 'threshold' in roi:
            mask = (sample if polarity=='light' else 255-sample)>roi['threshold']
            if 'seed' in roi:
                from scipy.ndimage import label
                components,_ = label(mask)
                selected = components[tuple(roi['seed'])]
                mask = (components==selected) & mask
            sample = sample[mask]
        if not sample.size:
            raise ValueError('目标条带选区为空。')
        corrected = (float(sample.sum())-background*sample.size)*(1 if polarity=='light' else -1)
        intensities.append(max(0.,corrected))
        backgrounds.append(regions)
    if not 0<=control<len(rectangles) or intensities[control]<=0:
        raise ValueError('对照泳道的背景校正信号为零，无法归一化。请检查对照、条带方向和圈选区域。')
    return [value/intensities[control] for value in intensities],backgrounds


def control_lane(panel_id,labels,settings):
    controls = settings.get('controls',{})
    value = controls.get(panel_id,settings.get('default_control',settings.get('control')))
    if type(value) is int and 0<=value<len(labels):
        return value
    if isinstance(value,str) and value in labels:
        return labels.index(value)
    return None


def panel_density(panel,image,labels,settings):
    entry = settings.get('panels',{}).get(panel.get('uid'))
    if not entry or entry['signature']!=image_signature(image) or entry['labels']!=labels:
        raise ValueError(f"「{panel['label']}」的条带、素材或泳道标签有变化，请在「相对灰度」中重新圈选。")
    rectangles = entry['rectangles']
    if len(rectangles)!=len(labels):
        raise ValueError(f"「{panel['label']}」尚未为每个泳道选取条带。")
    try:
        control = control_lane(panel.get('uid'),labels,settings)
        if control is None:
            raise ValueError('请选择对照泳道。')
        ratios,backgrounds = measure_bands(image,rectangles,settings['polarity'],control)
    except ValueError as error:
        raise ValueError(f"「{panel['label']}」：{error}") from None
    return ratios,rectangles,backgrounds
