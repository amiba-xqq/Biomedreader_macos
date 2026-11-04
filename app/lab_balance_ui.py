"""PI-only balance queries against official endpoints, using active local keys."""
from PySide6.QtCore import QUrl,QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QVBoxLayout,QHBoxLayout,QTableWidget,QTableWidgetItem,QPushButton,QLabel,QHeaderView
from lab_ui import CloudDialog
from lab_service import LAB_PROVIDERS

BILLS={'deepseek':'https://platform.deepseek.com/usage','openai':'https://platform.openai.com/settings/organization/billing/overview','kimi':'https://platform.moonshot.cn/console/account','glm':'https://bigmodel.cn/finance-center/finance/overview','bailian':'https://bailian.console.aliyun.com/','ark':'https://console.volcengine.com/ark/'}

class LabBalanceDialog(CloudDialog):
    def __init__(self,manager,parent=None):
        super().__init__(parent);self.manager=manager;self.task=None;self.setWindowTitle('PI · 账户余额与账单');self.resize(880,530)
        self.state=manager.store.lab_state()
        layout=QVBoxLayout(self);layout.addWidget(QLabel('仅查询当前PI账户缓存的Key；同一平台的模型通常共享账户余额。该操作不调用生成模型。'))
        self.status=QLabel();layout.addWidget(self.status);self.table=QTableWidget(0,3);self.table.setHorizontalHeaderLabels(['平台','余额 / 查询状态','官方用量与账单']);layout.addWidget(self.table,1)
        header=self.table.horizontalHeader();header.setStretchLastSection(False);header.setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents);header.setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch);header.setSectionResizeMode(2,QHeaderView.ResizeMode.ResizeToContents)
        header.sectionResized.connect(lambda *_:QTimer.singleShot(0,self.table.resizeRowsToContents))
        row=QHBoxLayout();refresh=QPushButton('一键刷新全部余额');refresh.clicked.connect(self.refresh);row.addWidget(refresh);close=QPushButton('关闭');close.clicked.connect(self.reject);row.addWidget(close);layout.addLayout(row);self.refresh()
    def refresh(self):
        if self.task:return
        if not self.state or self.state['identity']['role']!='pi':self.status.setText('此功能仅限PI。');return
        from lab_local import account
        saved=account(self.manager.store.database,self.state['identity'].get('local_owner','')) or {}
        keys=saved.get('keys',{})
        if not keys:self.status.setText('当前账户没有可读取的本地Key，请先同步。');return
        self.status.setText('正在向各服务商查询……可随时关闭。')
        def work():
            from ai_service import AIClient
            from ai_cancel import check_cancelled
            results=[]
            for provider,key in keys.items():
                check_cancelled()
                if provider not in ('deepseek','kimi'):results.append((provider,'未接入可核实的普通API Key余额接口，请查看官方账单'));continue
                try:
                    with AIClient(provider,key) as client:data=client.balance()
                    text='；'.join(str(r.get('currency',''))+' '+str(r.get('total_balance','未返回')) for r in data.get('balance_infos',[])) or '服务商未返回余额'
                except ValueError as error:text=str(error)
                results.append((provider,text))
            return results
        self.task=self.manager.submit(self,work,'loaded','failed')
    def loaded(self,rows):
        if getattr(self,'_closed',False):return
        self.task=None;current=self.manager.store.lab_state()
        if not current or current['identity'].get('local_owner')!=self.state['identity'].get('local_owner'):self.status.setText('账户已切换，结果已丢弃。');return
        self.table.setRowCount(len(rows))
        for i,(provider,text) in enumerate(rows):
            self.table.setItem(i,0,QTableWidgetItem(LAB_PROVIDERS.get(provider,provider)));self.table.setItem(i,1,QTableWidgetItem(text))
            button=QPushButton('打开用量 / 账单');button.clicked.connect(lambda checked=False,p=provider:QDesktopServices.openUrl(QUrl(BILLS[p])));self.table.setCellWidget(i,2,button)
        self.table.resizeRowsToContents();self.status.setText('查询完成；余额以服务商账户为准。')
    def failed(self,message):
        if not getattr(self,'_closed',False):self.task=None;self.status.setText(message)
