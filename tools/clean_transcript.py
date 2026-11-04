#!/usr/bin/env python3
"""
clean_transcript.py - 语音转写稿 / 会议整理稿 PDF 抽取文本的清洗工具

处理内容：
1. 整行剔除「姓名(工号)单位 日期 时间」型水印行（兼容全角括号、日期分隔符），
   只匹配"整行就是水印"的短行，正文里出现编号和时间的句子不会被误删；
2. 消除中文字符之间因分栏排版引入的半角/全角空格（不跨行，保留"第一章 总则"这类标题空格）；
3. 可选：合并双栏硬换行（--reflow）、剔除反复出现的页眉页脚行（--drop-repeated）；
4. 数字完整性校验：比对清洗前后（扣除被删水印行）的数字序列，任何数字丢失都会报告。

用法：
    python tools/clean_transcript.py 原文.md -o 清洗后.md
    python tools/clean_transcript.py 原文.md -o out.md --reflow --report removed.txt
    cat 原文.txt | python tools/clean_transcript.py - > out.txt
    python tools/clean_transcript.py --demo

退出码：0 成功；1 参数/读写错误；3 数字完整性校验失败（配合 --strict）。
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

CJK = r"㐀-䶿一-鿿豈-﫿"
CJK_PUNCT = "，。、；：？！（）【】《》“”‘’…—"
HSPACE = r"[ \t 　]"

# 水印行：短行、整行只有「姓名/单位 + (工号) + 单位 + 日期时间」，不含句读标点
WATERMARK_RE = re.compile(
    rf"^{HSPACE}*[^\s，。；：！？]{{1,20}}[(（]\d{{3,}}[)）][^\n，。；！？]{{0,40}}?"
    rf"{HSPACE}*\d{{4}}[-/.年]\d{{1,2}}[-/.月]\d{{1,2}}日?{HSPACE}+\d{{1,2}}:\d{{2}}(?::\d{{2}})?{HSPACE}*$"
)
WATERMARK_MAX_LEN = 80

# 中文之间的多余空格（只匹配水平空白，绝不跨行）
SPACE_BETWEEN_CJK_RE = re.compile(rf"(?<=[{CJK}{CJK_PUNCT}]){HSPACE}{{1,3}}(?=[{CJK}{CJK_PUNCT}])")
# 章节标题前缀后面的空格需要保留："第一章 总则"、"附录A 名词解释"
HEADING_PREFIX_RE = re.compile(rf"^({HSPACE}*(?:第[一二三四五六七八九十百零〇\d]+[章节篇部]|附录[A-Z]?){HSPACE}+)")

# 硬换行合并的判定
SENTENCE_END = "。！？；：…”」』）】"
BLOCK_START_RE = re.compile(
    rf"^{HSPACE}*(?:#|[-*+]\s|>|\||\d+(?:\.\d+)*{HSPACE}|[（(]\d+[)）]|\d+[、.)）]|[一二三四五六七八九十]+、|第.{{1,6}}[章节条]|附录)"
)
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*%?")


@dataclass
class CleanReport:
    watermarks_removed: int = 0
    repeated_lines_removed: int = 0
    unwanted_spaces_removed: int = 0
    lines_reflowed: int = 0
    removed_lines: list[str] = field(default_factory=list)
    missing_numbers: list[str] = field(default_factory=list)
    extra_numbers: list[str] = field(default_factory=list)

    @property
    def numbers_ok(self) -> bool:
        return not self.missing_numbers and not self.extra_numbers

    def as_dict(self) -> dict:
        return {
            "watermarks_removed": self.watermarks_removed,
            "repeated_lines_removed": self.repeated_lines_removed,
            "unwanted_spaces_removed": self.unwanted_spaces_removed,
            "lines_reflowed": self.lines_reflowed,
            "numbers_ok": self.numbers_ok,
            "missing_numbers": self.missing_numbers,
            "extra_numbers": self.extra_numbers,
        }


def is_watermark_line(line: str, extra: list[re.Pattern] | None = None) -> bool:
    s = line.rstrip("\r\n")
    if not s.strip():
        return False
    if extra and any(p.search(s) for p in extra):
        return True
    return len(s) <= WATERMARK_MAX_LEN and bool(WATERMARK_RE.match(s))


def remove_cjk_spaces(line: str) -> tuple[str, int]:
    m = HEADING_PREFIX_RE.match(line)
    head, body = (m.group(1), line[m.end():]) if m else ("", line)
    body, n = SPACE_BETWEEN_CJK_RE.subn("", body)
    return head + body, n


_CJK_END_RE = re.compile(rf"[{CJK}，、]$")
_CJK_START_RE = re.compile(rf"^{HSPACE}*[{CJK}]")
_SHORT_HEADING_MAX = 12  # 编号后的标题文字长度上限，如"一般规定""车辆基地"


def _can_join(prev: str, line: str) -> bool:
    p, cur = prev.rstrip(), line.strip()
    if not p or not cur:
        return False
    if p.lstrip().startswith(("#", "|", ">")):  # 标题、表格、引用不续接
        return False
    if not _CJK_END_RE.search(p) or p[-1] in SENTENCE_END:
        return False
    if not _CJK_START_RE.match(line) or BLOCK_START_RE.match(line):  # 下一行是新条目
        return False
    # 上一行是"1 总则""第三章 车辆基地"这类无标点短标题，不续接
    m = BLOCK_START_RE.match(p)
    if m and len(p[m.end():].strip()) <= _SHORT_HEADING_MAX and not re.search("[，。；：]", p):
        return False
    return True


def reflow(lines: list[str]) -> tuple[list[str], int]:
    """合并被排版宽度截断的中文行：上一行以汉字结尾且非句末、下一行以汉字开头且不是新条目。"""
    out: list[str] = []
    merged = 0
    in_fence = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        if not in_fence and out and _can_join(out[-1], line):
            out[-1] = out[-1].rstrip() + line.strip()
            merged += 1
        else:
            out.append(line)
    return out, merged


def clean_transcript_text(
    raw_text: str,
    *,
    reflow_lines: bool = False,
    drop_repeated: int = 0,
    extra_watermarks: list[str] | None = None,
) -> tuple[str, dict]:
    """
    清洗文本并返回 (清洗后文本, 统计字典)。保持与 v1 相同的返回形态，
    统计字典新增 numbers_ok / missing_numbers 等字段。
    """
    text, report = clean_text(raw_text, reflow_lines=reflow_lines, drop_repeated=drop_repeated,
                              extra_watermarks=extra_watermarks)
    return text, report.as_dict()


def clean_text(
    raw_text: str,
    *,
    reflow_lines: bool = False,
    drop_repeated: int = 0,
    extra_watermarks: list[str] | None = None,
) -> tuple[str, CleanReport]:
    rep = CleanReport()
    extra = [re.compile(p) for p in (extra_watermarks or [])]
    lines = raw_text.replace("\r\n", "\n").split("\n")

    # 1. 水印行
    kept: list[str] = []
    for line in lines:
        if is_watermark_line(line, extra):
            rep.watermarks_removed += 1
            rep.removed_lines.append(line)
        else:
            kept.append(line)

    # 2. 反复出现的页眉页脚（可选）
    if drop_repeated > 1:
        freq = Counter(s.strip() for s in kept if s.strip() and len(s.strip()) <= 40)
        noisy = {s for s, n in freq.items() if n >= drop_repeated}
        new_kept = []
        for line in kept:
            if line.strip() in noisy:
                rep.repeated_lines_removed += 1
                rep.removed_lines.append(line)
            else:
                new_kept.append(line)
        kept = new_kept

    baseline = "\n".join(kept)  # 数字校验基准：只扣除被主动删除的行

    # 3. 中文间多余空格
    spaced: list[str] = []
    for line in kept:
        new, n = remove_cjk_spaces(line)
        rep.unwanted_spaces_removed += n
        spaced.append(new)

    # 4. 硬换行合并（可选）
    if reflow_lines:
        spaced, rep.lines_reflowed = reflow(spaced)

    cleaned = "\n".join(spaced)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned) if rep.watermarks_removed or rep.repeated_lines_removed else cleaned

    before = Counter(NUMBER_RE.findall(baseline))
    after = Counter(NUMBER_RE.findall(cleaned))
    rep.missing_numbers = sorted((before - after).elements())
    rep.extra_numbers = sorted((after - before).elements())
    return cleaned, rep


DEMO = """
第一章 总则

