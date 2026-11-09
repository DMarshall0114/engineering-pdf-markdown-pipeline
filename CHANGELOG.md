# Changelog

## v0.3.1

来自 macOS 实机部署时的发现。

### 新增
- **mineru_cloud**：排除名单 `cloud_denied_dirs` / `cloud_denied_patterns`（通配符逐层匹配目录名与文件名），
  优先于白名单，且 `--allow-outside-whitelist` 也不能绕过。起因：白名单里的"常用规范"目录下夹着企业标准、院内发文。
- **doctor**：`--strict`，关键项不满足时退出码 1（默认仍只出报告、返回 0）；报告排除名单。

### 修复
- **doctor**：只查 PATH 上的 `java`，在 macOS 上会被 `/usr/bin/java` 占位程序误导，报"未找到 Java"。
  现按 `JAVA_HOME` → `PATH` → `/usr/libexec/java_home` 查找，并列出被跳过的候选。

### 文档
- Agent skill 的质检一节补上 `clean_transcript.py` 清洗步骤；说明禁用串只填旧 OCR 层特有的错字，不能填原文水印。
- 排坑指南：坑 4 补充院内发文的签署水印，坑 23 补充"白名单目录里夹着内部文件"。

## v0.3.0

基于一轮多类样本实测（纯扫描、扫描+水印、扫描+隐形 OCR 层、SHX/TrueType 施工图、图文混排书籍、PPT 导出件，
共 70 余页逐页对照，分歧页逐页渲染人工核对）以及一次 99 页扫描规范的云端端到端实测。

### 修复
- **classify**：扫描件上压着每页相同的**可见**水印文字（约 90 字）时被判为文本页，整本只抽出水印。新增跨页重复行检测。
- **classify**：图片覆盖阈值 60% → 85%。真实扫描页实测几乎都是 1.00；"标题 + 大幅效果图"的 PPT 页（覆盖 0.4~0.6）不再整页送 OCR。
- **classify**：字少、无整页图的页（PPT 标题页、图题页）不再按扫描页处理，改为文本页 + 插图定位。
- **classify**：大幅面图纸逐条统计矢量路径每页 1~4 秒，改为先读内容流字节数（与路径数 ρ≈0.98，快约 20 倍），超大页跳过计数。
- **PyMuPDF 后端**（v0.2.0 起 CI 即失败）：`/Rotate` 页的图签区域取不到文字——PyMuPDF 的文字、图片坐标是未旋转页面坐标，
  现按 `rotation_matrix` 换算到可视页面后再定位；图片外接框同样换算。
- **classify**（同上，PyMuPDF 下失败）：缺字形被替换成符号字体时，PyMuPDF 抽出的是 `nnnn…` 而不是 `■`，乱码页被当成文本页。
  乱码率现在也计入"同一字母连续 6 次以上"，以及整页无汉字时的 Latin-1 乱码（GBK 被当西文解码，如 `¸ßËÙÌú·`）。

### 新增
- **classify**：`pages[].figures` / `figure_pages`：文本页中 ≥15% 页面的插图外接框（可视坐标，已处理 `/Rotate`），图中文字另行识别。
- **classify**：图幅改按毫米判断（长边 ≥415mm），大图幅 + 内容流 ≥30 万字节计入图纸分；输出 `long_side_mm`、`content_bytes`。
- **tools/mineru_cloud.py**：MinerU 云 API 客户端（仅标准库）。目录白名单（未配置即拒绝）、token 取自环境变量或系统钥匙串且从不打印、
  扫描件+OCR 层自动强制 OCR、mixed 文档只提交 OCR 页段、整本一次提交、防路径穿越解压、禁用串 / 条文连续性 / 逐页字数完整性质检、
  `run_summary.json` 记录全部参数。
- **tools/qa_check.py**：`--forbid` 禁用串、`--clauses` 条文编号连续性（跳号、重号、乱序；条文说明分段）。
- **tools/doctor.py**：三平台环境自检。
- **tools/_compat.py**：非交互输出统一 UTF-8；按平台定位配置文件；外部命令输出按正确编码解码。
- **skills/pdf-to-markdown/SKILL.md**：三平台通用的 Agent 工作流。
- `config.example.json`、`docs/cross-platform.md`；排坑指南新增坑 16~23；基准数据新增云 API 整本 vs 逐页、矢量计数耗时。
- CI 扩展到 Ubuntu / Windows / macOS。

### 兼容性
- `classify.py` 输出 `schema_version` 升为 `"3"`，只新增字段，原有字段含义不变。
- 阈值变化会改变少数页的判定（见上），依赖旧结果的下游请复核。

## v0.2.0

### 修复
- **classify**：设计规范被误判为工程图纸。旧版全页命中 ≥4 个"设计/审核/日期/专业…"即判图纸，而这些词在规范正文里几乎每页都有。改为图签区域定位 + 强/弱特征词 + 图幅/矢量路径/句读密度计分。
- **classify**：只采样前 5 页，封面目录导致电子版被判为扫描件、后半本扫描附录被漏掉。改为全文均匀采样，≤60 页全量扫描。
- **classify**：README 与代码阈值不一致（300 vs 50），已统一并集中到脚本顶部。
- **clean_transcript**：空格正则使用 `\s` 会吞掉换行，把标题和正文粘成一行；改为只匹配水平空白，保留"第一章 总则"标题空格。
- **clean_transcript**：水印正则过宽，含 `(1)` 编号和时间戳的正文行会被整行删除（关键数字丢失）；改为只匹配行尾为时间戳、无句读标点的短行，并兼容全角括号。
- **clean_transcript**：docstring 声称的"数字完整性校验"此前并未实现，现已实现。
- README 中 OpenDataLoader PDF 的仓库链接与安装方式更正为官方 `opendataloader-project/opendataloader-pdf`、`pip install -U opendataloader-pdf`（需 Java 11+）。

### 新增
- **classify**：逐页判定 `text / scan / scan_ocr_layer / garbled / drawing / vector_only / blank`；混排文档输出 `segments` 页段路由；乱码文本层检测；扫描件隐形 OCR 层检测；正确处理 `/Rotate` 旋转页；目录批量、`jsonl`/`table` 输出、`--pages` 逐页明细；坏文件不中断批量，退出码区分。
- **PDF 后端抽象**：PyMuPDF（AGPL）与 pypdfium2（Apache/BSD）二选一，自动识别。
- **clean_transcript**：命令行化；`--reflow` 合并双栏硬换行；`--drop-repeated` 剔除页眉页脚；`--watermark` 自定义规则；`--report` 输出被删行；`--strict`。
- **rebuild_headings.py**：按条文编号重建标题层级（坑 5）。
- **qa_check.py**：字节/字符/汉字统计、OCR 形近字扫描与自动修正、两版本数字比对（坑 2、6）。
- **add_frontmatter.py**：写入溯源 Frontmatter，保留用户已有字段（坑 11）。
- 合成测试样本（`tests/samples.py`）、pytest 测试、ruff、GitHub Actions CI。
- 排坑指南新增坑 13~15（乱码文本层、只看前几页、扫描件 OCR 层与背景图）。

## v0.1.0
- 初始版本：classify.py、clean_transcript.py 与路由/排坑/基准文档。
