#!/usr/bin/env python3
"""
classify.py - 轻量 PDF 结构探测与转换引擎路由推荐工具

用于在执行耗时的 PDF 转 Markdown 任务前，先探查文档的文本层密度、
工程图纸特征（会签栏/图签）及页面分布，推荐最佳处理引擎。
"""

import sys
import os
import json
import re
from typing import Dict, Any, List

try:
    import fitz  # PyMuPDF
except ImportError:
    print("错误: 缺少 PyMuPDF 依赖，请运行: pip install pymupdf", file=sys.stderr)
    sys.exit(1)

# 工程图签/会签栏常见关键词特征
TITLE_BLOCK_KEYWORDS = [
    "设计", "校核", "审核", "审定", "工程名称", "图号", 
    "比例", "日期", "图签", "阶段", "专业", "标段"
]

def count_chinese_chars(text: str) -> int:
    """准确统计文本中的中文字符数（Unicode 4e00-9fff）"""
    return sum(1 for c in text if '\u4e00' <= c <= '\u9fff')

def inspect_pdf(pdf_path: str, sample_pages: int = 5) -> Dict[str, Any]:
    """探测 PDF 特征并推荐转换引擎"""
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"未找到文件: {pdf_path}")

    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    
    pages_to_sample = min(total_pages, sample_pages)
    char_counts: List[int] = []
    chinese_counts: List[int] = []
    title_block_hits = 0

    for i in range(pages_to_sample):
        page = doc[i]
        text = page.get_text() or ""
        char_len = len(text.strip())
        zh_count = count_chinese_chars(text)
        
        char_counts.append(char_len)
        chinese_counts.append(zh_count)

        # 检查是否高度集中在会签栏词汇
        hits = sum(1 for kw in TITLE_BLOCK_KEYWORDS if kw in text)
        if hits >= 4:
            title_block_hits += 1

    doc.close()

    avg_chars = sum(char_counts) / max(1, len(char_counts))
    avg_chinese = sum(chinese_counts) / max(1, len(chinese_counts))
    has_text_layer = avg_chars > 50

    # 判定是否为图签型工程图纸 (文字量有数百字但基本全是图框会签栏)
    is_drawing_like = (title_block_hits >= (pages_to_sample // 2 + 1)) and (avg_chars < 1800)

    # 决策路由
    if not has_text_layer:
        engine = "mineru-ocr"
        mode = "scan_or_image"
        reason = "未检测到有效内嵌文本层（可能为纯扫描件或图片型 PDF），必须走 OCR/版面模型。"
    elif is_drawing_like:
        engine = "mineru-layout-or-vision"
        mode = "engineering_drawing"
        reason = "检测到文本高度集中在图签/会签栏，图面主体疑似矢量或光栅图纸，建议走版面分析或 Vision 兜底。"
    else:
        engine = "opendataloader-fast"
        mode = "text_native"
        reason = "检测到稳定且高密度的内嵌文本层，推荐走纯文本流式抽取（秒级处理且杜绝 OCR 误字）。"

    return {
        "file": os.path.basename(pdf_path),
        "total_pages": total_pages,
        "sampled_pages": pages_to_sample,
        "has_text_layer": has_text_layer,
        "avg_chars_per_page": round(avg_chars, 1),
        "avg_chinese_chars_per_page": round(avg_chinese, 1),
        "is_drawing_like": is_drawing_like,
        "recommended_engine": engine,
        "document_type": mode,
        "reason": reason
    }

def main():
    if len(sys.argv) < 2:
        print("用法: python classify.py <path_to_pdf>")
        sys.exit(1)

    pdf_file = sys.argv[1]
    try:
        result = inspect_pdf(pdf_file)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"检测失败: {e}", file=sys.stderr)
        sys.exit(2)

if __name__ == "__main__":
    main()
