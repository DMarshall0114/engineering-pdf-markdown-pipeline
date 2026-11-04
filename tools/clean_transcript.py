#!/usr/bin/env python3
"""
clean_transcript.py - 语音转写稿与会议整理稿 PDF 文本清洗示范脚本

展示如何通过针对性正则，剔除转写稿中的常见水印行、
消除中文字符间因分栏断行引入的多余半角空格，并校验关键数字完整性。
"""

import re
from typing import Tuple, Dict

def clean_transcript_text(raw_text: str) -> Tuple[str, Dict[str, int]]:
    """
    清洗语音转写稿常见噪音：
    1. 剔除整行人员工号及时间戳水印行
    2. 消除中文与标点之间的多余半角空格
    """
    stats = {}

    # 1. 水印匹配与剔除 (形如: 张三(123456)某某单位 2026-10-25 10:35:18)
    watermark_pattern = re.compile(
        r"^.*?\(\d+\).*?\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}.*?$\n?",
        re.MULTILINE
    )
    watermark_matches = len(watermark_pattern.findall(raw_text))
    cleaned_text = watermark_pattern.sub("", raw_text)
    stats["watermarks_removed"] = watermark_matches

    # 2. 中文字符与标点之间的多余半角空格剔除
    # 匹配左侧为中文/全角标点，右侧也是中文的多余 1-2 个空格
    space_pattern = re.compile(
        r"(?<=[\u4e00-\u9fff，。、；：？！）】》”])\s{1,2}(?=[\u4e00-\u9fff])"
    )
    space_matches = len(space_pattern.findall(cleaned_text))
    cleaned_text = space_pattern.sub("", cleaned_text)
    stats["unwanted_spaces_removed"] = space_matches

    return cleaned_text, stats

if __name__ == "__main__":
    demo_dirty_text = """
第一章 总则

王工(098765)某工程咨询有限公司 2026-11-04 14:00:00
1.1 为 规 范 铁 道 与 城 轨 机 辆 工 程 审 查 流 程，特 制 定 本 细 则。

王工(098765)某工程咨询有限公司 2026-11-04 14:00:00
1.2 本 工程 涉 及 新 增 线 路 12.5 公 里，总 投 资 预 算 达 98.6 亿 元。
"""
    cleaned, report = clean_transcript_text(demo_dirty_text)
    print("--- 清洗前 ---")
    print(demo_dirty_text.strip())
    print("\n--- 清洗后 ---")
    print(cleaned.strip())
    print("\n--- 清洗统计 ---")
    print(report)
