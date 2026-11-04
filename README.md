# Engineering PDF-to-Markdown Pipeline
> 面向中文工程规范、技术规格书与设计图纸的可靠 PDF 转 Markdown 路由工作流。

[![CI](https://github.com/DMarshall0114/engineering-pdf-markdown-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/DMarshall0114/engineering-pdf-markdown-pipeline/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

## 痛点与定位

通用 PDF 解析方案或多模态大模型在面对**中文工程技术文档**时，常常遇到以下典型痛点：
1. **盲目走 OCR**：上百页带干净文本层的国标/行规，跑视觉或重度 OCR 模型动辄数十分钟，甚至把原本干净的条文字符弄错（形近字翻车）。
2. **静默失败（0 字节）**：无文本层的纯扫描件直接用快速文本抽取工具处理，不报错但输出 0 字节内容。
3. **假文本层陷阱**：工程施工图 PDF 每一页都有 1~2k 字符，但全是图签、会签栏和图框，核心图面是矢量轮廓或图块；还有字体缺 ToUnicode 映射导致"有字但全是乱码"的文本层。
4. **混排文档**：一本规程前半本是电子版、后半本附录是扫描件，整本只能选一个引擎时总有一半吃亏。
5. **格式损毁与污染**：语音录音整理稿、会议纪要常包含大量带工号时间的水印行，以及双栏硬换行导致的中文词汇被空格割裂。

**本项目不重新造 OCR 轮子，而是提供一套经过实战检验的「探测、分流路由与后清洗」工程化方案**：
- **Fast 模式**（[OpenDataLoader PDF](https://github.com/opendataloader-project/opendataloader-pdf)）：针对具备完整内嵌文本层的标准规范与报告，**实测 200 页 42MB 仅需 1.1 秒**，条文读序精准，杜绝 OCR 幻觉。
- **Layout/Vision 模式**（[MinerU](https://github.com/opendatalab/MinerU)）：针对扫描件、复杂双栏图文排版、混合版式文档，走本地/云端版面分析与识别。
- **逐页探测与分段路由**：轻量脚本逐页识别文本页、扫描页、乱码页、图纸页，混排文档按页段分别路由。
- **后处理工具链**：水印清洗、条文标题层级重建、形近字与关键数字核对、溯源元数据，全部只依赖标准库。

---

## 快速开始

### 1. 安装依赖
```bash
# 探测工具需要一个 PDF 后端（二选一，自动识别，PyMuPDF 优先）
pip install pymupdf        # 功能最全，AGPL-3.0
# pip install pypdfium2    # Apache-2.0/BSD，对许可证敏感的内网部署用它

# Fast 引擎：OpenDataLoader PDF（需要 Java 11+）
pip install -U opendataloader-pdf
```

### 2. 先探测，再路由
```bash
python tools/classify.py 规范.pdf                 # 单文件，输出 JSON
python tools/classify.py 资料目录/ -r --format table  # 批量汇总
python tools/classify.py 规范.pdf --pages          # 附带逐页判定与命中信号
```

输出示例（混排文档）：
```json
{
  "file": "某车辆基地设计规程.pdf",
  "total_pages": 186,
  "sampled_pages": 186,
  "full_scan": true,
  "page_classes": {"text": 160, "scan": 24, "blank": 2},
  "recommended_engine": "split",
  "document_type": "mixed",
  "reason": "文档内含不同类型页面，建议按页段分别路由（见 segments）。",
  "segments": [
    {"start": 1,   "end": 162, "engine": "opendataloader-fast"},
    {"start": 163, "end": 186, "engine": "mineru-ocr"}
  ]
}
```

`recommended_engine` 取值：`opendataloader-fast` / `mineru-ocr` / `mineru-layout-or-vision` / `split`（按 `segments` 分段）。
60 页以内默认全量逐页扫描，更长的文档均匀采样 15 页；需要精确页段时加 `--sample 0`。

### 3. 按推荐执行转换
```bash
# 文本型：OpenDataLoader fast（Python API）
python -c "import opendataloader_pdf as o; o.convert(input_path=['规范.pdf'], output_dir='out/', format='markdown')"

# 扫描/图纸页段：MinerU（4.x 的 --pages 为 1-based 闭区间，可直接填 segments；CLI 以官方文档为准）
mineru-kit parse 规范.pdf -o out/规范_163-186.md --tier basic --pages 163-186

# 或者先用 qpdf 按页段拆分，再分别处理
qpdf 规范.pdf --pages . 163-186 -- part_scan.pdf
```

### 4. 后处理与质检
```bash
python tools/clean_transcript.py 纪要.md -o 纪要.clean.md --reflow --report removed.txt --strict
python tools/rebuild_headings.py out/规范.md -o out/规范.md
python tools/qa_check.py out/规范_ocr.md --against out/规范_fast.md   # 形近字 + 数字比对
python tools/add_frontmatter.py out/规范.md --source 规范.pdf --classify-json c.json --in-place
```

---

## 路由决策矩阵

```text
                    [ 输入 PDF ]
                         │
          [ 逐页提取：字符数 / 汉字 / 乱码率 / 图片覆盖率 /
            隐形文字层 / 图签区域关键词 / 图幅 / 矢量路径 ]
                         │
   ┌──────────┬──────────┼──────────────┬──────────────┐
   ▼          ▼          ▼              ▼              ▼
[ 扫描页 ] [ 扫描页+  ] [ 乱码文本层 ] [ 图纸页       ] [ 文本页 ]
 无文字     隐形OCR层    ■/□/PUA≥20%   图签区域强特征   其余有效
   │          │          │             +图幅/矢量≥3分   文本层
   └──────────┴────┬─────┘              │              │
                   ▼                    ▼              ▼
             [ mineru-ocr ]  [ mineru-layout-or-vision ] [ opendataloader-fast ]
                   └────────────────────┼──────────────┘
                                        ▼
                   [ 文档级汇总：单一类型 → 直接推荐；多类型 → split + segments ]
```

判定细节、阈值与调参方法见：[docs/routing-rules.md](docs/routing-rules.md)。

---

## 工具一览

| 工具 | 作用 | 对应排坑条目 |
|---|---|---|
| [`tools/classify.py`](tools/classify.py) | 逐页探测与引擎路由，混排文档输出页段 | 坑 1、3、13~15 |
| [`tools/clean_transcript.py`](tools/clean_transcript.py) | 水印行、中文间空格、双栏硬换行、页眉页脚清洗 + 数字完整性校验 | 坑 4 |
| [`tools/rebuild_headings.py`](tools/rebuild_headings.py) | 按条文编号重建 `##/###` 标题层级，修复列表扁平化 | 坑 5 |
| [`tools/qa_check.py`](tools/qa_check.py) | 字节/汉字统计、OCR 形近字扫描与修正、两版本数字比对 | 坑 2、6 |
| [`tools/add_frontmatter.py`](tools/add_frontmatter.py) | 写入来源文件、SHA-256、引擎、页数等溯源元数据 | 坑 11 |

## 核心文档目录

- 📖 **[路由与决策规则 (Routing Rules)](docs/routing-rules.md)**：各引擎适用边界、逐页判定信号、阈值与分批策略。
- 🛠️ **[工程排坑指南 (Pitfalls & Best Practices)](docs/pitfalls.md)**：形近字、矢量图纸、乱码文本层、转写稿清洗、统计口径等 15 条实战经验。
- 📊 **[实测基准数据 (Benchmark)](docs/benchmark.md)**：OpenDataLoader fast 与 MinerU 的性能、资源与耗时对比。
- 🧪 **[样例说明 (Examples)](examples/README.md)**：典型文档类型与合成测试样本。

---

## 开发

```bash
pip install -r requirements.txt -r requirements-dev.txt
ruff check tools tests
python -m pytest            # 用 reportlab 合成测试 PDF，不依赖任何真实（可能涉密）文件
python tests/samples.py out/  # 把合成样本导出来人工查看
```

## 贡献与反馈

欢迎提交 Issue 或 PR 补充您在处理各类工程、招投标文件及科技文献时发现的新模式与清洗经验。
误判样本请附上 `classify.py --pages` 的输出（不需要上传原文件）。

## License

本项目基于 [MIT 许可证](LICENSE) 开源。注意可选依赖 PyMuPDF 为 AGPL-3.0，如有顾虑请改用 pypdfium2 后端。
