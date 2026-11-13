"""Per-file sample and target aliases for qPCR projects."""
from figure_status import FigureStatus
import csv
import io
import json
from collections import defaultdict
from pathlib import Path

from PySide6.QtCore import QEvent,QMimeData,QSignalBlocker,QTimer,Qt,Signal
from PySide6.QtGui import QBrush,QColor,QDrag,QKeySequence
from PySide6.QtWidgets import (
    QApplication,QDialog,QDialogButtonBox,QFormLayout,QFrame,QGridLayout,QHeaderView,QHBoxLayout,
    QLabel,QLineEdit,QMenu,QPushButton,QScrollArea,QStyledItemDelegate,QTabWidget,QTableWidget,
    QTableWidgetItem,QVBoxLayout,QWidget,
)

from figure_data import (qpcr_keep,qpcr_mapping_entry,qpcr_mapping_issues,qpcr_name,qpcr_required,
                         qpcr_saved_enrichment_pairs,qpcr_scope,suggested_enrichment_pairs)

KINDS = ('samples','genes')
REQUIRED = ('control','reference')
PAIR_COLORS = {'control':('#E2F0F4','#174853','#7DB5C1'),
               'ip':('#FFE9D5','#783D16','#E7A267'),
               'unpaired':('#F5F1EB','#4D4338','#D8CCBE')}


def clipboard_names(text):
    if not text:
        return []
    separator = '\t' if '\t' in text or ',' not in text else ','
    rows = list(csv.reader(io.StringIO(text,newline=''),delimiter=separator))
    width = max(map(len,rows),default=0)
    if not width:
        return [''] if rows else []
    return [row[column].strip() if column<len(row) else '' for column in range(width) for row in rows]


class MappingTable(QTableWidget):
    aliases_pasted = Signal(object)
    clipboard_error = Signal(str)

    def __init__(self,rows):
        super().__init__(rows,3)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.clipboard_menu)

    def keyPressEvent(self,event):
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_names()
        elif event.matches(QKeySequence.StandardKey.Paste):
            self.paste_names()
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def clipboard_menu(self,point):
        index = self.indexAt(point)
        if index.isValid() and not self.selectionModel().isSelected(index):
            self.setCurrentCell(index.row(),index.column())
        menu = QMenu(self)
        menu.addAction('复制映射名称',self.copy_names)
        menu.addAction('粘贴到映射名称',self.paste_names)
        menu.exec(self.viewport().mapToGlobal(point))

    def copy_names(self):
        rows = sorted({item.row() for item in self.selectedItems()})
        if rows:
            text = io.StringIO(newline='')
            writer = csv.writer(text,delimiter='\t',lineterminator='\r\n')
            for row in rows:
                writer.writerow([self.item(row,2).text()])
            mime = QMimeData()
            mime.setText(text.getvalue())
            mime.setData('application/x-biomed-mapping-names',
                         json.dumps([self.item(row,2).text() for row in rows],ensure_ascii=False).encode())
            QApplication.clipboard().setMimeData(mime)

    def paste_names(self,values=None,start=None):
        if values is None:
            values = self.paste_values()
        if not values:
            return
        if start is None:
            start = min((item.row() for item in self.selectedItems()),default=self.currentRow())
        if start<0 or start+len(values)>self.rowCount():
            self.clipboard_error.emit('粘贴范围超出映射表。请选择有足够剩余行的起始单元格。')
            return
        if any(len(value)>120 or '\n' in value or '\r' in value for value in values):
            self.clipboard_error.emit('每个映射名称须为一行，最多 120 个字符。')
            return
        if len(values)==1:
            selected = sorted({item.row() for item in self.selectedItems() if item.column()==2})
            if selected and selected[0]==start and selected==list(range(start,selected[-1]+1)):
                values *= len(selected)
        with QSignalBlocker(self):
            for row,value in enumerate(values,start):
                self.item(row,2).setText(value)
        self.aliases_pasted.emit([self.item(row,2) for row in range(start,start+len(values))])

    @staticmethod
    def paste_values():
        mime = QApplication.clipboard().mimeData()
        if mime is None:
            return []
        format_name = 'application/x-biomed-mapping-names'
        if mime.hasFormat(format_name):
            try:
                values = json.loads(bytes(mime.data(format_name)))
                if isinstance(values,list) and all(isinstance(value,str) for value in values):
                    return values
            except (UnicodeError,ValueError):
                pass
        return clipboard_names(mime.text())


