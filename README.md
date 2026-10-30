# 刊禾 · Biomed Reader 2.2.1

面向生物医学研究者的文献检索、离线阅读、收藏与 AI 辅助分析桌面软件。

## 下载

- [Windows 安装版与完整目录 ZIP](https://github.com/amiba-xqq/Biomedreader/releases/tag/v2.2.1)
- [macOS 12+ Intel / Apple Silicon DMG](https://github.com/amiba-xqq/Biomedreader_macos/releases/tag/v2.2.1)
- [使用教程与历史版本](https://amiba-xqq.github.io/Biomedreader/)

## 主要功能

- PubMed、bioRxiv、medRxiv 检索，独立关键词、增量刷新、来源筛选、隐藏文章和离线缓存。
- 关键词方案与收藏分组；卡片/表格显示；批量导入 PDF、检索收藏、分享分组、链接本地 PDF。
- JIF 年份与期刊目录筛选、引用格式与 Volume/Pages 信息补全。
- AI 关键词推荐、中文/英文简报、新闻稿、增量 AI 报告、科研文献对话、领域知识库、科学问题与 AI reviewer。
- 个人 API Key 或课题组 PI/学生通道。一个学生访问 ID 可使用 PI 已配置的平台；Codex 本机登录不共享。
- 图像通道：阿里云百炼（Qwen-Image、万相图像、Z-Image）、火山引擎方舟、GLM-Image；也可使用分析模型自身支持的作图工具或跳过。

## 2.2.1 本次更新

百炼图像模型合并为同一配置入口，支持区域与图像模型选择、刷新模型目录和打开官方用量/账单。百炼推理 Key 不用于查询需要独立账户权限的费用中心余额。
课题组保留已有学生 ID 和旧百炼平台授权映射；登录连接失败时尝试备用入口并重新读取系统代理。服务需要当前网络能够访问 Cloudflare Workers；不可达时不会误报为密钥错误。

## 安装与使用

### Windows 10/11 x64

下载 Setup EXE 并安装；或完整解压 Portable ZIP 后运行 BiomedReader.exe。升级前退出软件与 Word，建议先备份本地数据。
Windows 提供 Biomed Reader Word 插件，用于插入/更新引文、切换期刊格式和导出 EndNote 文档。Word/EndNote 需自行安装。

### macOS 12 及以上

按芯片下载对应 DMG，打开后把 Biomed Reader 拖入 Applications。应用未进行 Apple Developer ID 公证，首次打开可能需要在系统“隐私与安全性”中确认可信来源后允许打开。
Mac 不安装 Windows COM 插件；在收藏页使用“导出 Word / EndNote”生成含引文域、嵌入记录与文末参考文献的 DOCX，交由已安装的 Mac EndNote CWYW 接管。没有原生 Mac Word 工具栏。
API Key 使用 macOS 钥匙串保护。首次保存可能提示授权；从 Windows 迁移的数据中的密钥需要在 Mac 上重新输入。Codex 连接需要另行安装官方 Codex 组件并完成官方登录。

### 基本流程

1. 在期刊目录选择/建立期刊范围。
2. 新建关键词方案，设置来源、关键词与年份；首次完整检索，后续默认增量刷新。
3. 收藏文章、选择分组并按需链接 PDF；使用批量收藏 PDF 可同时匹配多篇论文。
4. AI 部署中配置本人 Key，或登录 PI 提供的学生访问 ID，再开启模型。生成模型图的平台只用于绘图，不出现在文本分析模型列表。
5. AI 结果可保存、离线查看与导出；必须人工核对引用和结论。网页全文读取受校园网络、订阅与网页权限限制。

## 数据与费用

默认数据目录：Windows `%LOCALAPPDATA%\OrangeReader\BiomedLiterature`；Mac `~/Library/Application Support/OrangeReader/BiomedLiterature`。可在设置中备份、迁移和清理无用缓存。
不会随发布包上传个人资料库或 API Key。调用云端 AI 会发送所选材料并产生服务商费用；课题组通道由 PI 承担。所有接口有自身的上下文、权限、地区和速率限制。
本次发布进行了自动化与启动检查；未以用户密钥进行付费生成实测。软件不承诺绕过网络限制、人机验证或付费全文权限。

## 反馈

使用软件反馈页打开预填 GitHub Issue 或提交私密反馈。请勿在 Issue 中上传 API Key、学生访问 ID 或含隐私的数据库。


## 最新 2.2.1 使用补充

- AI寻找文章：用户消息下可复制或编辑，AI回复下可复制。编辑后选择发送或取消；重新发送前会提示替换其后的对话与推荐卡片，已收藏文章保留。
- AI回复默认铺满对话可用宽度。拖动回复右侧细边可调整正文和内嵌文章卡片宽度，文字自动换行；每条回复的手动宽度会保留。
- 拖动输入区上方的横向分隔条可调整高度，也可拖到底部完全收起；保留分隔条可重新展开，未发送的草稿不会因此丢失。编辑历史消息会定位到该消息。
- PI或学生登录后，在对应管理/用量按钮右侧使用“可用AI模型”查询课题组绑定平台及模型目录。目录可见不等于保证所有付费模型权限，调用仍由服务商校验。
- 设置 → AI报告提供内容：默认只提供简报，也可只提供新闻稿或两者都提供。此选项只作用于AI报告；未生成的模式留空，可在报告窗口点击对应生成按钮，用上次材料补充。
- 清理无用缓存会清除未被AI报告引用且未收藏的方案文章的AI总结，以及已删除AI推荐、知识库、科学问题、reviewer和寻找文章对话的遗留索引/空闲空间。仍在使用的收藏、历史记录与知识库保留。

升级前请退出软件和Word，并备份本地数据。Windows安装版与完整目录ZIP包含相同程序；ZIP请完整解压后运行。
