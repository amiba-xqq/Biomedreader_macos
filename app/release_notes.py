"""Bundled release notes; no network request or developer credentials required."""
from html import escape
from pathlib import Path
import sys
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QTextBrowser,QDialogButtonBox
from version import APP_VERSION

BUILD_ID='2.2.2-20261111.3'
SECTIONS=[('2.2.2 · AI Protocol',
  'AI 助手新增 AI Protocol，可从论文、学位论文、收藏文献或自拟方法编写中文实验方案。',
  '自动加载内置科研写作、科学证据评估与文献检索技能。支持指定要提取的方法、检索补充资料、生成流程图与中文草案，并在全屏中编辑。可导出 Word 或 PDF，也可批量打包分享和导入。'),
 ('2.2.2 · AI 作图',
  'AI 助手可建立 WB / gel、IF、qPCR 和 Excel / CSV 作图项目，导出无水印、可编辑的 PDF / SVG。',
  '自动加载内置科研可视化技能。支持原图选区、WB 标尺校平、IF 多通道同步裁剪，以及 qPCR 按独立重复计算 ΔΔCt 并导出统计 CSV。可框选预览区域后继续让模型修改排版。删除卡片同时删除其本地缓存。'),
 ('2.2.2 · 内置 Scientific Agent Skills',
  '内置七项科研技能，自动用于文章总结、AI报告、科学问题、同行评审和文献寻找，安装后即可使用。',
  '随安装包提供 Scientific Critical Thinking、Scientific Writing、Scientific Brainstorming、Hypothesis Generation、Peer Review、Paper Lookup 和 Literature Review，以及相应参考资料。按任务加载科研工作流，支持 Codex 本机连接及 DeepSeek、Kimi、GLM、OpenAI API 等分析通道。结果记录使用的技能与内置版本；按生物医学同行的表达组织问题、核对论据、区分证据与假说，并结合现有检索、全文读取和报告导出流程。'),
 ('文献检索与增量更新',
  '整合 PubMed、bioRxiv 和 medRxiv，支持独立关键词、筛选及批量刷新。',
  '关键词方案可分别设置 PubMed 与预印本关键词、候选数、年份、文章类型和期刊目录。支持最新发表、相关性和综合排序；首次完整检索，后续按成功刷新日期增量合并，也可手动完整检索。全部联网刷新按照卡片顺序依次运行。'),
 ('关键词方案与离线阅读',
  '保存检索方案与结果，提供表格、卡片和隐藏文章管理。',
  '方案卡片可拖动排序并调整宽度，显示缓存数量、刷新时间和间隔。结果支持表格列宽调整、紧密或宽松卡片、来源筛选、分页及浏览位置记忆；增量新增文章显示 new⭐，打开详情后消失。缓存结果可离线查看，已访问 DOI 与已保存 AI '
  '总结具有相应标记。'),
 ('期刊目录与 JIF',
  '内置与自定义期刊目录支持多选、筛选、分享及年度 JIF。',
  '可选择多个目录的期刊并集，用于 PubMed 检索；目录支持名称搜索、JIF 范围、只看已选期刊及排序。提供 NC 精选、NC 扩选及 IF 分组，JIF 按所选年份显示；目录卡片可排序并通过压缩包分享。'),
 ('收藏、PDF 与分享',
  '收藏文章支持自定义分组、搜索、批量操作和 PDF 关联。',
  '分组与文章均支持表格或卡片展示，最近收藏优先，可复制或移动到其它分组。批量收藏 PDF 可识别标题、在指定期刊范围匹配 PubMed，选择是否链接 '
  'PDF，并报告未匹配文件。支持笔记、标签、分享压缩包及搜索已收藏文章；移出最后一组前确认关联资料的删除。'),
 ('AI 部署与课题组',
  '支持多平台模型、推理强度、PI 管理及学生访问 ID。',
  '可配置 DeepSeek、OpenAI API、Kimi、GLM，或使用 Codex 本机连接。模型设置按实际支持的档位提供推理强度，并在结果中记录。PI 可配置多个服务商、管理学生 ID、查看每日用量和余额；学生使用访问 ID '
  '登录。绘图平台独立用于模型图，包含阿里云百炼、火山引擎方舟及 GLM-Image。'),
 ('AI 总结与报告',
  '文章总结和增量报告支持中文简报、英文切换、新闻稿及 PDF 导出。',
  '优先读取已链接 PDF 或可访问的文章正文，材料不足时说明读取范围。简报展示科学问题、主要结论和创新点；新闻稿组织研究背景、发现、证据与贡献。增量报告逐篇展示结果，零新增不调用模型。报告可按需补充简报或新闻稿，保存后离线阅读；图文 PDF '
  '带模型水印，优先论文原图，绘图失败保留文字。'),
 ('AI 助手与科研对话',
  '提供文献寻找、领域知识库、科学问题探索和英文同行评审。',
  'AI 寻找文章支持研究资料、持续对话、可定位的文献引用和指定分组收藏。领域知识库可持续补充文献，供科学问题和 AI reviewer 选用；它整理证据资料，不改变模型权重。科学问题侧重领域缺口、理论冲突和技术瓶颈；reviewer '
  '按总结与意义、主要意见、次要意见输出，并支持带水印 PDF 和历史记录。'),
 ('关键词推荐与 AI 任务管理',
  '课题描述可生成英文检索建议，AI 任务支持保存、续聊及取消。',
  '关键词推荐结果可逐项复制并保存历史。AI 分析可并行运行，取消后停止本地等待与后续调用；已提交的云端请求仍可能计费。软件不另设统一 Token 上限，实际容量与费用取决于服务商及模型。模型变化时，继续使用已有对话材料前会提醒确认。'),
 ('Word 引用与 EndNote',
  'Windows Word 插件支持插入、更新引用及参考文献样式。',
  '从收藏文章选择文献后，可在 Word 光标位置插入引用并更新编号及文末参考文献。支持期刊样式、引用字体设置、卷页信息补全，以及导出可由 EndNote 接管的引用文档。'),
 ('本地数据与常用操作',
  '支持资料库备份、移动、链接、缓存清理及软件更新。',
  '本地数据与关联 PDF 集中保存，可备份为压缩包、移动或链接已有资料库。清理无用缓存回收已删除方案和 AI 历史的残留，保留有效收藏与资料；删除本地数据需再次确认。设置支持更新检查、更新说明和卸载；反馈可选择公开 GitHub Issue '
  '或私密表单。')]

