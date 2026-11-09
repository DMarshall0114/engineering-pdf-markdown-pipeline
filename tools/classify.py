#!/usr/bin/env python3
"""
classify.py - 轻量 PDF 结构探测与转换引擎路由推荐工具

在执行耗时的 PDF 转 Markdown 任务前，逐页探查文本层密度、乱码、
扫描件特征（整页图片 + 隐形 OCR 文本 / 跨页重复的水印文字）与工程图纸特征
（图签区域定位、图幅、矢量路径或内容流体量），给出文档级推荐引擎；
对于扫描页与文本页混排的文档，输出按页段拆分的路由方案；
文本页中面积较大的插图（其中文字 fast 模式抽不到）会单独列出位置。

用法：
    python tools/classify.py 文件.pdf
    python tools/classify.py 目录/ --recursive          # 批量，每个文件一行 JSON
    python tools/classify.py 文件.pdf --pages             # 附带逐页明细
    python tools/classify.py 文件.pdf --sample 0          # 全量逐页扫描
    python tools/classify.py 目录/ --format table         # 终端表格汇总

退出码：0 成功；1 参数错误；2 存在探测失败的文件。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _compat import ensure_utf8_stdio  # noqa: E402
from _pdfbackend import (  # noqa: E402
    BackendUnavailable,
    EncryptedPDF,
    RawPage,
    open_pdf,
)

SCHEMA_VERSION = "3"

# ------------------------------------------------------------------ 阈值
# 所有阈值集中在这里，与 docs/routing-rules.md 保持一致。
MIN_TEXT_CHARS = 50  # 每页非空白字符数低于此值视为"无有效文本"
BLANK_MAX_CHARS = 10
# 图片覆盖页面 ≥85% 视为扫描/图像页。真实扫描页实测几乎都是 1.00；
# 0.6 会把"标题 + 大幅效果图"的 PPT 导出页（覆盖 0.4~0.6）误送 OCR。
SCAN_IMAGE_COVERAGE = 0.85
OCR_LAYER_INVISIBLE_RATIO = 0.5  # 隐形文本对象占比 ≥50% 视为"扫描件 + OCR 隐形文字层"
GARBLED_RATIO = 0.2  # 乱码字符占比 ≥20% 视为文本层损坏（缺 ToUnicode 等）
DRAW_LONG_MM = 415.0  # 长边 ≥415mm（A3 为 420mm，留取整余量）视为大图幅
MANY_PATHS = 300  # 矢量路径对象数
# 内容流字节数 ≥30 万视为"矢量很重"。只在大图幅上作为图纸信号：
# 图文混排书籍的插图页内容流也可达 30 万字节，单看字节会误判。
DRAW_BYTES_MIN = 300_000
FIGURE_MIN_AREA = 0.15  # 文本页中单张图片占页面 ≥15%：图中文字 fast 抽不到，列入 figures 另行识别
WATERMARK_REPEAT = 0.5  # 某行文字出现在 ≥50% 的采样页上，视为水印/页眉页脚
WATERMARK_MIN_PAGES = 3  # 采样页少于该数时不做跨页重复判断
PROSE_MIN_CJK = 200
PROSE_PUNCT_PER_100 = 4.0  # 每 100 个汉字中句读标点数，条文正文通常 6~12
DRAWING_SCORE = 3
DEFAULT_SAMPLE = 15
AUTO_FULL_SCAN_PAGES = 60  # 不超过该页数时默认全量扫描

# 图签"强特征词"：在规范正文中很少成组出现
TITLE_BLOCK_STRONG = [
    "图号", "图名", "比例", "审定", "校核", "会签", "制图", "描图", "图幅",
    "项目负责人", "专业负责人", "设计号", "工程编号", "子项", "版次", "出图日期",
]
# 弱特征词：规范、说明书正文中大量出现，只作辅助（旧版本误判的根源）
TITLE_BLOCK_WEAK = ["设计", "审核", "日期", "阶段", "专业", "工程名称", "标段", "建设单位"]

PROSE_PUNCT = set("。；，、：")
_CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0002fa1f]")
_GARBLE_RE = re.compile(r"[�■□-\x00-\x08\x0b\x0c\x0e-\x1f]|\(cid:\d+\)")

ENGINE_BY_CLASS = {
    "text": "opendataloader-fast",
    "scan": "mineru-ocr",
    "scan_ocr_layer": "mineru-ocr",
    "garbled": "mineru-ocr",
    "drawing": "mineru-layout-or-vision",
    "vector_only": "mineru-layout-or-vision",
    "blank": None,
}

REASONS = {
    "text_native": "检测到稳定的内嵌文本层，推荐走纯文本流式抽取（秒级处理且杜绝 OCR 误字）。",
    "scan_or_image": "未检测到有效内嵌文本层（纯扫描件或图片型 PDF），必须走 OCR/版面模型。",
    "scan_with_ocr_layer": "页面主体为扫描图像，文字是隐形 OCR 层（编号、尺寸、标准号错误率高），须强制 OCR 重新识别。",
    "garbled_text_layer": "文本层存在大量乱码（常见于字体缺少 ToUnicode 映射），抽取结果不可用，需按图像走 OCR。",
    "engineering_drawing": "图签区域命中多个强特征词且图幅/矢量特征符合施工图，建议走版面分析或 Vision 兜底。",
    "blank": "未发现可抽取内容（空白页或纯矢量线框）。",
    "mixed": "文档内含不同类型页面，建议按页段分别路由（见 segments）。",
}


# ------------------------------------------------------------------ 工具函数
def count_chinese_chars(text: str) -> int:
    """统计汉字数（含扩展 A/B 区与兼容区），不要用 wc -c 字节数代替字数。"""
    return len(_CJK_RE.findall(text))


def garbled_ratio(text: str) -> float:
    stripped = re.sub(r"\s", "", text)
    if not stripped:
        return 0.0
    bad = sum(len(m) for m in _GARBLE_RE.findall(stripped))
    return min(bad / len(stripped), 1.0)


def _distinct_hits(text: str, words: Iterable[str]) -> int:
    return sum(1 for w in words if w in text)


def _norm_line(line: str) -> str:
    return re.sub(r"\s+", "", line)


def repeated_lines(texts: list[str]) -> frozenset[str]:
    """跨页重复出现的行（水印、页眉页脚、下载站标记）。

    扫描件上常压着一层"可见"的水印文字，字符数足以越过有效文本阈值，
    但页页相同；不剔除它，整本扫描件会被当作文本页送去 fast 抽取，产出只有水印。
    """
    n = len(texts)
    if n < WATERMARK_MIN_PAGES:
        return frozenset()
    seen: Counter[str] = Counter()
    for t in texts:
        seen.update({k for k in (_norm_line(x) for x in t.splitlines()) if len(k) >= 4})
    need = max(2, math.ceil(n * WATERMARK_REPEAT))
    return frozenset(k for k, c in seen.items() if c >= need)


def _body_chars(text: str, repeated: frozenset[str]) -> int:
    if not repeated:
        return len(re.sub(r"\s", "", text))
    return sum(len(k) for k in (_norm_line(x) for x in text.splitlines()) if k not in repeated)


@dataclass
class PageVerdict:
    page: int  # 1-based，便于人工核对
    cls: str
    chars: int
    cjk: int
    garbled_ratio: float
    image_coverage: float
    invisible_ratio: float
    path_count: int
    landscape_or_large: bool
    title_block_score: int
    signals: list[str] = field(default_factory=list)
    long_side_mm: float = 0.0
    content_bytes: int = 0
    # 需另行识别的插图：[x0, y0, x1, y1, 面积占比]，可视页面坐标，左上原点，单位 pt
    figures: list[list[float]] = field(default_factory=list)


def classify_page(p: RawPage, repeated: frozenset[str] = frozenset()) -> PageVerdict:
    """纯函数：根据单页观测量判定页面类型。repeated 为跨页重复行（见 repeated_lines）。"""
    text = p.text or ""
    chars = len(re.sub(r"\s", "", text))
    body_chars = _body_chars(text, repeated)
    cjk = count_chinese_chars(text)
    g_ratio = garbled_ratio(text)
    inv_ratio = p.invisible_text_objects / p.text_objects if p.text_objects else 0.0
    long_mm = max(p.width, p.height) / 72 * 25.4
    large = long_mm >= DRAW_LONG_MM
    fmt = p.width > p.height * 1.05 or large
    heavy_vector = p.path_count >= MANY_PATHS or not p.paths_counted or p.content_bytes >= DRAW_BYTES_MIN
    punct = sum(1 for c in text if c in PROSE_PUNCT)
    punct_density = punct * 100 / cjk if cjk else 0.0
    prose = cjk >= PROSE_MIN_CJK and punct_density >= PROSE_PUNCT_PER_100

    strong_region = _distinct_hits(p.region_text, TITLE_BLOCK_STRONG)
    strong_all = _distinct_hits(text, TITLE_BLOCK_STRONG)
    weak_all = _distinct_hits(text, TITLE_BLOCK_WEAK)

    signals: list[str] = []
    score = 0
    if strong_region >= 2:
        score += 2
        signals.append(f"图签区域强特征词×{strong_region}")
    if strong_all >= 3 or (strong_all >= 2 and weak_all >= 3):
        score += 1
        signals.append(f"全页图签词 强{strong_all}/弱{weak_all}")
    if fmt:
        score += 1
        signals.append("横向或大图幅")
    if p.path_count >= MANY_PATHS or not p.paths_counted:
        score += 1
        signals.append(f"矢量路径×{p.path_count}" if p.paths_counted else "内容流过大，跳过路径计数")
    elif large and p.content_bytes >= DRAW_BYTES_MIN:
        score += 1
        signals.append(f"大图幅+内容流 {p.content_bytes:,} 字节")
    if prose and not fmt:
        score -= 2
        signals.append(f"正文句读密度 {punct_density:.1f}/百字")

    full_image = p.image_coverage >= SCAN_IMAGE_COVERAGE
    if chars <= BLANK_MAX_CHARS and p.image_coverage < 0.05:
        cls = "vector_only" if heavy_vector else "blank"
    elif full_image and chars < MIN_TEXT_CHARS:
        cls = "scan"
    elif full_image and inv_ratio >= OCR_LAYER_INVISIBLE_RATIO:
        cls = "scan_ocr_layer"
    elif full_image and body_chars < MIN_TEXT_CHARS:
        cls = "scan"
        signals.append("文字层只有跨页重复的水印/页眉，按扫描页处理")
    elif chars >= MIN_TEXT_CHARS and g_ratio >= GARBLED_RATIO:
        cls = "garbled"
    elif score >= DRAWING_SCORE:
        cls = "drawing"
    elif chars >= MIN_TEXT_CHARS:
        cls = "text"
        if full_image:
            signals.append("整页背景图但文字可见，按文本页处理")
    elif heavy_vector:
        cls = "vector_only"
    elif chars == 0:
        cls = "scan" if p.image_coverage >= 0.2 else "blank"
    else:
        # 有原生文字但字少、且不是整页图：PPT 标题页、图题页等。文字直接抽取，
        # 插图列入 figures 另行识别，不必整页 OCR。
        cls = "text"

    figures: list[list[float]] = []
    if cls == "text":
        figures = [list(b) for b in p.image_boxes if b[4] >= FIGURE_MIN_AREA]
        if not figures and p.image_coverage >= FIGURE_MIN_AREA and not p.image_boxes:
            figures = [[0.0, 0.0, round(p.width, 1), round(p.height, 1), round(p.image_coverage, 3)]]
        if figures:
            signals.append(f"含 {len(figures)} 张大图，图中文字需另行识别")

    return PageVerdict(
        page=p.index + 1, cls=cls, chars=chars, cjk=cjk,
        garbled_ratio=round(g_ratio, 3), image_coverage=round(p.image_coverage, 3),
        invisible_ratio=round(inv_ratio, 3), path_count=p.path_count,
        landscape_or_large=fmt, title_block_score=score, signals=signals,
        long_side_mm=round(long_mm, 1), content_bytes=p.content_bytes, figures=figures,
    )


def sample_indices(total: int, sample: int | None) -> list[int]:
    """均匀采样（而非只看前几页：封面、目录页常导致误判）。sample=0 表示全量。"""
    if total <= 0:
        return []
    if sample is None:
        sample = 0 if total <= AUTO_FULL_SCAN_PAGES else DEFAULT_SAMPLE
    if sample <= 0 or sample >= total:
        return list(range(total))
    step = (total - 1) / (sample - 1) if sample > 1 else 0
    return sorted({round(i * step) for i in range(sample)})


def build_segments(verdicts: list[PageVerdict], total_pages: int, full_scan: bool) -> list[dict]:
    """把连续同引擎的页合并为页段；空白页并入前一页段。仅在全量扫描时页段精确。"""
    segs: list[dict] = []
    for v in verdicts:
        engine = ENGINE_BY_CLASS[v.cls]
        if engine is None and segs:
            segs[-1]["end"] = v.page
            continue
        if segs and segs[-1]["engine"] == engine:
            segs[-1]["end"] = v.page
        else:
            segs.append({"start": v.page, "end": v.page, "engine": engine})
    if segs and segs[0]["engine"] is None and len(segs) > 1:
        segs[1]["start"] = segs[0]["start"]
        segs.pop(0)
    if not full_scan and segs:  # 采样模式：页段只是近似，延伸到相邻采样点之间
        segs[-1]["end"] = total_pages
        for a, b in zip(segs, segs[1:], strict=False):
            a["end"] = b["start"] - 1
    return segs


def inspect_pdf(
    pdf_path: str,
    sample_pages: int | None = None,
    backend: str | None = None,
    include_pages: bool = False,
) -> dict[str, Any]:
    """探测 PDF 特征并推荐转换引擎。返回值向后兼容 v1 的主要字段。"""
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"未找到文件: {pdf_path}")

    doc = open_pdf(pdf_path, backend)
    try:
        total = doc.page_count
        indices = sample_indices(total, sample_pages)
        raws = [doc.page(i) for i in indices]
        backend_name = doc.backend
    finally:
        doc.close()

    repeated = repeated_lines([r.text or "" for r in raws])
    verdicts = [classify_page(r, repeated) for r in raws]

    full_scan = len(indices) == total
    counts = Counter(v.cls for v in verdicts)
    content = [v for v in verdicts if v.cls != "blank"]
    engine_counts = Counter(ENGINE_BY_CLASS[v.cls] for v in content)
    warnings: list[str] = []

    if not content:
        doc_type, engine = "blank", None
    else:
        dominant_engine, _ = engine_counts.most_common(1)[0]
        minority = sum(n for e, n in engine_counts.items() if e != dominant_engine)
        if minority and (minority / len(content) >= 0.1 or minority >= 3):
            doc_type, engine = "mixed", "split"
        else:
            cls_for_engine = Counter(v.cls for v in content if ENGINE_BY_CLASS[v.cls] == dominant_engine)
            top_cls = cls_for_engine.most_common(1)[0][0]
            doc_type = {
                "text": "text_native", "scan": "scan_or_image", "scan_ocr_layer": "scan_with_ocr_layer",
                "garbled": "garbled_text_layer", "drawing": "engineering_drawing",
                "vector_only": "engineering_drawing",
            }[top_cls]
            engine = dominant_engine
            if minority:
                warnings.append(f"有 {minority} 个采样页类型与主类型不同，可用 --pages 查看明细。")

    if doc_type == "mixed" and not full_scan:
        warnings.append("采样模式下页段边界为估计值，精确拆分请加 --sample 0 全量扫描。")
    if counts.get("scan_ocr_layer"):
        warnings.append("存在带隐形 OCR 文字层的扫描页：该层的条文编号、尺寸、标准号错误率高，正式入库须重新识别，"
                        "并强制 OCR（MinerU 云 API is_ocr=true / 本地 OCR 方法），否则引擎可能沿用这层文字。")
    figure_pages = [v.page for v in verdicts if v.figures]
    if figure_pages:
        warnings.append(f"{len(figure_pages)} 个文本页含大图，图中文字 fast 模式抽不到，"
                        "需另行识别（见 figure_pages / pages[].figures）。")

    n = max(len(verdicts), 1)
    avg_chars = sum(v.chars for v in verdicts) / n
    avg_cjk = sum(v.cjk for v in verdicts) / n

    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "file": os.path.basename(pdf_path),
        "path": os.path.abspath(pdf_path),
        "backend": backend_name,
        "total_pages": total,
        "sampled_pages": len(indices),
        "full_scan": full_scan,
        "has_text_layer": counts.get("text", 0) + counts.get("drawing", 0) > 0,
        "avg_chars_per_page": round(avg_chars, 1),
        "avg_chinese_chars_per_page": round(avg_cjk, 1),
        "is_drawing_like": doc_type == "engineering_drawing",
        "page_classes": dict(counts),
        "recommended_engine": engine,
        "document_type": doc_type,
        "reason": REASONS[doc_type],
        "warnings": warnings,
    }
    if figure_pages:
        result["figure_pages"] = figure_pages
    if doc_type == "mixed":
        result["segments"] = build_segments(verdicts, total, full_scan)
    if include_pages:
        result["pages"] = [asdict(v) for v in verdicts]
    return result


# ------------------------------------------------------------------ CLI
def _iter_pdf_paths(inputs: list[str], recursive: bool) -> Iterable[str]:
    for item in inputs:
        if os.path.isdir(item):
            walker = os.walk(item) if recursive else [(item, [], os.listdir(item))]
            for root, _dirs, files in walker:
                for name in sorted(files):
                    if name.lower().endswith(".pdf") and not name.startswith("._"):
                        yield os.path.join(root, name)
        else:
            yield item


def _table_row(r: dict) -> str:
    if "error" in r:
        return f"{'ERROR':<24} {'-':>5}  {r['file']}  ({r['error']})"
    return f"{str(r['recommended_engine']):<24} {r['total_pages']:>5}  {r['file']}  [{r['document_type']}]"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="中文工程 PDF 结构探测与转换引擎路由推荐")
    ap.add_argument("inputs", nargs="+", help="PDF 文件或目录")
    ap.add_argument("--sample", type=int, default=None,
                    help=f"均匀采样页数；0=全量扫描；默认 ≤{AUTO_FULL_SCAN_PAGES} 页全量，否则采样 {DEFAULT_SAMPLE} 页")
    ap.add_argument("--pages", action="store_true", help="输出逐页判定明细")
    ap.add_argument("--recursive", "-r", action="store_true", help="递归遍历目录")
    ap.add_argument("--format", choices=["json", "jsonl", "table"], default=None,
                    help="输出格式：单文件默认 json，多文件默认 jsonl")
    ap.add_argument("--backend", choices=["pymupdf", "pypdfium2"], default=None, help="强制指定 PDF 后端")
    args = ap.parse_args(argv)
    ensure_utf8_stdio()

    paths = list(_iter_pdf_paths(args.inputs, args.recursive))
    if not paths:
        print("未找到 PDF 文件", file=sys.stderr)
        return 1
    fmt = args.format or ("json" if len(paths) == 1 else "jsonl")

    failed = 0
    for path in paths:
        try:
            result = inspect_pdf(path, args.sample, args.backend, include_pages=args.pages)
        except BackendUnavailable as e:
            print(f"错误: {e}", file=sys.stderr)
            return 1
        except (EncryptedPDF, FileNotFoundError, RuntimeError) as e:
            failed += 1
            result = {"file": os.path.basename(path), "path": os.path.abspath(path), "error": str(e)}
        except Exception as e:  # noqa: BLE001 - 批量模式下单个坏文件不应中断
            failed += 1
            result = {"file": os.path.basename(path), "path": os.path.abspath(path),
                      "error": f"{type(e).__name__}: {e}"}

        if fmt == "json":
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif fmt == "jsonl":
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(_table_row(result))
        sys.stdout.flush()

    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
