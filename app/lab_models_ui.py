"""Authenticated metadata only: no paid generation and no provider keys in UI."""
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QTableWidget,QTableWidgetItem,QAbstractItemView
from lab_service import LabAPI,LAB_PROVIDERS

class AvailableModelsDialog(QDialog):
    def __init__(self,manager,parent=None):
        super().__init__(parent);self.manager=manager;self.task=None;self.setWindowTitle('课题组 · 可用AI模型');self.resize(880,600)
        layout=QVBoxLayout(self);note=QLabel('显示已绑定Key的平台与模型目录。绘图模型仅用于模型图；具体权限和余额以服务商实际响应为准。目录查询不调用付费生成模型。');note.setWordWrap(True);layout.addWidget(note)
        self.status=QLabel();self.status.setWordWrap(True);layout.addWidget(self.status);self.table=QTableWidget(0,3);self.table.setHorizontalHeaderLabels(['平台','模型 / 状态','用途']);self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.table.horizontalHeader().setStretchLastSection(True);layout.addWidget(self.table,1)
        row=QHBoxLayout();self.refresh=QPushButton('刷新模型目录');self.refresh.clicked.connect(self.reload);row.addWidget(self.refresh);close=QPushButton('关闭');close.clicked.connect(self.reject);row.addWidget(close);layout.addLayout(row);self.reload()
    def reload(self):
        if self.task:return
        state=self.manager.store.lab_state()
        if not state:self.status.setText('请先登录课题组。');return
        token=self.manager.store.lab_token();gateway=state['identity'].get('gateway_url');self.refresh.setEnabled(False);self.status.setText('正在验证授权并读取模型目录……')
        def work():
            api=LabAPI(token,gateway);identity=api.me();rows=[]
            for provider in identity.get('providers',[]):
                if provider not in LAB_PROVIDERS:continue
                purpose='模型图' if provider in ('ark','bailian') else '文本分析 / 模型图' if provider=='glm' else '文本分析'
                try:
                    region='cn'
                    if provider=='bailian':
                        from image_routes import config
                        region=config(self.manager.store.database,'bailian')['region']
                    models=api.request('GET','/models?provider='+provider+'&region='+region).get('data') or []
                    rows.extend((LAB_PROVIDERS[provider],str(m['id']),purpose) for m in models if isinstance(m,dict) and m.get('id'))
                    if not models:rows.append((LAB_PROVIDERS[provider],'已绑定，目录为空',purpose))
                except ValueError as error:rows.append((LAB_PROVIDERS[provider],'已绑定，目录暂未确认：'+str(error),purpose))
            return rows
        self.task=self.manager.submit(self,work,'loaded','failed')
    def loaded(self,rows):
        self.task=None;self.refresh.setEnabled(True);self.table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            for j,value in enumerate(row):self.table.setItem(i,j,QTableWidgetItem(value))
        self.table.setColumnWidth(0,190);self.table.setColumnWidth(1,480);self.status.setText('已更新；请在AI部署中选择并开启模型。' if rows else 'PI尚未绑定平台。')
    def failed(self,message):self.task=None;self.refresh.setEnabled(True);self.status.setText(message)
    def reject(self):
        if self.task:self.status.setText('正在读取，请等待完成后关闭。');return
        super().reject()
