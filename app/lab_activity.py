"""Local-calendar Token heatmap. No credentials, billing calls or chart dependencies."""
from datetime import date, datetime, time, timedelta
from PySide6.QtCore import Qt, QRectF, QEvent
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import QWidget, QToolTip


def year_days(today=None):
    """One rolling calendar year, including today, in the computer's timezone.

    Send explicit local-midnight boundaries so the server does not group UTC
    days or assume that every local day has 24 hours (daylight saving).
    """
    today = today or date.today()
    try:
        prior = today.replace(year=today.year-1)
    except ValueError:
        prior = today.replace(year=today.year-1, day=28)
    first = prior + timedelta(days=1)
    days = []
    for index in range((today-first).days+1):
        day = first + timedelta(days=index)
        start = int(datetime.combine(day,time.min).astimezone().timestamp())
        end = int(datetime.combine(day+timedelta(days=1),time.min).astimezone().timestamp())
        days.append([day.isoformat(),start,end])
    return days


class TokenActivity(QWidget):
    COLORS = ['#F0F1F2','#FFE9CB','#FAC889','#F39A42','#D66A1D','#9C410A']

    def __init__(self,parent=None):
        super().__init__(parent)
        self.days = year_days();self.rows = [];self.member = None;self.provider = None;self.ready = False
        self.setMinimumSize(760,300)
        self.setAccessibleName('最近一年每日 Token 用量热力图')
        self.setMouseTracking(True)

    def set_activity(self,data):
        self.days = data['days'];self.rows = data['daily'];self.ready = True
        self.update()

    def select_member(self,identifier):
        self.member = identifier;self.update()
    def select_provider(self,provider):
        self.provider=provider;self.update()

    def totals(self):
        values = {}
        for row in self.rows:
            if self.member is not None and row['user_id'] != self.member:
                continue
            if self.provider is not None and row.get('provider','legacy')!=self.provider:continue
            value = values.setdefault(row['day'],dict(input_tokens=0,output_tokens=0,total_tokens=0,calls=0,unknown_calls=0))
            for key in value:
                value[key] += int(row.get(key) or 0)
        return values

    def cells(self):
        if not self.days:return []
        first = date.fromisoformat(self.days[0][0]);monday = first-timedelta(days=first.weekday())
        last = date.fromisoformat(self.days[-1][0]);weeks = (last-monday).days//7+1
        step = min(22.,max(9.,(self.width()-65)/weeks));size = max(5.,step-3)
        return [(QRectF(46+(date.fromisoformat(day)-monday).days//7*step,
                        62+date.fromisoformat(day).weekday()*step,size,size),day)
                for day,_,_ in self.days]

    def color(self,total,maximum):
        if not total:return QColor(self.COLORS[0])
        level = min(5,max(1,__import__('math').ceil(total/max(1,maximum)*5)))
        return QColor(self.COLORS[level])

    def day_description(self,day):
        value = self.totals().get(day,{})
        if not self.ready:return day+'\n每日用量尚未读取成功，请刷新后查看。'
        return (f"{day}\n总 Token：{value.get('total_tokens',0):,}"
                f"\n输入：{value.get('input_tokens',0):,} · 输出：{value.get('output_tokens',0):,}"
                f"\n调用：{value.get('calls',0):,} · 用量未确认：{value.get('unknown_calls',0):,}")

    def event(self,event):
        if event.type()==QEvent.Type.ToolTip:
            for rect,day in self.cells():
                if rect.contains(event.pos()):
                    QToolTip.showText(event.globalPos(),self.day_description(day),self,rect.toRect())
                    return True
            QToolTip.hideText();event.ignore();return True
        return super().event(event)

    def paintEvent(self,event):
        painter = QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(),QColor('#FFFFFF'));painter.setPen(QColor('#40535B'))
        total = sum(r['total_tokens'] for r in self.totals().values())
        painter.drawText(12,22,f'最近一年  /  {total:,} Token' if self.ready else '最近一年  /  每日记录尚未加载')
        painter.setPen(QColor('#8A9296'))
        if self.days:painter.drawText(12,42,self.days[0][0]+' — '+self.days[-1][0]+' · 本机日历')
        values = self.totals();maximum = max([v['total_tokens'] for v in values.values()] or [1])
        cells = self.cells();last_month = None
        for rect,day in cells:
            painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(self.color(values.get(day,{}).get('total_tokens',0),maximum))
            painter.drawRoundedRect(rect,3,3)
            current = date.fromisoformat(day)
            if last_month != current.month:
                painter.setPen(QColor('#7B858A'));painter.drawText(int(rect.x()),int(rect.bottom()+30+(6-current.weekday())*(rect.width()+3)),str(current.month)+'月')
                last_month=current.month
        if cells:
            step=cells[0][0].width()+3
            painter.setPen(QColor('#8A9296'))
            for row,text in ((0,'一'),(2,'三'),(4,'五')):painter.drawText(12,int(62+row*step+step*.7),text)
            y=int(62+7*step+45);painter.drawText(12,y,'仅课题组通道 · 颜色按当前筛选的日用量分级')
            painter.drawText(self.width()-158,y,'少')
            for i,color in enumerate(self.COLORS):
                painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor(color));painter.drawRoundedRect(QRectF(self.width()-136+i*16,y-12,12,12),3,3)
            painter.setPen(QColor('#8A9296'));painter.drawText(self.width()-29,y,'多')
        painter.end()