王工(098765)某工程咨询有限公司 2026-11-04 14:00:00
1.1 为 规 范 铁 道 与 城 轨 机 辆 工 程 审 查 流 程，特 制 定 本 细 则。

王工（098765）某工程咨询有限公司 2026/11/04 14:00
1.2 本 工程 涉 及 新 增 线 路 12.5 公 里，总 投 资 预 算 达 98.6 亿 元。
(1) 2026-11-04 14:00:00 召开评审会，确定车站规模为 12 座。
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="转写稿/会议纪要抽取文本清洗")
    ap.add_argument("input", nargs="?", help="输入文件（- 表示标准输入）")
    ap.add_argument("-o", "--output", help="输出文件（默认标准输出）")
    ap.add_argument("--reflow", action="store_true", help="合并双栏排版造成的中文硬换行")
    ap.add_argument("--drop-repeated", type=int, default=0, metavar="N",
                    help="剔除出现 ≥N 次的短行（页眉页脚、保密提示），默认关闭")
    ap.add_argument("--watermark", action="append", default=[], metavar="REGEX",
                    help="追加自定义水印行正则（可重复）")
    ap.add_argument("--report", metavar="FILE", help="把被删除的行写入该文件，便于人工复核")
    ap.add_argument("--strict", action="store_true", help="数字完整性校验失败时以退出码 3 结束")
    ap.add_argument("--demo", action="store_true", help="运行内置示例")
    args = ap.parse_args(argv)

    if args.demo:
        cleaned, rep = clean_text(DEMO, reflow_lines=True)
        print("--- 清洗前 ---\n" + DEMO.strip() + "\n\n--- 清洗后 ---\n" + cleaned.strip())
        print("\n--- 清洗统计 ---\n", rep.as_dict())
        return 0
    if not args.input:
        ap.print_usage(sys.stderr)
        return 1

    try:
        raw = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
    except OSError as e:
        print(f"读取失败: {e}", file=sys.stderr)
        return 1

    cleaned, rep = clean_text(raw, reflow_lines=args.reflow, drop_repeated=args.drop_repeated,
                              extra_watermarks=args.watermark)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(cleaned)
    else:
        sys.stdout.write(cleaned)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write("\n".join(rep.removed_lines) + ("\n" if rep.removed_lines else ""))

    d = rep.as_dict()
    summary = (f"水印行 {d['watermarks_removed']} / 重复行 {d['repeated_lines_removed']} / "
               f"空格 {d['unwanted_spaces_removed']} / 合并换行 {d['lines_reflowed']}")
    print(f"[clean] {summary}", file=sys.stderr)
    if not rep.numbers_ok:
        print(f"[clean] ⚠ 数字完整性校验失败：缺失 {rep.missing_numbers[:20]} 多出 {rep.extra_numbers[:20]}",
              file=sys.stderr)
        return 3 if args.strict else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
