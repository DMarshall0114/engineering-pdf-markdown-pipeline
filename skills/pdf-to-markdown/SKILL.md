---
name: pdf-to-markdown
description: 把中文工程 PDF（规范、扫描件、施工图、PPT 导出件、图文混排书籍）转成可靠的 Markdown——先逐页探测再分流，扫描件强制重新 OCR，结果做编号连续性与禁用串质检。Windows / macOS / Linux 通用。
platforms: [windows, macos, linux]
---

# PDF → Markdown（中文工程文档）

工具在仓库 `tools/` 下，只依赖 Python 3.10+ 标准库和一个 PDF 后端（PyMuPDF 或 pypdfium2）。
下文 `$REPO` 指仓库所在目录。所有阈值在 `tools/classify.py` 顶部，含义见 `docs/routing-rules.md`。

## 0. 每台机器第一次用：自检

```bash
python $REPO/tools/doctor.py --json
```

按结果决定本机能走哪些路，**不要假设别的机器上的能力在这里也有**：
- 没有 OpenDataLoader 或 Java < 11 → 文本页改用 PyMuPDF 直接抽取；
- 没有 MinerU CLI、也没有云 API token → 扫描页只能标记出来，交给用户用 MinerU 图形版处理；
- 不要调用不存在的脚本或命令，不要编造识别结果。

## 1. 探测

```bash
python $REPO/tools/classify.py 文件.pdf --sample 0 --pages > classify.json
```

看 `document_type`、`recommended_engine`、`segments`、`figure_pages`、`warnings`。逐页判定：

| 页类型 | 处理 |
|---|---|
| `text` | 直接抽取（OpenDataLoader fast 或 PyMuPDF）。`figures` 里的大图另行识别 |
| `scan` | OCR（含"文字只是跨页重复水印"的扫描页） |
| `scan_ocr_layer` | **必须重新 OCR，并强制 OCR**。旧层的条文编号、尺寸、标准号错误率极高 |
| `garbled` | 按图像 OCR |
| `drawing` / `vector_only` | 版面解析。无文字层（SHX 字体）→ 高分辨率分块渲染后识别，先图签/标题栏再说明区；有文字层（TrueType）→ 带坐标抽取，按图签区、说明区分组 |
| `blank` | 跳过 |

`mixed` 文档按 `segments` 分段处理，最后按页码合并。

## 2. 云 API（只限白名单目录）

```bash
python $REPO/tools/mineru_cloud.py 文件.pdf --classify-json classify.json --dry-run   # 先看参数
python $REPO/tools/mineru_cloud.py 文件.pdf --classify-json classify.json -o 输出目录 \
       --forbid "<旧层错字1>" --forbid "<旧层错字2>" --strict
```

规则：
- 能否上传**只看文件所在目录**是否在配置 `cloud_allowed_dirs` 内，不要自己判断内容是否涉密。
  不在白名单、又不确定时，**先问用户**；用户明确同意后才加 `--allow-outside-whitelist`。
- 整本（或 `segments` 页段）一次提交，不要逐页提交。
- token 只从环境变量或系统钥匙串读取；汇报时只说"读取成功、长度 N"，不显示任何字符。
- 退出码：0 成功，1 参数/配置，3 API/网络，4 质检未过，5 白名单拒绝。

## 3. 本地处理

- 文本页：`opendataloader-pdf` fast 模式（需 Java 11+），没有就用 PyMuPDF `page.get_text()`。
- 扫描页：本地 MinerU CLI，使用 OCR 方法（参数以所装版本 `--help` 为准）。
- 含图页：按 `figures` 外接框以 2 倍分辨率裁图（PyMuPDF：`page.get_pixmap(clip=..., matrix=fitz.Matrix(2, 2))`），
  白名单内可送云 API，白名单外只标记"含图，需单独识别"并输出外接框，交用户处理；结果按 `y0` 插回正文。
- 视觉模型：只有在用已知答案的样本实测过、超时率低且不漏字时才可使用；输出未经人工复核不得写进成果。

## 4. 质检（每次都做）

```bash
python $REPO/tools/qa_check.py 结果.md --clauses --forbid "<已知错字>" --strict
python $REPO/tools/qa_check.py 结果_ocr.md --against 结果_fast.md     # 两个版本比数字
```

- **禁用串**：扫描件旧 OCR 层里核实过的错字不得出现，出现即说明沿用了旧层。
- **条文连续性**：每节 x.y.z 从 1 起连续；跳号、重号逐个渲染原页核对（规范本身也可能删条）。
- **逐页字数**：`mineru_cloud.py` 自动比对新结果与原文字层的每页汉字数，比值 <0.8 或 >1.2 的页要核对。
- 统计字数用汉字数，不用字节数（UTF-8 一个汉字 3 字节）。
- 最后：`rebuild_headings.py` 重建标题层级，`add_frontmatter.py` 写入来源、SHA-256、引擎。

## 5. 平台相关的坑

- 判断命令是否存在用 `shutil.which()`；读原生命令输出用 `tools/_compat.py` 的 `run_text()`。
  Windows 原生命令输出是 GBK，Python 处于 UTF-8 模式时 `text=True` 会解码失败，而且异常可能被吞掉，
  被误判成"没装"。不要用 `errors="replace"` 掩盖——中文路径会静默变乱码。
- 读写文件一律 `encoding="utf-8"`。
- 路径一律从配置读取，不写死盘符或用户名。

## 汇报格式

完成后报告：文件名与页数、各页类型计数、走了哪条路、耗时、云 API 消耗页数、
禁用串检查结果、条文编号首尾与断点、字数异常页、输出目录。失败或跳过的步骤如实说明原因。
