# 样例文档说明 (Examples)

本项目提供针对典型中文工程场景的转换对比与脱敏样例说明：

## 1. 规范与标准条文类 (`text-layer-spec`)
- **特征**：出版发行的标准规程、设备技术规格书，内嵌文字层清晰。
- **推荐策略**：`opendataloader-fast`
- **对比**：
  - 传统 OCR 耗时 5 分钟且可能发生 `FAS` 错认为 `CFAS)` 等字母和标点幻觉；
  - 流式 Fast 模式耗时约 1 秒，100% 原始文本还原。

## 2. 扫描件与图像型 PDF (`scanned-drawing`)
- **特征**：无文字层、盖章扫描件、老旧工程技术资料。
- **推荐策略**：`mineru-ocr`（本地 basic 档离线运行，或云端批处理）
- **要点**：分批控制在 5~10 页，长篇先拆分，记录状态断点续跑。

## 3. 语音/会议整理稿清洗 (`transcript-cleanup`)
- **特征**：第三方语音识别导出的 PDF 整理稿，夹带工号时间戳水印，且双栏硬断行包含散落空格。
- **推荐策略**：`python tools/clean_transcript.py 纪要.md -o out.md --reflow --report removed.txt --strict`，被删行写入 `removed.txt` 供复核，数字完整性校验失败时返回退出码 3。

## 4. 工程施工图 (`engineering-drawing`)
- **特征**：A1/A0 横向图幅（或竖向存储 + `/Rotate 90`），右下角图签含图号、图名、比例、审定、校核等，正文为矢量线条。
- **推荐策略**：`mineru-layout-or-vision`；`classify.py --pages` 的 `signals` 会列出"图签区域强特征词×N""矢量路径×N"等命中项。

## 5. 电子版 + 扫描附录混排 (`mixed`)
- **特征**：正文为电子版，审查批复、会签页、附图为扫描件。
- **推荐策略**：`classify.py --sample 0` 输出 `segments`，按页段分别走 fast 与 MinerU，最后按页码合并。

## 合成样本

仓库不收录真实 PDF（`.gitignore` 已忽略 `*.pdf`，也避免涉密文件误提交）。以上各类型都有可复现的合成样本，由 `tests/samples.py` 用 reportlab 生成：

```bash
python tests/samples.py samples_out/
python tools/classify.py samples_out/ --format table
```

| 样本 | 模拟场景 | 期望判定 |
|---|---|---|
| `spec.pdf` | 大量"设计/审核/比例/审定"字样的规范正文 | `text_native`（旧版误判为图纸） |
| `cover_then_spec.pdf` | 封面 + 目录 + 3 页空白后接正文 | `text_native`（旧版误判为扫描件） |
| `drawing.pdf` / `drawing_rotated.pdf` | A1 横向图纸 / 竖向存储 + `/Rotate 90` | `engineering_drawing` |
| `scan.pdf` | 纯图像页 | `scan_or_image` |
| `scan_ocr.pdf` | 图像 + 隐形 OCR 文字层 | `scan_with_ocr_layer` |
| `garbled.pdf` | 未嵌入 CJK 字体，抽取为 `■` | `garbled_text_layer` |
| `mixed.pdf` | 文本 3 页 + 扫描 3 页 + 空白 + 文本 2 页 | `mixed`，三段 |
