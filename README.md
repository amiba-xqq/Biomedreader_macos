# 刊禾 · Biomed Reader 2.2.2

面向生物医学研究者的文献检索、收藏管理、离线阅读、引用与 AI 辅助分析桌面软件。

## 下载

| 系统 | 安装包 |
| --- | --- |
| Windows 10/11 x64 | [安装版 EXE](https://github.com/amiba-xqq/Biomedreader/releases/download/v2.2.2/BiomedReader_Setup_2.2.2_x64.exe) · [完整目录 ZIP](https://github.com/amiba-xqq/Biomedreader/releases/download/v2.2.2/BiomedReader_2.2.2_Portable_x64.zip) |
| macOS 12+，Apple Silicon | [DMG](https://github.com/amiba-xqq/Biomedreader_macos/releases/download/v2.2.2/BiomedReader_2.2.2_macOS12_AppleSilicon.dmg) |
| macOS 12+，Intel | [DMG](https://github.com/amiba-xqq/Biomedreader_macos/releases/download/v2.2.2/BiomedReader_2.2.2_macOS12_Intel.dmg) |

[Windows Release](https://github.com/amiba-xqq/Biomedreader/releases/tag/v2.2.2) · [macOS Release](https://github.com/amiba-xqq/Biomedreader_macos/releases/tag/v2.2.2) · [使用教程](https://amiba-xqq.github.io/Biomedreader/)

Release 同时提供 SHA256 校验文件。升级前请退出 Biomed Reader 和 Word，并备份本地资料库。

## 主要功能

- **文献检索**：PubMed、bioRxiv 与 medRxiv；按来源分别设置关键词，支持期刊范围、年份、JIF、排序、联网增量刷新及离线查看。
- **文献管理**：关键词方案、收藏分组、卡片与表格视图；标签、个人笔记、PDF 批量导入与本地原文关联；分组分享与引用导出。
- **文章分析**：AI 关键词推荐、文章总结、简报与新闻稿、增量 AI 报告；中英文简报、模型图及带水印的 PDF 导出。
- **科研助手**：AI 寻找文章、领域知识库、AI 提出科学问题和 AI reviewer。知识库保存可复用的文献证据资料，不微调云端模型权重。审稿结果按 Summary and significance assessment、Major comments、Minor comments 组织。
- **AI 作图**：支持 WB／gel、IF、qPCR 和 Excel／CSV 作图、可编辑 PDF／SVG 导出，以及基于项目上下文的连续修改。
- **AI Protocol**：从资料提取实验方法并编辑中文方案，支持 Word／PDF 导出和方案分享。
- **内置科研技能**：按任务加载随软件提供的科研分析、审稿和可视化技能。
- **科研对话**：正文引用编号可定位右侧文献卡片；正文和卡片分栏、独立滚动；支持消息复制、编辑、删除与对话布局恢复。
- **AI 部署**：DeepSeek、ChatGPT Codex 本机连接、OpenAI API、Kimi 和 GLM；按模型实际能力选择推理强度。个人通道与 PI／学生课题组通道分别配置，一个学生访问 ID 可使用 PI 已开通的 API 平台；Codex 本机登录不共享。
- **模型图**：阿里云百炼、火山引擎方舟、GLM-Image、分析模型可调用的作图工具或跳过；绘图平台仅用于模型图，不进入文字分析模型列表。优先使用文章已有模型图，生成能力受所选服务与模型权限限制。
- **本地数据**：支持备份、迁移与清理无用缓存，按引用关系保留有效收藏、报告、对话和知识库资料。课题组用量支持最近一年每日用量日历和来源筛选。

## 安装与使用

### Windows

运行 EXE 安装；或将完整目录 ZIP 全部解压后运行 `BiomedReader.exe`。不要只复制 ZIP 内的 EXE。

安装时可选注册当前用户的 Word 引用插件。Word 与 EndNote 需自行安装；插件用于插入／更新引文、选择期刊样式、设置参考文献字体和导出 EndNote Word 文档。

### macOS

1. 按芯片类型下载 DMG，将 `Biomed Reader.app` 拖入“应用程序”，从应用程序目录启动。
2. 应用支持 macOS 12 及以上，DMG 未进行 Apple Developer ID 公证。首次打开请核对下载来源，再按系统“隐私与安全性”中的提示允许打开。
3. DMG 保留原生 Word 模板插件与架构匹配的官方 Codex 组件。Codex 不依赖 Finder 的终端 PATH，也无需另装 Node.js。

### Mac Word 插件

1. 用 `⌘Q` 完全退出 Word。进入 Biomed Reader 的“设置 → Word 插件 → 安装／修复 Word 插件”，再重新打开 Word。
2. 按 Office 提示信任本地 `BiomedReader.dotm` 模板。**无需启用所有宏，也无需开启“信任对 VBA 工程对象模型的访问”。**
3. 在软件收藏页选择文章，回到 Word 放置光标，通过 Biomed Reader 选项卡插入／更新引用、选择期刊或调整参考文献字体。
4. 插入、更新等操作复用后台连接；只有“打开 Biomed Reader”主动显示主界面。应用未运行时，引用请求以后台模式启动。
5. 导出 EndNote 文档时生成文档副本，并保留嵌入文献记录；需安装官方 EndNote CWYW，在 EndNote 选项卡核查和更新。

默认插件位置：

- `~/Library/Group Containers/UBF8T346G9.Office/User Content.localized/Startup.localized/Word/BiomedReader.dotm`
- `~/Library/Application Scripts/com.microsoft.Word/BiomedReader.scpt`

使用自定义 Word 启动目录时，请将模板放入实际启动目录，避免重复加载旧副本。修复安装为旧模板和脚本保留 `.previous` 备份；卸载插件前退出 Word，再移走上述文件。

Mac 模板保留 Macintosh VBA 源码工程、Ribbon、编译 AppleScript 通信及安装自检，不使用 Windows COM。发布构建会检查模板结构、双架构应用、后台通信和 Codex 组件；实际 Mac Word／EndNote 交互及个人 ChatGPT 账户授权仍需在用户环境中验证，请先使用文档副本。应用的 macOS 12 最低要求与 Word／EndNote 各自的系统要求分别适用。

### AI 基本流程

1. 在“AI 部署”配置服务商，或使用 PI 提供的学生访问 ID 登录课题组；登录默认使用 `biomedreader.cn`，也可选择 `workers.dev` 入口。后续课题组访问保持本次登录所选入口，重新登录可更换。
2. 使用 Codex 时，展开“ChatGPT Codex · 本机连接”，点击“使用 ChatGPT 登录”，在官方页面完成授权，返回后刷新账户可用模型并开启。
3. 按需要选择分析模型、推理强度与模型图生成通道。AI 报告默认仅提供简报，也可选择新闻稿或两者；未生成的内容可在报告窗口按需补充。
4. 全文分析优先读取已关联 PDF，否则尝试本机有权访问的文章网页；缺少全文时会说明材料范围。校园订阅、登录、人机验证与服务商能力仍影响读取结果。
5. 保存结果后可离线查阅。文献链接、科学结论、方法建议与审稿意见均需人工核对。

## 数据、费用与反馈

默认资料库位置：Windows 为 `%LOCALAPPDATA%\OrangeReader\BiomedLiterature`；Mac 为 `~/Library/Application Support/OrangeReader/BiomedLiterature`。可在设置中查看、备份或迁移。

安装包不包含用户资料库、账号或登录凭据。调用云端 AI 会将所选材料发送给对应服务，费用由所用服务商账户承担；课题组调用费用由 PI 承担。模型能力、上下文、地区、余额与速率限制以服务商实际返回为准。

软件内提供开发者私密反馈和 GitHub Issue。请勿在公开 Issue 中发送 API Key、学生访问 ID、含隐私的数据库或未公开研究材料。

[Windows 问题反馈](https://github.com/amiba-xqq/Biomedreader/issues) · [macOS 问题反馈](https://github.com/amiba-xqq/Biomedreader_macos/issues)
