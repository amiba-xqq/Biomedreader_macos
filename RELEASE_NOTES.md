# 刊禾 · Biomed Reader 2.1（macOS）

## 本次 2.1 更新

- 收藏文章新增按多个分组和关键词搜索，支持分组/不分组、表格/卡片筛选和清除搜索。
- 新增批量取消收藏。所有取消入口先确认，再从全部分组移除，并删除不再使用的 paper 内 PDF 副本；原始 PDF 与缓存保留。
- 设置新增软件更新检查和官网入口，发现更高版本时下载对应系统/芯片的 GitHub 安装包，校验大小及 SHA256，完成后手动安装。版本同为 2.1 时提示无需更新。
- 保留此前的引用卷页补全、批量 PDF 收藏和 Word/EndNote 文档导出。


同步最新 Windows 2.1 桌面应用。提供 macOS 12+ Intel 与 Apple Silicon 两种原生 DMG，内置 Python、依赖、浏览器和期刊数据。

- 引用补全 Volume、Issue、Pages；卷号或页码缺失时，每次点击“导出/复制引用”立即尝试查询，完整时直接读取缓存。
- 新增批量收藏 PDF：多选/拖放、首页完整标题识别、必选期刊目录及分组、PubMed 匹配、自动复制关联 PDF、分组进度和可复制/导出的失败名单。
- PDF 选择窗口独立显示，主窗口最小化后可继续拖入文件。
- 保留全部联网刷新、按收藏时间倒序、离线缓存、隐藏文章、分享、年度 JIF 及数据迁移等桌面功能。

## Mac Word / EndNote 使用范围

“收藏文章 → 导出 Word / EndNote…”生成包含 EndNote 引文域、嵌入文献记录和完整参考文献的 `.docx` 引文集合；在 Mac Word 中由已安装的 EndNote CWYW 接管格式化。文档包含卷页，默认 Arial 常规 10.5 磅，支持选择期刊样式。

**本版不安装 Mac 原生 Biomed Reader Word 工具栏。** 与 Windows COM 插件不同，Mac 使用文档互通工作流，不自动跟踪分组变化。结构和真实 CWYW 重建参考文献已在 Windows Word/EndNote 测试通过；Mac Word/EndNote 实机端到端验证尚未完成。完整步骤及限制见 README。

## 安装

选择与芯片匹配的 DMG，将应用拖到 Applications。更新前退出旧版并替换应用；个人数据仍在 `~/Library/Application Support/OrangeReader/BiomedLiterature`，不随覆盖应用删除。

GitHub Actions 检查二进制最低部署目标、临时签名、源程序和打包程序启动、内置浏览器与冻结 PDF 解析，并校验 DMG。使用 ad-hoc 签名，尚无 Apple Developer ID 签名或公证；首次打开请按 macOS 的开发者验证提示操作，不需要关闭系统安全机制。