class MappingNameDelegate(QStyledItemDelegate):
    def createEditor(self,parent,option,index):
        editor = QLineEdit(parent)
        editor.setFont(option.font)
        editor.setMaxLength(120)
        editor.setStyleSheet('QLineEdit {padding:1px 7px;min-height:0px;}')
        return editor

    def updateEditorGeometry(self,editor,option,index):
        editor.setGeometry(option.rect.adjusted(4,3,-4,-3))

    def eventFilter(self,editor,event):
        if isinstance(editor,QLineEdit) and event.type()==QEvent.Type.KeyPress and event.matches(QKeySequence.StandardKey.Paste):
            table = self.parent()
            values = table.paste_values()
            if len(values)>1:
                row = table.currentRow()
                self.closeEditor.emit(editor,QStyledItemDelegate.EndEditHint.RevertModelCache)
                table.paste_names(values,row)
                return True
        return super().eventFilter(editor,event)


class MappingDialog(QDialog):
    def __init__(self,parent,files,options):
        super().__init__(parent)
        self.setWindowTitle('qPCR 样本与基因映射')
        self.resize(790,580)
        self.files,self.result_settings = [tuple(record[:3]) for record in files],None
        self.options = options
        self.enrichment = options.get('analysis')=='enrichment'
        self.tables,self.required = {},{}
        outer = QVBoxLayout(self)
        instructions = QLabel('双击编辑映射名称，留空保留原名。可用 Ctrl+C／Ctrl+V 或右键批量复制和粘贴。'
                              '多列内容按列转为一列。首个素材的映射会同步到后续同名条目，后续页可单独调整。')
        instructions.setWordWrap(True)
        outer.addWidget(instructions)
        form = QFormLayout()
        for kind,key,title in zip(KINDS,REQUIRED,('对照组最终名称','内参基因最终名称')):
            entry = QLineEdit(options.get(key,'') if self.enrichment else qpcr_required(options,kind))
            self.required[key] = entry
            if not self.enrichment:
                form.addRow(title,entry)
        outer.addLayout(form)
        self.files_tabs = QTabWidget()
        outer.addWidget(self.files_tabs,1)
        self.tab_by_file = {}
        counts = defaultdict(int)
        for scope_id,_,_ in self.files:
            counts[qpcr_scope(scope_id)[0]] += 1
        self.first_attachment = qpcr_scope(self.files[0][0])[0] if self.files else None
        self.alias_rows = defaultdict(list)
        self.overrides = set()
        file_tabs = {}
        for file_id,filename,dimensions in self.files:
            tabs = QTabWidget()
            attachment,_,repeat = qpcr_scope(file_id)
            parent = self.files_tabs
            caption = Path(filename).stem
            if counts[attachment]>1:
                if attachment not in file_tabs:
                    parent = QTabWidget()
                    index = self.files_tabs.addTab(parent,caption)
                    self.files_tabs.setTabToolTip(index,filename)
                    file_tabs[attachment] = parent
                parent = file_tabs[attachment]
                caption = repeat or f'重复 {parent.count()+1}'
                if any(parent.tabText(i)==caption for i in range(parent.count())):
                    caption = f'{caption} · {parent.count()+1}'
            index = parent.addTab(tabs,caption)
            self.tab_by_file[file_id] = parent,index,filename,tabs
            for kind,caption,names in zip(KINDS,('样本','基因'),dimensions):
                table = MappingTable(len(names))
                table.setHorizontalHeaderLabels(['保留','原始名称','映射名称'])
                table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents)
                table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
                table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch)
                table.verticalHeader().setDefaultSectionSize(max(38,table.fontMetrics().lineSpacing()+22))
                table.verticalHeader().hide()
                table.setItemDelegateForColumn(2,MappingNameDelegate(table))
                for row,name in enumerate(names):
                    config = qpcr_mapping_entry(options,kind,name,file_id)
                    include = QTableWidgetItem()
                    include.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsUserCheckable)
                    include.setCheckState(Qt.CheckState.Checked if config.get('keep',True) else Qt.CheckState.Unchecked)
                    original = QTableWidgetItem(name)
                    original.setFlags(original.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    table.setItem(row,0,include)
                    table.setItem(row,1,original)
                    matching = self.alias_rows[kind,name]
                    first = next((item.text() for source,_,item in matching if source==self.first_attachment),None)
                    own = config.get('name','')
                    follows = attachment!=self.first_attachment and first is not None
                    alias = QTableWidgetItem(own if config or not follows else first)
                    if follows and config and own!=first:
                        self.overrides.add((id(table),row))
                    table.setItem(row,2,alias)
                    self.alias_rows[kind,name].append((attachment,table,alias))
                self.tables[file_id,kind] = table
                tabs.addTab(table,caption)
                table.itemChanged.connect(lambda item,source=attachment,dimension=kind:
                                          self.mapping_changed(source,dimension,[item]))
                table.aliases_pasted.connect(lambda items,source=attachment,dimension=kind:
                                             self.mapping_changed(source,dimension,items))
        self.message = FigureStatus()
        for table in self.tables.values():
            table.clipboard_error.connect(self.message.error)
        self.message.setWordWrap(True)
        outer.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.finish)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        for entry in self.required.values():
            entry.textChanged.connect(self.validate)
        self.validate()

    def mapping_changed(self,attachment,kind,items):
        for item in items:
            if item.column()!=2:
                continue
            table = item.tableWidget()
            name = table.item(item.row(),1).text()
            if attachment==self.first_attachment:
                for target,target_table,alias in self.alias_rows[kind,name]:
                    if target!=attachment and (id(target_table),alias.row()) not in self.overrides:
                        with QSignalBlocker(target_table):
                            alias.setText(item.text())
            else:
                self.overrides.add((id(table),item.row()))
        self.validate()

    def settings(self):
        mapping = {file_id:{} for file_id,_,_ in self.files}
        result = {key:entry.text().strip() for key,entry in self.required.items()}
        for (file_id,kind),table in self.tables.items():
            mapping[file_id][kind] = {
                table.item(row,1).text():dict(name=table.item(row,2).text().strip(),
                    keep=table.item(row,0).checkState()==Qt.CheckState.Checked)
                for row in range(table.rowCount())
            }
        return dict(**result,file_mappings=mapping,name_mapping={},
                    analysis='enrichment' if self.enrichment else 'expression')

    def validate(self,*_):
        settings = self.settings()
        issues = qpcr_mapping_issues(self.files,{**self.options,**settings},
                                     strict_samples=not bool(self.options.get('qpcr_sample_repeats')))
        notes = []
        for file_id,(parent,index,filename,tabs) in self.tab_by_file.items():
            file_notes = []
            for tab,kind in enumerate(KINDS):
                table = self.tables[file_id,kind]
                flagged,missing,duplicates = issues.get((file_id,kind),(set(),set(),set()))
                caption = ('样本','基因')[tab]
                tabs.setTabText(tab,caption+(' · 有差异' if flagged or missing else ''))
                if missing or duplicates:
                    details = []
                    if missing:
                        details.append('缺少 '+'、'.join(sorted(missing)))
                    if duplicates:
                        details.append('重复映射 '+'、'.join(sorted(duplicates)))
                    file_notes.append(caption+' '+ '；'.join(details))
                with QSignalBlocker(table):
                    pairs = qpcr_saved_enrichment_pairs(self.options,file_id) if self.enrichment and kind=='samples' else None
                    for row in range(table.rowCount()):
                        original = table.item(row,1).text()
                        names = (original,qpcr_name(settings,kind,original,file_id))
                        required = (names[1]==qpcr_required(settings,kind) or
                                    pairs is not None and any(name in pairs or name in pairs.values() for name in names))
                        check = table.item(row,0)
                        if required:
                            check.setCheckState(Qt.CheckState.Checked)
                        check.setFlags(Qt.ItemFlag.ItemIsEnabled if required else
                                       Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsUserCheckable)
                        for column in (1,2):
                            item = table.item(row,column)
                            item.setForeground(QBrush(QColor('#B42318')) if original in flagged else QBrush())
                            item.setToolTip('此最终名称在其它素材中缺失，或在同一素材中重复。' if original in flagged else '')
            parent.setTabToolTip(index,'\n'.join((filename,*file_notes)))
            if file_notes:
                notes.append(filename+'　'+'；'.join(file_notes))
        if issues:
            self.message.error('\n'.join(notes[:4]))
        else:
            self.message.setText('各素材的样本和基因名称一致。')
        self.message.setToolTip('\n'.join(notes))
        return issues

    def warn_mismatch(self):
        if self.validate():
            self.message.error(self.message.text())

    def finish(self):
        result = self.settings()
        if not self.enrichment and not all(result[key] for key in REQUIRED):
            self.message.error('请填写对照组和内参基因的最终名称。')
            return
        if self.validate():
            self.warn_mismatch()
            return
        self.result_settings = self.settings()
        self.accept()


