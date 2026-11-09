#!/usr/bin/env python3
"""
rebuild_headings.py - 按中文规范条文编号重建 Markdown 标题层级

快速流式抽取工具遇到多级编号时常把层级拍平成 "- " 列表或普通段落
（docs/pitfalls.md 坑 5）。本工具按 GB/T 1.1 及工程建设标准常见编号规则重建：

    第一章 总则 / 1 总则 / 附录A 名词解释     → 章（默认 ##）
    第一节 / 3.1 一般规定 / A.1 一般规定      → 节（默认 ###）
    3.1.2 车辆基地的设计应……                 → 条文：保持为段落，去掉误加的列表符号，编号加粗（可关）
    3.1.2 检修库（无句读的短文本）            → 小节标题（默认 ####）

目录页（带"……"或"····"引导线的行）、代码块、表格不会被改动。

用法：
    python tools/rebuild_headings.py in.md -o out.md
    python tools/rebuild_headings.py in.md --base-level 1 --no-bold-clause
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from _compat import ensure_utf8_stdio

ZH_NUM = "一二三四五六七八九十百零〇"
HS_CHARS = " \t\u3000"
HS = f"[{HS_CHARS}]"
MAX_TITLE_LEN = 20  # 标题文字长度上限，超过视为条文正文

PREFIX_RE = re.compile(r"^(?:#{1,6}\s+|[-*+]\s+)?")
TOC_LEADER_RE = re.compile(r"(?:…{2,}|·{3,}|\.{4,}|-{4,})\s*\d*\s*$")
SENTENCE_PUNCT_RE = re.compile(r"[，。；：！？]")

CHAPTER_RE = re.compile(
    rf"^(第[{ZH_NUM}\d]+[章篇部]|附录[{HS_CHARS}]*[A-Z](?=[{HS_CHARS}\u4e00-\u9fff])|\d{{1,2}}(?=[{HS_CHARS}]))"
    rf"{HS}+(.+)$"
)
SECTION_RE = re.compile(rf"^(第[{ZH_NUM}\d]+节|(?:\d{{1,2}}|[A-Z])\.\d{{1,2}})(?:{HS}+)(.+)$")
CLAUSE_RE = re.compile(rf"^((?:\d{{1,2}}|[A-Z])\.\d{{1,2}}\.\d{{1,3}}(?:\.\d{{1,3}})?)(?:{HS}+)(.*)$")


def _is_title(text: str) -> bool:
    t = text.strip()
    return 0 < len(t) <= MAX_TITLE_LEN and not SENTENCE_PUNCT_RE.search(t)


class _State:
    """记录已识别的阿拉伯数字章号：裸数字章标题必须按 1、2、3… 递增，
    以免把条文内的款项（"1 电压等级"）误升为章标题。"""

    def __init__(self) -> None:
        self.last_chapter = 0


def rebuild_line(line: str, base_level: int = 2, bold_clause: bool = True, state: _State | None = None) -> str:
    raw = line.rstrip("\n")
    stripped = raw.strip()
    if not stripped or stripped.startswith(("|", ">", "!", "<")) or TOC_LEADER_RE.search(stripped):
        return raw
    body = PREFIX_RE.sub("", stripped, count=1).strip()
    body = re.sub(r"^\*\*(.+?)\*\*", r"\1", body)  # 已有的加粗编号先剥掉，统一重建

    m = CLAUSE_RE.match(body)
    if m:
        num, text = m.groups()
        if _is_title(text):
            return f"{'#' * min(base_level + 2, 6)} {num} {text.strip()}"
        return f"**{num}** {text.strip()}" if bold_clause else f"{num} {text.strip()}"

    m = SECTION_RE.match(body)
    if m and _is_title(m.group(2)):
        return f"{'#' * min(base_level + 1, 6)} {m.group(1)} {m.group(2).strip()}"

    m = CHAPTER_RE.match(body)
    if m and _is_title(m.group(2)):
        num = re.sub(rf"附录{HS}*", "附录", m.group(1))
        if num.isdigit() and state is not None:
            if int(num) != state.last_chapter + 1:
                return raw
            state.last_chapter = int(num)
        return f"{'#' * base_level} {num} {m.group(2).strip()}"

    return raw


def rebuild_headings(text: str, base_level: int = 2, bold_clause: bool = True) -> tuple[str, int]:
    out, changed, in_fence = [], 0, False
    state = _State()
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        new = line if in_fence else rebuild_line(line, base_level, bold_clause, state)
        changed += new != line
        out.append(new)
    return "\n".join(out), changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="按条文编号重建 Markdown 标题层级")
    ap.add_argument("input", help="输入 Markdown（- 表示标准输入）")
    ap.add_argument("-o", "--output", help="输出文件（默认标准输出）")
    ap.add_argument("--base-level", type=int, default=2, choices=range(1, 5),
                    help="章对应的标题级别，默认 2（文档标题留给 #）")
    ap.add_argument("--no-bold-clause", action="store_true", help="条文编号不加粗")
    args = ap.parse_args(argv)
    ensure_utf8_stdio()

    try:
        src = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
    except OSError as e:
        print(f"读取失败: {e}", file=sys.stderr)
        return 1
    result, n = rebuild_headings(src, args.base_level, not args.no_bold_clause)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result)
    else:
        sys.stdout.write(result)
    print(f"[headings] 改写 {n} 行", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
