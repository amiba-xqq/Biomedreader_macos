"""Prepare project files for the selected model without exposing local paths."""
import base64
import io
from pathlib import Path

from ai_cancel import check_cancelled

REFERENCE_FILES = {'.pdf','.docx','.md','.log','.json','.rtf'}


def material_context(store,identifier,provider,visual,original,previous=None):
    from figure_data import IMAGES,TABLES,sheet_rows
    from figure_ui import image_reference,svg_reference
    from PIL import Image
    entries,images,local = [],[],[]
    documents = [('preview.pdf',original['pdf'])]
    if previous and 'pdf' in previous and previous['pdf']!=original['pdf']:
        documents.append(('previous-ai-figure.pdf',previous['pdf']))
    if visual:
        svg_previews = [original['svg']]
        if previous and 'svg' in previous and previous['svg']!=original['svg']:
            svg_previews.append(previous['svg'])
        for svg in svg_previews:
            preview = svg_reference(svg,None)
            if preview:
                images.extend(preview['images'])
    for index,file in enumerate(store.files(identifier),1):
        check_cancelled()
        name = file['name']
        suffix = Path(name).suffix.lower()
        raw = store.file(identifier,file['id'])
        entry = dict(id='file:'+str(file['id']),name=name,bytes=len(raw))
        if provider=='codex':
            local.append((f'material-{index}{suffix}',raw))
            entry['attachment'] = local[-1][0]
        elif suffix in IMAGES and visual:
            with Image.open(io.BytesIO(raw)) as image:
                images.extend(image_reference(image)['images'])
            entry['image_index'] = len(images)
        elif suffix=='.pdf' and provider=='openai':
            documents.append((f'material-{index}.pdf',raw))
            entry['attachment'] = documents[-1][0]
        elif suffix in TABLES and suffix!='.txt':
            if suffix=='.xlsx':
                from openpyxl import load_workbook
                workbook = load_workbook(io.BytesIO(raw),read_only=True,data_only=True)
                try:
                    entry['sheets'] = {sheet.title:list(sheet.values) for sheet in workbook.worksheets}
                finally:
                    workbook.close()
            elif suffix=='.xls':
                import xlrd
                workbook = xlrd.open_workbook(file_contents=raw)
                entry['sheets'] = {sheet.name:[sheet.row_values(row) for row in range(sheet.nrows)] for sheet in workbook.sheets()}
            else:
                entry['rows'] = sheet_rows(name,raw)
        elif suffix in ('.pdf','.docx'):
            from protocol_workflow import local_material
            entry['text'] = local_material(Path(file['path']))
        elif suffix not in IMAGES:
            for encoding in ('utf-8-sig','utf-16','gb18030'):
                try:
                    decoded = raw.decode(encoding)
                    if '\x00' not in decoded:
                        entry['text'] = decoded
                        break
                except UnicodeError:
                    pass
            if 'text' not in entry:
                raise ValueError(f'无法读取素材「{name}」的文本编码。')
        else:
            entry['note'] = '当前模型为文字输入，参照图件的对象坐标与素材信息。'
        entries.append(entry)
    reference = None
    if provider=='codex':
        reference = dict(mode='project',images=images,local_files=[*documents,*local])
    elif provider=='openai':
        files = [(name,'data:application/pdf;base64,'+base64.b64encode(raw).decode('ascii')) for name,raw in documents]
        reference = dict(mode='project',files=files,images=images)
    elif images:
        reference = dict(mode='images',images=images)
    return entries,reference