def pair_colors(role):
    background,foreground,border = PAIR_COLORS[role]
    return f'background:{background};color:{foreground};border:1px solid {border};'


class PairSampleTile(QPushButton):
    def __init__(self,page,name,role):
        super().__init__(name)
        self.page,self.name,self.role = page,name,role
        self.press_position = None
        self.drop_side = None
        self.setAcceptDrops(True)
        self.setMinimumHeight(44)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setAccessibleName(f'{page.roles[role]}样本 {name}')
        self.setToolTip(f'{page.roles[role]} · {name}\n拖到另一张卡片的上半部或下半部，也可用下方的配对选项。')
        self.clear_drop()
        self.clicked.connect(lambda:page.select_sample(name,role))

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self.press_position = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self,event):
        self.press_position = None
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self,event):
        if (self.press_position is None or not event.buttons() & Qt.MouseButton.LeftButton
                or (event.position().toPoint()-self.press_position).manhattanLength()<QApplication.startDragDistance()):
            return super().mouseMoveEvent(event)
        self.press_position = None
        self.setDown(False)
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData('application/x-biomed-ip-sample',b'1')
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        drag.setHotSpot(event.position().toPoint())
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self,event):
        source = event.source()
        if (isinstance(source,PairSampleTile) and source.page is self.page and source.name!=self.name
                and event.mimeData().hasFormat('application/x-biomed-ip-sample')):
            self.dragMoveEvent(event)

    def dragMoveEvent(self,event):
        top = event.position().y()<self.height()/2
        if self.drop_side!=top:
            self.drop_side = top
            border = 'border-top' if top else 'border-bottom'
            self.setStyleSheet('QPushButton {'+pair_colors(self.role)+
                               f'{border}:3px solid #A85C20;padding:5px 10px;border-radius:7px;'+'}')
        event.acceptProposedAction()

    def dragLeaveEvent(self,event):
        self.clear_drop()
        super().dragLeaveEvent(event)

    def clear_drop(self):
        self.drop_side = None
        self.setStyleSheet(
            'QPushButton {'+pair_colors(self.role)+'padding:5px 10px;border-radius:7px;}'
            'QPushButton:focus {border:2px solid #945217;}'
            'QPushButton:hover {border:2px solid #AD6B31;}')

    def dropEvent(self,event):
        source = event.source()
        if not isinstance(source,PairSampleTile) or source.page is not self.page or source.name==self.name:
            self.clear_drop()
            return
        top = event.position().y()<self.height()/2
        control,ip = (source.name,self.name) if top else (self.name,source.name)
        self.clear_drop()
        event.acceptProposedAction()
        QTimer.singleShot(0,self.page,lambda:self.page.bind(control,ip))


