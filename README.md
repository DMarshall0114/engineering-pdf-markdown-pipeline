# Engineering PDF-to-Markdown Pipeline
> 面向中文工程规范、技术规格书与设计图纸的可靠 PDF 转 Markdown 路由工作流。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

## 痛点与定位

通用 PDF 解析方案或多模态大模型在面对**中文工程技术文档**时，常常遇到以下典型痛点：
1. **盲目走 OCR**：上百页带干净文本层的国标/行规，跑视觉或重度 OCR 模型动辄数十分钟，甚至把原本干净的条文字符弄错（形近字翻车）。
2. **静默失败（0 字节）**：无文本层的纯扫描件直接用快速文本抽取工具处理，不报错但输出 0 字节内容。
3. **假文本层陷阱**：工程施工图 PDF 每一页都有 1~2k 字符，但全是图签、会签栏和图框，核心图面是矢量轮廓或图块，常规文字抽取完全抓瞎。
4. **格式损毁与污染**：语音录音整理稿、会议纪要常包含大量带工号时间的水印行，以及双栏硬换行导致的中文词汇被空格割裂。

**本项目不重新造 OCR 轮子，而是提供一套经过实战检验的「探测、分流路由与后清洗」工程化方案**：
- **Fast 模式**（[OpenDataLoader PDF](https://github.com/amondnet/opendataloader-bench)）：针对具备完整内嵌文本层的标准规范与报告，**实测 200 页 42MB 仅需 1.1 秒**，条文读序精准，杜绝 OCR 幻觉。
- **Layout/Vision 模式**（[MinerU](https://github.com/opendatalab/MinerU)）：针对扫描件、复杂双栏图文排版、混合版式文档，走本地/云端版面分析与识别。
- **自动检测与兜底**：通过轻量脚本先探测文本层密度与会签栏特征，自动推荐并分流引擎，规避 0 字节与假文本层。

---

## 快速开始

### 1. 安装依赖
```bash
# 核心依赖仅需 PyMuPDF (fitz)
pip install pymupdf

# 可选：快速文本层解析工具 OpenDataLoader PDF
# npm i -g @opendataloader/pdf 或按官方说明配置
```

### 2. 使用轻量探测与路由工具
在提交整篇文档进重型模型前，先运行 `classify.py` 探测结构：

```bash
python tools/classify.py your_document.pdf
```

输出示例：
```json
{
  "file": "your_document.pdf",
  "total_pages": 45,
  "has_text_layer": true,
  "avg_chars_per_page": 1280,
  "is_drawing_like": false,
  "recommended_engine": "opendataloader-fast",
  "reason": "检测到均匀高密度有效文本层，推荐走纯文本流式抽取，兼顾速度与零字符损耗"
}
```

---

## 路由决策矩阵

```text
               [ 输入 PDF 文件 ]
                       │
                       ▼
            [ 采样前 5-10 页字符特征 ]
                       │
         ┌─────────────┴─────────────┐
         ▼                           ▼
[ 平均汉字/字符 > 300 ]      [ 字符极少 或 0 字节 ]
         │                           │
  ┌──────┴──────┐                    ▼
  │ 检查会签栏特征 │          [ 纯扫描件 / 图片型 PDF ]
  └──────┬──────┘                    │
    否   │   是 (仅图签)              │
    ▼    └───────────────┐           │
[ 标准规范 / 文本稿 ]      ▼           │
    │             [ 矢量图纸 PDF ]     │
    ▼                    │           │
[ OpenDataLoader fast ]  └─────┬─────┘
(1.1s / 200页, 零错误)          ▼
                      [ MinerU / Layout-OCR ]
                      (本地 basic 档 / 云端 remote)
```

详细判定边界与参数调优见：[docs/routing-rules.md](docs/routing-rules.md)。

---

## 核心文档目录

- 📖 **[路由与决策规则 (Routing Rules)](docs/routing-rules.md)**：各引擎适用边界、判定阈值与分批策略。
- 🛠️ **[工程排坑指南 (Pitfalls & Best Practices)](docs/pitfalls.md)**：包含形近字校验、矢量图纸、转写稿清洗正则、统计口径等 12 条实战经验。
- 📊 **[实测基准数据 (Benchmark)](docs/benchmark.md)**：OpenDataLoader fast 与 MinerU 的性能、资源与耗时对比。

---

## 贡献与反馈

欢迎提交 Issue 或 PR 补充您在处理各类工程、招投标文件及科技文献时发现的新模式与清洗经验。

## License

本项目基于 [MIT 许可证](LICENSE) 开源。
