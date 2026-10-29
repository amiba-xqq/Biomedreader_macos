"""Plain final-answer formatting and a compact turn locator (no new AI calls)."""
from PySide6.QtCore import Qt,QSize,Signal
from PySide6.QtGui import QTextCursor,QTextBlockFormat
from PySide6.QtWidgets import QListWidget,QListWidgetItem,QFrame

def set_line_spacing(editor):
    cursor=QTextCursor(editor.document());cursor.select(QTextCursor.SelectionType.Document)
    block=QTextBlockFormat();block.setLineHeight(150,QTextBlockFormat.LineHeightTypes.ProportionalHeight.value);cursor.mergeBlockFormat(block)

class ConversationRail(QListWidget):
    jumpRequested=Signal(int)
    def __init__(self,parent=None):
        super().__init__(parent);self.targets=[];self.setObjectName('ConversationRail');self.setFixedWidth(44);self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAccessibleName('对话分节定位');self.setToolTip('悬停预览每轮输入与输出，点击短横线跳转；可用方向键选择并按Enter定位。')
        self.setStyleSheet('QListWidget#ConversationRail {background:transparent;border:none;outline:none;font-family:Arial,Microsoft YaHei;font-size:18pt;} QListWidget#ConversationRail::item {color:#B6C3C8;padding:0px;border:none;} QListWidget#ConversationRail::item:hover {color:#D97A22;} QListWidget#ConversationRail::item:selected {color:#F28A22;background:transparent;}')
        self.itemClicked.connect(lambda item:self.jumpRequested.emit(self.row(item)));self.itemActivated.connect(lambda item:self.jumpRequested.emit(self.row(item)))
    def rebuild(self,messages,frames):
        self.clear();self.targets=[];turns=[]
        for index,message in enumerate(messages):
            if message.get('role')=='user' or not turns:turns.append(dict(input='',output=[],target=frames[index][0]))
            text=' '.join(str(message.get('content') or '').split())
            if message.get('role')=='user':turns[-1]['input']=text
            else:turns[-1]['output'].append(text)
        for index,turn in enumerate(turns):
            item=QListWidgetItem('━');item.setTextAlignment(Qt.AlignmentFlag.AlignCenter);item.setSizeHint(QSize(34,26));item.setData(Qt.ItemDataRole.AccessibleTextRole,'第'+str(index+1)+'轮对话')
            item.setToolTip('第'+str(index+1)+'轮\n输入：'+(turn['input'][:180] or '无独立输入')+'\n输出：'+(' '.join(turn['output'])[:220] or '等待回复'))
            self.addItem(item);self.targets.append(turn['target'])
        self.setEnabled(bool(turns))
