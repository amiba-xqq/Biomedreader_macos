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
Mac 版新增原生 VBA＋Ribbon Word 模板插件，保留收藏页“导出 Word / EndNote”功能。插件提供打开软件、插入与更新引用、期刊样式和字体选择、导出嵌入文献记录的 EndNote Word 文档。Mac 实际 Word/EndNote 联调尚未完成，不能宣称与 Windows 已验证版具有完全一致的实机兼容性。
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
- AI回复默认铺满对话可用宽度；正文与右侧文献栏使用独立分栏，拖动中间分隔条可调整比例。
- 拖动输入区上方的横向分隔条可调整高度，也可拖到底部完全收起；保留分隔条可重新展开，未发送的草稿不会因此丢失。编辑历史消息会定位到该消息。
- PI或学生登录后，在对应管理/用量按钮右侧使用“可用AI模型”查询课题组绑定平台及模型目录。目录可见不等于保证所有付费模型权限，调用仍由服务商校验。
- 设置 → AI报告提供内容：默认只提供简报，也可只提供新闻稿或两者都提供。此选项只作用于AI报告；未生成的模式留空，可在报告窗口点击对应生成按钮，用上次材料补充。
- 清理无用缓存会清除未被AI报告引用且未收藏的方案文章的AI总结，以及已删除AI推荐、知识库、科学问题、reviewer和寻找文章对话的遗留索引/空闲空间。仍在使用的收藏、历史记录与知识库保留。

升级前请退出软件和Word，并备份本地数据。Windows安装版与完整目录ZIP包含相同程序；ZIP请完整解压后运行。

## Mac 原生 Word 模板插件（2.2.1）

1. 把 DMG 中的 Biomed Reader 拖入“应用程序”，从应用程序打开。不要从挂载的 DMG 中安装插件。
2. 退出 Word。首次打开软件会提示安装插件；也可进入设置 → Word 插件 → 安装 / 修复 Word 插件。
3. 重新打开 Word，按 Office 提示只信任本地 BiomedReader.dotm 模板。**不需要启用所有宏，也不需要终端用户开启“信任对VBA工程对象模型的访问”**。
4. 在 Word 的 Biomed Reader 选项卡点击打开软件；在收藏页勾选文章，再回到 Word 将光标放在正文，点击插入引用。重复插入会更新编号和文末参考文献。
5. 下拉选择期刊或点击选择期刊按关键词筛选；更换样式后点击更新引用文献。参考文献字体使用 Word 原生字体对话框，默认为 Arial、常规、10.5 磅；引文是否为上标由期刊样式决定。
6. 更新时，如果引用所对应的原收藏分组已不再包含该文章，会移除该引文并重新编号。跨文献库更新会拒绝执行，防止误删。
7. 导出为 EndNote 会创建新的文档副本，保留嵌入文献记录（包括 Volume 和 Pages）与文末参考文献。需另行安装 EndNote 桌面版及官方 CWYW；在 EndNote 选项卡核对并更新后再用于正式稿件。软件会尝试调用 CWYW 更新命令，若未能自动格式化请在 EndNote 选项卡手动更新。

默认安装位置：

- `~/Library/Group Containers/UBF8T346G9.Office/User Content.localized/Startup.localized/Word/BiomedReader.dotm`
- `~/Library/Application Scripts/com.microsoft.Word/BiomedReader.applescript`

自定义 Word 启动目录的用户需把模板放入实际启动目录。卸载插件时，退出 Word，移走上述两个文件即可；既有文档请先备份。安装修复会为旧模板及脚本保留 `.previous` 副本。

**验证范围与兼容边界：** 已在 Windows Word 中编译同一套跨平台 VBA 并验证两次插入、更新、分组失效删除和嵌入 EndNote 字段；GitHub macOS 构建验证 AppleScript 语法、模板结构、程序启动及插件 CLI。由于没有安装 Word/EndNote 的 Mac 环境，Mac Ribbon 加载、沙箱授权、字体对话框及 CWYW 接管尚未实机验证，请先使用稿件副本。此版本包含可用功能实现，但不作为“已完成 Mac 全功能兼容认证”的声明。

Biomed Reader 应用维持 macOS 12+ 的部署目标；[微软当前最新版 Office 的系统要求](https://learn.microsoft.com/en-us/officeupdates/release-notes-office-for-mac)高于 macOS 12，旧系统只能使用其可安装的 Office 版本。参考目标为 Word 16.x、EndNote 2025.4.1；[EndNote 支持范围](https://endnote.com/product-details/compatibility/)以官方说明为准。


## 本次 2.2.1 更新补充：科研对话与卡片阅读

- AI寻找文章采用更贴近科研人员阅读习惯的表达，围绕科学问题、证据、研究路径和局限展开。既有回答保持原文，新回答使用新提示词。
- AI回复左侧展示全文，右侧为独立滚动的文献卡片栏，默认占回复宽度约四分之一。拖动中间分隔条调整比例；正文中的 `[1]` 等引用编号可定位右侧对应卡片并高亮提示。右侧滚轮仅滚动文献栏，正文由对话主滚动条控制。
- 关闭后重新进入同一对话会恢复窗口大小、分栏比例、输入区高度及对话和文献栏滚动位置。文献卡片自动缓存，卡片不显示摘要，可进入详情查看；已收藏文章显示“已收藏”。
- 每条用户消息及AI回复均可删除，确认后同步清理该条独有的文献卡片、证据与续聊资料；其它对话和已收藏文章保留。移除单独删除文献卡片的入口。
- AI reviewer支持把本地稿件拖入文件列表，自动添加路径并去重；仅在执行审阅后读取和提交资料。
- 优化关键词方案及收藏文章卡片的标题换行和摘要间距，避免末行被裁切或遮挡。超长标题显示省略号，完整内容可悬停或进入详情查看。

