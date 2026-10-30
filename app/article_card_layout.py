"""Reserve real wrapped-title height before the abstract gets remaining space."""
from PySide6.QtCore import Qt,QRect,QEvent,QTimer
from PySide6.QtWidgets import QLabel
from interface_scale import scaled


class CardTitle(QLabel):
    def __init__(self,text,card,compact):
        super().__init__(text,card)
        self.full_text=text;self.card=card;self.compact=compact;self.pending=False
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignTop)
        self.setContentsMargins(0,1,0,5)
        self.setToolTip(text)

    def refresh_height(self):
        self.pending=False
        width=max(40,self.width()-self.contentsMargins().left()-self.contentsMargins().right()-4)
        metrics=self.fontMetrics()
        padding=scaled(7)
        maximum=round(self.card.height()*.43)
        layout=self.card.layout()
        if layout is not None and hasattr(self.card,'abstract'):
            margins=layout.contentsMargins()
            reserved=margins.top()+margins.bottom()+max(0,layout.spacing())*max(0,layout.count()-1)
            for index in range(layout.count()):
                item=layout.itemAt(index)
                if item.layout() and item.layout().indexOf(self)>=0:continue
                reserved+=self.card.abstract.minimumHeight() if item.widget() is self.card.abstract else item.sizeHint().height()
            maximum=min(maximum,self.card.height()-reserved)
        maximum=max(metrics.lineSpacing()+padding,maximum)
        minimum=min(metrics.lineSpacing()+padding,maximum)
        def text_height(text):
            return metrics.boundingRect(QRect(0,0,width,100000),Qt.TextFlag.TextWordWrap,text).height()
        text=self.full_text
        if text_height(text)+padding>maximum:
            # Extremely long titles end on a complete visible line rather than
            # drawing a clipped descender. The tooltip/details retain all text.
            low,high=0,len(text)
            while low<high:
                middle=(low+high+1)//2
                if text_height(text[:middle].rstrip()+'…')+padding<=maximum:low=middle
                else:high=middle-1
            text=text[:low].rstrip()+'…'
        if self.text()!=text:self.setText(text)
        height=max(minimum,text_height(text)+padding)
        if self.height()!=height:self.setFixedHeight(height)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.queue_refresh()

    def event(self,event):
        result=super().event(event)
        if event.type() in (QEvent.Type.FontChange,QEvent.Type.StyleChange,QEvent.Type.Show) and hasattr(self,'pending'):self.queue_refresh()
        return result

    def queue_refresh(self):
        if self.pending:return
        self.pending=True
        QTimer.singleShot(0,self.refresh_height)
