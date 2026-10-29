"""Separate biomedical Biomni credentials from the unrelated IT platform."""
from PySide6.QtCore import QUrl,Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QPushButton,QHBoxLayout,QLabel,QDialog,QVBoxLayout,QTextBrowser,QDialogButtonBox
from ai_ui import AISettingsDialog,plain_label

class BiomniSettingsDialog(AISettingsDialog):
    def __init__(self,manager,parent=None):
        super().__init__(manager,'biomni',parent);self.setWindowTitle('Biomni · Biomedical AI Agent · API设置');self.resize(740,560);self.refresh_button.setText('验证Key／刷新档位')
        for label in self.findChildren(QLabel):
            if label.text().startswith('优先读取/models接口'):label.setText('联网验证只查询Biomni项目访问权限，不创建科研任务。standard／fast／max来自官方API规范，不是基础模型名称；具体可用权限以实际任务响应为准。')
        if not self.config['encrypted_key']:self.key_edit.setPlaceholderText('Biomni Lab / Phylo API Key')
        self.information_dialog=None;self.layout().insertWidget(0,plain_label('连接产品：Biomedical AI Agent · Biomni Lab（Phylo）。不适用于Biomni.com的IT Service Experience Platform。\nAPI为早期访问，账户需已获得API权限；使用Biomni Lab Settings → API Keys中的Key。',True))
        more_row=QHBoxLayout();self.more_button=QPushButton('平台区别、Key与费用说明');self.more_button.setProperty('secondary',True);self.more_button.clicked.connect(self.show_information);more_row.addWidget(self.more_button);more_row.addStretch();self.layout().insertLayout(1,more_row)
        buttons=QHBoxLayout();platform=QPushButton('打开Biomni Lab／获取API Key');platform.setProperty('secondary',True);platform.clicked.connect(lambda:QDesktopServices.openUrl(QUrl('https://biomni.phylo.bio')));buttons.addWidget(platform)
        docs=QPushButton('API说明');docs.setProperty('secondary',True);docs.clicked.connect(lambda:QDesktopServices.openUrl(QUrl('https://docs.biomni.phylo.bio/api/authentication')));buttons.addWidget(docs);buttons.addStretch();self.layout().insertLayout(3,buttons)
    def show_information(self):
        if self.information_dialog is None:
            dialog=BiomniInformationDialog(self);self.information_dialog=dialog;dialog.finished.connect(lambda:self.clear_information(dialog))
        self.information_dialog.showNormal();self.information_dialog.raise_();self.information_dialog.activateWindow()
    def clear_information(self,dialog):
        if self.information_dialog is dialog:self.information_dialog=None

class BiomniInformationDialog(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent);self.setWindowTitle('Biomni · 平台区别、Key与费用说明');self.resize(720,540);self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose,True)
        self.setWindowFlags(Qt.WindowType.Window|Qt.WindowType.WindowMinMaxButtonsHint|Qt.WindowType.WindowCloseButtonHint);self.setWindowModality(Qt.WindowModality.WindowModal)
        layout=QVBoxLayout(self);self.browser=QTextBrowser();self.browser.setOpenExternalLinks(True);self.browser.setStyleSheet('QTextBrowser {background:white;border:1px solid #E8D5C4;border-radius:12px;padding:18px;}');layout.addWidget(self.browser)
        self.browser.setHtml('''<html><body style="font-family:Arial,Microsoft YaHei;color:#30251D;font-size:11pt;">
<h2 style="color:#B45309;">Biomni 使用说明</h2><h3>Biomedical AI Agent · 生物医学研究</h3>
<p>本连接针对Biomni Lab（Phylo）生物医学研究Agent。请使用该平台 Settings → API Keys 中创建的Key。</p>
<h3>IT Service Experience Platform · 同名IT平台</h3><p>Biomni.com提供IT／HR服务，不是生物医学Agent，其Key不能用于此处。</p>
<h3>API Key与访问范围</h3><p>自助API地址为 https://api.phylo.bio/v1。目前需要账户已获API访问权限；企业专用地址和Stanford开源版自建服务尚未接入。个人通道Key在本机加密保存；课题组通道由PI将Key配置到服务端，学生使用访问ID，无法读取PI Key。</p>
<h3>任务与费用</h3><p>首次科研调用会创建云端“Biomed Reader”项目，只提交本次任务材料。standard、fast、max是任务档位，不是基础模型名称。费用以平台账单为准；当前接口没有可核实的精确Token和余额查询，未确认用量不代表零消耗。</p>
<p><a href="https://biomni.phylo.bio">打开Biomni Lab</a> · <a href="https://docs.biomni.phylo.bio/api/authentication">阅读API说明</a></p></body></html>''')
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);buttons.button(QDialogButtonBox.StandardButton.Close).setText('关闭');buttons.rejected.connect(self.reject);layout.addWidget(buttons)
