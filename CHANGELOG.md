# Changelog

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
