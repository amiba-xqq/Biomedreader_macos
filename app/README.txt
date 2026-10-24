# 刊禾 · Biomed Reader

面向生物医学文献的桌面阅读工具。当前版本 **2.1**，支持 PubMed、bioRxiv、medRxiv 检索，以及离线缓存、收藏、PDF 关联和期刊目录管理。

## 下载与安装

从 [Releases](https://github.com/amiba-xqq/Biomedreader_macos/releases/latest) 下载对应芯片的安装包：

| 电脑 | 文件 |
|---|---|
| Apple Silicon：M1、M2、M3、M4 等 | `BiomedReader_2.1_macOS12_AppleSilicon.dmg` |
| Intel Mac | `BiomedReader_2.1_macOS12_Intel.dmg` |

最低系统为 **macOS 12 Monterey**。在“苹果菜单 → 关于本机”中查看芯片和系统版本。两个 DMG 分别包含对应架构的完整应用、Python 3.12、PySide6/Qt 6.8.3、内置浏览器和期刊数据，不需要自己安装 Python。

1. 打开 DMG，将 **Biomed Reader.app** 拖到 **Applications（应用程序）**。
2. 弹出 DMG，从应用程序文件夹启动 Biomed Reader。
3. 更新前退出旧版本，再替换应用程序；个人数据保存在应用程序外，覆盖应用不会清空数据。

当前构建使用临时签名（ad-hoc），尚未使用 Apple Developer ID 签名或公证。如果 macOS 提示开发者无法验证，请确认安装包来自本仓库，在 Finder 中按住 Control 点击应用并选择“打开”；较新系统也可以在“系统设置 → 隐私与安全性”中按提示允许本次打开。macOS 12 对应“系统偏好设置 → 安全性与隐私”。不需要关闭系统安全机制。

每个版本同时发布 SHA256 校验文件。终端中可用 `shasum -a 256 安装包.dmg` 核对。

## 首次使用

### 关键词方案与搜索

- 点击“添加关键词组”，填写名称、关键词或布尔表达式，例如 `R-loop`、`ATM`、`"DNA repair" AND replication`。
- 在“编辑方案”中选择 PubMed、预印本或两者，设置年份上下限、文章类型、候选数、排序和期刊目录。
- PubMed 与预印本候选数分别设置；**0 表示不限**。同时选 bioRxiv 和 medRxiv 时，预印本候选数分别应用于每个来源。
- 首次搜索进行完整检索；后续刷新按上次成功日期到本地当天增量搜索并合并。勾选“完整检索”可重新建立候选集。
- 结果分批保存。取消或中途网络失败后，已经取得的新文章仍可离线查看；只有成功完成才更新增量检查点和最后刷新时间。
- “最新发表”“最相关”“综合最佳”均可离线排序。综合最佳兼顾相关性与日期；它不是官方网站提供的排序。

bioRxiv、medRxiv 使用各自官网的关键词搜索。可能弹出官网窗口要求人工验证，请在窗口中自行完成。窗口可最小化，软件按页限速读取；大量结果首次搜索需要较长时间。软件不会绕过验证码或访问权限。

### 查看结果与隐藏文章

关键词组支持**表格**及**嵌入式卡片**两种展现形式，卡片可选宽松或紧密密度。表格按需读取后续行，卡片通过上一页/下一页浏览。

- 表格选中文章后点击底部“隐藏 / 取消隐藏”；卡片直接点击“隐藏”，文章详情中也可以操作。
- “检索源”和“完整检索”之间有 **隐藏设置**，默认不显示隐藏文章。
- 勾选“显示隐藏的文章”后，隐藏文章会重新显示，并带有标记；点击“取消隐藏”恢复正常显示。
- 隐藏仅作用于**当前关键词方案**，不删除缓存，不取消收藏，也不影响其它关键词组。
- 隐藏状态和显示偏好会保存，重启及联网刷新后继续有效；同一 DOI/PMID 可跨检索记录保持对应。
- 缓存总数仍包含隐藏文章，“当前显示”按来源、年份、类型及隐藏设置筛选。

点击 DOI 使用系统默认浏览器打开文章页面。摘要能否提供取决于来源与访问权限；软件不自动下载付费论文。

### 收藏、PDF 与分享

- 点击“收藏♡”，文章加入与当前关键词方案同名的收藏分组。可以新增自定义分组，并复制或移动文章到其它分组。
- 收藏文章支持表格/卡片、排序、压缩包分享及读取他人分享包。
- 在收藏文章详情中点击“链接文章”，选择已下载 PDF。软件将其复制到数据目录下的 `paper`，按标题命名，并保存关联。
- 链接成功后，“查看原文”调用默认 PDF 阅读器。备份或分享时可包含已关联 PDF。

### 2.1：引用卷页和批量收藏 PDF

文章详情的“导出/复制引用”使用 PubMed 元数据，并按 DOI 回退补齐卷号、期号及页码。Volume 或 Pages 仍缺失时，每次点击都重新联网尝试；两项齐全后直接使用缓存。尚未分配卷页的在线优先文章、预印本或未提供该字段的数据源不会被填入猜测值。

“收藏文章 → 批量收藏 PDF”支持单选、多选及拖放本地 PDF。选择窗口独立于主窗口，主窗口最小化后仍保留拖放窗口。

1. 添加 PDF，必须勾选一个或多个期刊目录，并选择已有分组或输入新分组名称。
2. 软件读取首页较大字体的完整标题（包括换行），通过 PubMed 按相关性检索并核验完整标题、DOI。检索范围跟随 PubMed 设置，不使用年份或 JIF 限制。
3. 匹配成功的文章加入目标分组，PDF 副本写入当前数据目录的 `paper` 并自动关联；源 PDF 不移动、不删除。
4. 分组卡片显示总数与完成数。全部成功时提示完成；部分失败时保留报告，可复制或导出失败文件名，复制不会关闭报告。
5. 取消时保留已完成收藏。扫描件或缺少可提取标题的 PDF 会报告失败，本版未内置 OCR。

### Mac Word 与 EndNote：文档互通

Mac 2.1 在“收藏文章”提供 **导出 Word / EndNote…**。可导出当前分组或所有收藏，勾选所需文章，从 NC 扩选与已核实 EndNote 样式的交集中选择期刊（支持输入关键词筛选），生成 `.docx` 引文集合。文件包含 `ADDIN EN.CITE` 引文域、各篇嵌入的 EndNote 文献记录、卷页信息，以及非空 `EN.REFLIST` 文末参考文献；并非把 RIS 改名为 Word 文件。

1. 用桌面版 Mac Word 打开导出的 `.docx`，确认已安装匹配的 EndNote 桌面版及 CWYW 插件。
2. 使用 EndNote 选项卡的 **Update Citations and Bibliography** 更新引用；没有相同期刊样式时，在 EndNote 中安装或选择可用样式。
3. 若要写入自己的稿件，将所需的上标/引文域从该集合复制到稿件中，保留 Word 域格式，再由 EndNote 更新编号和参考文献。不要选择“仅保留文本”，也不要转换为纯文本。
4. 如需建立 EndNote 文献库，使用 EndNote 的 **Tools → Export Traveling Library**，导出文档中嵌入的记录。

默认字体 Arial、常规、10.5 磅（五号），上标跟随样式。此导出方式不在 Mac Word 安装 Biomed Reader 原生工具栏，不会跟踪收藏分组删除而自动改写稿件。Windows COM 插件不包含在 Mac 安装包中。跨平台 OOXML 已在本机 Windows Word/EndNote CWYW 中验证：清除预览参考文献后，CWYW 能依据嵌入记录重新生成完整文末参考文献。GitHub runner 没有授权的 Mac Word/EndNote，**尚未完成 Mac Word/CWYW 实机端到端验证**，不能把构建检查等同于该项验证。

Word 与 CWYW 的安装及版本匹配请参阅 [Clarivate 的 Mac CWYW 安装说明](https://docs.endnote.com/docs/endnote/2025/v1/macos/en/content/09word/installing_cwywsprtinword.htm)和[官方兼容列表](https://endnote.com/product-details/compatibility/)。应用支持 macOS 12，并不意味着任意 EndNote/Word 新版本都支持 macOS 12，应另行核对各软件要求。

### 期刊目录与 JIF

内置 IF 6–10、IF 10–20、IF 20–500、NC精选和NC扩选。三个 IF 目录合并原清单后，按已录入 **2025 年 JIF** 划分：

| 目录 | 边界 | 默认期刊数 |
|---|---|---:|
| IF 6–10 | 6≤JIF<10 | 274 |
| IF 10–20 | 10≤JIF<20 | 345 |
| IF 20–500 | 20≤JIF≤500 | 177 |

缺少该年度 JIF 或不在范围内的期刊不纳入这三个目录。升级按本机已有的 2025 年指标划分，手动修改过指标时数量可能不同。其它年度 JIF 保留，设置中可选择显示年份，默认本机年份减一；不使用其它年份数值填补所选年度空缺。

可以添加自定义目录、筛选 JIF 范围、只显示已选期刊、导入 CSV、打包分享目录。目录选择只约束 PubMed，预印本检索不受目录限制。NLM 公开目录包含历史刊名，不能据此确认所有期刊当前均被 SCI 收录。

## 设置与本地数据

支持自动适配所在屏幕宽度，或统一调整字体与布局的缩放倍率；始终使用浅色橙色主题。

默认数据目录：

- **macOS**：`~/Library/Application Support/OrangeReader/BiomedLiterature`
- **Windows**：`%LOCALAPPDATA%\OrangeReader\BiomedLiterature`

目录中的 `biomed_literature.sqlite3` 保存方案、缓存、收藏、目录及隐藏状态；`paper` 保存关联 PDF。软件可以打开数据目录或备份整个目录为 ZIP。

**移动本地数据目录**：选择专用空文件夹。软件退出后由独立进程迁移，校验、保存新路径并删除旧目录，再重新启动。操作前关闭其它软件实例。若文件被占用导致旧目录未能完全删除，会明确提示；已校验的新目录保持可用。

**链接本地数据目录**：选择已有数据文件夹，里面应直接包含 `biomed_literature.sqlite3`。软件检查结构及数据库完整性；不合格时提示并拒绝链接。链接不搬运或合并数据，不删除原来使用的目录。

所有文件/文件夹选择窗口的 **Look in** 都可以输入或粘贴文件夹路径，按 Enter 进入。

不同平台共用数据库结构。分享单篇/分组优先使用软件内置分享包。要迁移整套数据，请先退出原软件，将整个数据文件夹复制到目标电脑，再通过“链接本地数据目录”切换；不同平台浏览器会话可能需要重新验证。不建议让两台电脑同时写同一份数据库。

## 从源码运行与制作 DMG

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-build.txt
python packaging/prepare_assets.py
python app/main.py
```

在 Mac 上构建当前架构的 DMG 所需应用：

```bash
export MACOSX_DEPLOYMENT_TARGET=12.0
python packaging/make_icon.py
python -m PyInstaller --noconfirm --clean packaging/BiomedReader-macos.spec
python packaging/audit_macos.py "dist/Biomed Reader.app"
```

完整流程见 [GitHub Actions 工作流](.github/workflows/macos.yml)。Intel 与 Apple Silicon 使用各自原生 runner；构建后检查所有 Mach-O 文件的最低系统要求、签名、打包启动和内置浏览器，再生成并校验 DMG。两种架构均成功后才上传 Release。

[Qt 6.8 官方平台说明](https://doc.qt.io/qt-6.8/supported-platforms.html)列明 macOS 12 为最低支持系统；本项目固定使用 PySide6 6.8.3 的 macOS 12 wheel，并检查打包二进制的部署目标。GitHub 当前 runner 的系统高于 macOS 12，构建检查不能替代在实体 macOS 12 电脑上的完整使用验证。

## 第三方组件

参见 [第三方声明](app/THIRD_PARTY_NOTICES.txt)。应用使用 PySide6/Qt、QtWebEngine、Python、requests、PyInstaller 等组件。期刊目录来自 NLM；内置年度 JIF 来自已提供的本地表格，缺失值保留为空。没有 Clarivate API Key 或自动补全科研通 JIF 功能。

## 2.0 保留功能

- 取消最后一处收藏时，删除数据目录 `paper` 下的关联 PDF 副本并清除链接；其它分组仍引用时保留。
- 最初下载的原文件、文章缓存、隐藏状态和笔记保留。PDF 被占用时提示重试，收藏状态和链接保留。
- PubMed 增量请求增加明确的发表日期上下限；大结果集分页限定在同一日期区间。
- 仅 PubMed 的检查点不再受预印本年限和候选数影响。增量结果与已有缓存合并，不会隐藏旧文章。

### 大缓存刷新与 2.0

PubMed 与预印本分别记录成功检查点。大量结果中的个别请求失败时，已成功来源仍可按日增量；未完成的 PubMed 检索复用缓存、补取缺失记录。部分失败不再更新整体成功刷新时间，界面会显示各来源本次的检索模式。

旧版若没有留下成功检查点，升级后的首次刷新需要核对匹配 PMID，但会复用已缓存摘要，不据缓存数量伪造成功检查点。核验完成后，后续刷新进入日级增量；主动勾选“完整检索”仍重新建立候选集。

关键词方案默认展现形式
--------------------
设置可选择宽松的嵌入式卡片、紧密的嵌入式卡片或表格，默认紧密的嵌入式卡片。
每次重新启动软件，每个关键词方案使用设置中的默认形式。
方案内修改展现形式与密度仅在本次运行有效，不改变全局默认值。
点击 DOI 后保持原有字体淡化效果，卡片背景始终为白色。