class PairingPage(QWidget):
    changed = Signal()

    def __init__(self,samples,pairs,control):
        super().__init__()
        from figure_ui import QComboBox
        self.samples = samples
        self.pairs = dict(pairs)
        self.roles = {'control':'Input' if control=='input' else 'IgG','ip':'IP','unpaired':'未配对'}
        layout = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll,1)
        panel = QWidget()
        body = QVBoxLayout(panel)
        body.setAlignment(Qt.AlignmentFlag.AlignTop)
        body.addWidget(QLabel('已配对'))
        self.paired = QGridLayout()
        body.addLayout(self.paired)
        body.addWidget(QLabel('待配对样本'))
        self.unpaired = QGridLayout()
        body.addLayout(self.unpaired)
        scroll.setWidget(panel)
        manual = QHBoxLayout()
        self.control_choice,self.ip_choice = QComboBox(),QComboBox()
        for role,combo in (('control',self.control_choice),('ip',self.ip_choice)):
            label = QLabel(self.roles[role]+' 样本')
            label.setBuddy(combo)
            manual.addWidget(label)
            combo.setAccessibleName(label.text())
            combo.addItem('选择样本')
            for name in samples:
                combo.addItem(name,name)
            manual.addWidget(combo,1)
        self.add_button = QPushButton('绑定配对')
        self.add_button.clicked.connect(self.bind_selected)
        manual.addWidget(self.add_button)
        layout.addLayout(manual)
        self.status = FigureStatus()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh()

    def select_sample(self,name,role):
        if role=='ip' or (role=='unpaired' and self.control_choice.currentData() not in (None,name)):
            self.ip_choice.setCurrentIndex(self.ip_choice.findData(name))
            self.add_button.setFocus()
        else:
            self.control_choice.setCurrentIndex(self.control_choice.findData(name))
            self.ip_choice.setFocus()

    def bind_selected(self):
        self.bind(self.control_choice.currentData(),self.ip_choice.currentData())

    def bind(self,control,ip):
        if control not in self.samples or ip not in self.samples or control==ip:
            self.status.error('请选择两个不同的样本。')
            return
        if control in self.pairs or ip in self.pairs.values():
            self.status.error(f'同一样本只能作为 {self.roles["control"]} 或 IP。可先取消原有配对。')
            return
        self.pairs[ip] = control
        self.refresh()
        self.status.setText(f'已绑定 {control} 与 {ip}。')
        self.changed.emit()

    def unbind(self,ip):
        if ip not in self.pairs:
            return
        del self.pairs[ip]
        self.refresh()
        self.status.setText(f'已取消 {ip} 的配对。')
        self.add_button.setFocus()
        self.changed.emit()

    def refresh(self):
        for grid in (self.paired,self.unpaired):
            while grid.count():
                item = grid.takeAt(0)
                item.widget().hide()
                item.widget().deleteLater()
        for index,(ip,control) in enumerate(self.pairs.items()):
            card = QFrame()
            layout = QVBoxLayout(card)
            layout.setContentsMargins(0,0,0,0)
            layout.setSpacing(2)
            layout.addWidget(PairSampleTile(self,control,'control'))
            layout.addWidget(PairSampleTile(self,ip,'ip'))
            remove = QPushButton('取消配对')
            remove.setAccessibleName(f'取消 {ip} 的配对')
            remove.clicked.connect(lambda checked=False,name=ip:self.unbind(name))
            layout.addWidget(remove)
            self.paired.addWidget(card,index//3,index%3)
        if not self.pairs:
            self.paired.addWidget(QLabel('将一张样本卡片拖到另一张卡片上，或在下方选择两个样本。'),0,0,1,3)
        assigned = set(self.pairs)|set(self.pairs.values())
        for index,name in enumerate(name for name in self.samples if name not in assigned):
            self.unpaired.addWidget(PairSampleTile(self,name,'unpaired'),index//4,index%4)
        if assigned==set(self.samples):
            self.unpaired.addWidget(QLabel('所有样本均已配对。'),0,0)
        self.set_summary(False)

    def set_summary(self,inherited):
        suffix = ' · 沿用首个素材的配对' if inherited else ''
        self.summary.setText(f'{len(self.pairs)} 对已绑定'+suffix)


def pairing_scope(scope_id):
    try:
        scope = json.loads(scope_id)
    except (TypeError,ValueError):
        scope = None
    if isinstance(scope,list) and len(scope)==3:
        file_id,_,repeat = scope
        return str(file_id),str(repeat)
    return scope_id,''


class EnrichmentPairsDialog(QDialog):
    def __init__(self,parent,files,options):
        super().__init__(parent)
        self.setWindowTitle('设置 IP 配对')
        self.resize(860,600)
        self.pages = {}
        self.inherited = {}
        first_file = pairing_scope(files[0][0])[0] if files else None
        base_repeats = {}
        raw_by_file = defaultdict(set)
        for scope_id,_,(originals,_),*_ in files:
            raw_by_file[pairing_scope(scope_id)[0]].update(originals)
        self.result_pairs = {}
        control = options.get('enrichment_control','input')
        existing = options.get('enrichment_pairs',{}).get(control,{})
        layout = QVBoxLayout(self)
        caption = 'Input' if control=='input' else 'IgG'
        instructions = QLabel(f'把一张样本卡片拖到另一张卡片的上半部或下半部。上方是 {caption} 对照，下方是 IP。'
                              '相同样本的素材会沿用首个素材的配对，可在各页单独调整。')
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        legend = QHBoxLayout()
        for role,label in (('control',caption+' 对照 · 上方'),('ip','IP 样本 · 下方'),('unpaired','未配对样本')):
            item = QLabel(label)
            item.setStyleSheet(pair_colors(role)+'padding:4px 8px;border-radius:5px;')
            legend.addWidget(item)
        legend.addStretch()
        layout.addLayout(legend)
        tabs = QTabWidget()
        layout.addWidget(tabs,1)
        file_pages = {}
        self.tab_for_scope = {}
        for scope_id,filename,(originals,_),*details in files:
            file_id,repeat = pairing_scope(scope_id)
            if details:
                repeat = details[0] or repeat
            samples = list(dict.fromkeys(originals))
            guessed = {ip:ref for ip,ref in suggested_enrichment_pairs(samples,control).items() if ref}
            aliases = {qpcr_name(options,'samples',name,file_id):name for name in samples}
            saved = existing.get(scope_id)
            if saved is None and file_id in existing:
                saved = existing[file_id]
            sample_set = frozenset(samples)
            initial = guessed if file_id==first_file else {}
            if saved is not None:
                initial = {}
                for ip,ref in saved.items():
                    ip = ip if ip in samples else aliases.get(ip)
                    ref = ref if ref in samples else aliases.get(ref)
                    if ip in samples and ref in samples and ip!=ref:
                        initial[ip] = ref
            if file_id==first_file:
                base_repeats.setdefault(sample_set,{}).setdefault(repeat,scope_id)
            elif raw_by_file[file_id]==raw_by_file[first_file]:
                matches = base_repeats.get(sample_set)
                if matches:
                    origin = matches.get(repeat,next(iter(matches.values())))
                    first = self.pages[origin].pairs
                    if saved is None or initial==first:
                        initial = first
                        self.inherited[scope_id] = origin
            page = PairingPage(samples,initial,control)
            self.pages[scope_id] = page
            page.changed.connect(lambda identifier=scope_id:self.pairing_changed(identifier))
            file_pages.setdefault(file_id,[]).append((scope_id,filename,repeat,page))
        for pages in file_pages.values():
            filename = pages[0][1]
            if len(pages)==1:
                scope_id,_,_,page = pages[0]
                index = tabs.addTab(page,Path(filename).stem)
                self.tab_for_scope[scope_id] = (index,None,0)
            else:
                repeats = QTabWidget()
                index = tabs.addTab(repeats,Path(filename).stem)
                for number,(scope_id,_,repeat,page) in enumerate(pages,1):
                    child = repeats.addTab(page,repeat or f'重复 {number}')
                    self.tab_for_scope[scope_id] = (index,repeats,child)
            tabs.setTabToolTip(index,filename)
        self.tabs = tabs
        self.refresh_inheritance()
        self.status = FigureStatus()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.finish)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def pairing_changed(self,file_id):
        self.inherited.pop(file_id,None)
        for other,origin in self.inherited.items():
            if origin==file_id:
                self.pages[other].pairs = dict(self.pages[file_id].pairs)
                self.pages[other].refresh()
        self.refresh_inheritance()

    def refresh_inheritance(self):
        for file_id,page in self.pages.items():
            page.set_summary(file_id in self.inherited)

    def finish(self):
        pairs = {}
        for file_id,page in self.pages.items():
            selected = page.pairs
            if not selected or set(selected).intersection(selected.values()):
                index,repeats,child = self.tab_for_scope[file_id]
                self.tabs.setCurrentIndex(index)
                if repeats is not None:
                    repeats.setCurrentIndex(child)
                self.status.error('请检查当前素材的配对。至少绑定一个 IP，同一样本只能担当一种角色。')
                return
            pairs[file_id] = selected
        self.result_pairs = pairs
        self.accept()


def remap_pairs(pairs,files,old,new):
    aliases = {}
    for file_id,_,(names,_),*_ in files:
        for name in names:
            if qpcr_keep(new,'samples',name,file_id):
                before = qpcr_name(old,'samples',name,file_id)
                after = qpcr_name(new,'samples',name,file_id)
                aliases.setdefault(before,set()).add(after)
    result = []
    for first,second in pairs:
        a,b = aliases.get(first,()),aliases.get(second,())
        if len(a)==len(b)==1:
            pair = [next(iter(a)),next(iter(b))]
            if pair[0]!=pair[1] and pair not in result and pair[::-1] not in result:
                result.append(pair)
    return result