class ReleaseNotesDialog(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent);self.setWindowTitle(f'刊禾 · Biomed Reader {APP_VERSION} 更新内容');self.resize(800,640)
        layout=QVBoxLayout(self);row=QHBoxLayout();heading=QLabel(f'<h2 style="color:#d96516">Biomed Reader {APP_VERSION}</h2>');row.addWidget(heading);row.addStretch()
        self.mode=QComboBox();self.mode.addItems(['精简','详细']);row.addWidget(QLabel('阅读模式'));row.addWidget(self.mode);layout.addLayout(row)
        self.browser=QTextBrowser();self.browser.setOpenExternalLinks(False);layout.addWidget(self.browser,1)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.mode.currentIndexChanged.connect(self.render);self.render()
    def render(self):
        detailed=self.mode.currentIndex()==1
        content=''.join('<h3 style="color:#bd5b15">'+escape(title)+'</h3><p>'+escape(short)+'</p>'+('<p style="color:#625447">'+escape(long)+'</p>' if detailed else '') for title,short,long in SECTIONS)
        self.browser.setHtml('<html><body style="background:#fffaf3;font-family:Microsoft YaHei UI,Arial;line-height:1.7;padding:18px"><p style="color:#8b7762">本机内置说明 · 无需联网</p>'+content+'</body></html>')

def show_release_notes(parent=None):ReleaseNotesDialog(parent).exec()

def first_run_release_notes(parent):
    settings=QSettings('OrangeReader','BiomedLiterature')
    # The installer changes this nonce even when reinstalling the same build.
    nonce=settings.value('installation_id','source')
    stamp=BUILD_ID+':'+str(nonce)
    if settings.value('last_release_notes','')==stamp:return
    show_release_notes(parent);settings.setValue('last_release_notes',stamp);settings.sync()
