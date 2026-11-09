#!/usr/bin/env python3
"""
qa_check.py - 转换结果质量抽检：字数统计、OCR 形近字扫描、两版本数字比对

1. 字数统计：分别给出 UTF-8 字节数、字符数、汉字数（坑 6：wc -c 是字节不是字数）；
2. 形近字扫描（坑 2）：内置常见 OCR 误识词表，可用 --dict 追加 TSV（错词<TAB>正词）；
   "fix" 级条目可用 --fix 自动替换，"review" 级只报告、需人工确认；
3. 数字比对：--against 另一个版本（如 fast 抽取 vs OCR 结果、清洗前 vs 清洗后），
   报告缺失/多出的数字，用于核对工程量、里程、投资额等关键参数；
4. 禁用串（--forbid）：已知的错误写法不得出现。典型用法是把扫描件旧 OCR 层里
   核实过的错字（如 "3.2.n"、"Zm内"）列进来，验证重新识别没有沿用旧层（坑 17）；
5. 条文编号连续性（--clauses）：x.y.z 条文在每节内应从 1 起逐个递增，
   跳号、重号、乱序往往意味着 OCR 吞段或跨页串读（坑 2、坑 17）。

用法：
    python tools/qa_check.py out.md
    python tools/qa_check.py ocr.md --against fast.md
    python tools/qa_check.py ocr.md --fix -o ocr.fixed.md --dict my_terms.tsv
    python tools/qa_check.py out.md --json
    python tools/qa_check.py ocr.md --clauses --forbid "3.2.n" --forbid "Zm内" --strict

退出码：0 通过；1 参数/读写错误；4 发现问题且指定了 --strict。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from _compat import ensure_utf8_stdio

CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0002fa1f]")
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*%?")


@dataclass(frozen=True)
class Rule:
    pattern: str  # 正则
    replacement: str | None  # None 表示仅提示
    level: str  # "fix" | "review"
    note: str = ""


# 只收录"几乎不可能是正确写法"的误识；有歧义的放 review 级
BUILTIN_RULES: list[Rule] = [
    Rule("主娈电所", "主变电所", "fix", "娈/变 形近"),
    Rule("通信网终", "通信网络", "fix", "终/络 形近"),
    Rule("(?<![外流输迁])出人口", "出入口", "fix", "人/入 形近"),
    Rule(r"(?:(?<=^)|(?<=[\s\d.]))般规定", "一般规定", "fix", "丢失首字「一」"),
    Rule("混泥土", "混凝土", "fix", "常见错字"),
    Rule("混疑土", "混凝土", "fix", "疑/凝 形近"),
    Rule("抗震没防", "抗震设防", "fix", "没/设 形近"),
    Rule("设汁", "设计", "fix", "汁/计 形近"),
    Rule("检査", "检查", "fix", "日文字形「査」"),
    Rule("栓修", "检修", "fix", "栓/检 形近"),
    Rule("通凤", "通风", "fix", "凤/风 形近"),
    Rule("排凤", "排风", "fix", "凤/风 形近"),
    Rule("凤机", "风机", "fix", "凤/风 形近"),
    Rule("士建", "土建", "fix", "士/土 形近"),
    Rule("隧遒", "隧道", "fix", "遒/道 形近"),
    Rule("[己巳]经", "已经", "fix", "己/巳/已 形近"),
    Rule("第[lI|][章节条]", None, "review", "字母 l/I 误识为数字 1"),
    Rule(r"\d[Oo]\d|[Oo]\d{2,}", None, "review", "字母 O 混入数字"),
    Rule("[０-９]", None, "review", "全角数字，入库前建议统一为半角"),
    Rule("[。，；]{2,}", None, "review", "重复标点"),
    Rule("&(?:gt|lt|amp|quot);", None, "review", "HTML 实体残留（hybrid 模式常见）"),
]


def load_dict(path: str) -> list[Rule]:
    rules = []
    with open(path, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                raise ValueError(f"{path}:{ln} 需要「错词<TAB>正词」格式")
            rules.append(Rule(re.escape(parts[0]), parts[1], "fix", parts[2] if len(parts) > 2 else "用户词表"))
    return rules


def text_stats(text: str) -> dict:
    return {
        "utf8_bytes": len(text.encode("utf-8")),
        "chars": len(text),
        "chinese_chars": len(CJK_RE.findall(text)),
        "lines": text.count("\n") + (1 if text and not text.endswith("\n") else 0),
    }


def scan(text: str, rules: list[Rule], context: int = 8) -> list[dict]:
    hits = []
    lines = text.split("\n")
    for rule in rules:
        rx = re.compile(rule.pattern, re.MULTILINE)
        for ln, line in enumerate(lines, 1):
            for m in rx.finditer(line):
                s, e = max(0, m.start() - context), min(len(line), m.end() + context)
                hits.append({
                    "line": ln, "match": m.group(0), "suggest": rule.replacement,
                    "level": rule.level, "note": rule.note, "context": line[s:e],
                })
    return sorted(hits, key=lambda h: h["line"])


def apply_fixes(text: str, rules: list[Rule]) -> tuple[str, int]:
    total = 0
    for rule in rules:
        if rule.level == "fix" and rule.replacement is not None:
            text, n = re.subn(rule.pattern, rule.replacement, text, flags=re.MULTILINE)
            total += n
    return text, total


def scan_forbidden(text: str, needles: list[str], context: int = 8) -> list[dict]:
    hits = []
    for ln, line in enumerate(text.split("\n"), 1):
        for needle in needles:
            start = line.find(needle)
            while start >= 0:
                s, e = max(0, start - context), min(len(line), start + len(needle) + context)
                hits.append({"line": ln, "match": needle, "suggest": None, "level": "forbid",
                             "note": "禁用串（已知错误写法）", "context": line[s:e]})
                start = line.find(needle, start + 1)
    return hits


CLAUSE_LINE_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:[-*+]\s+)?(?:\*\*)?([1-9]\d?)\.(\d{1,2})\.(\d{1,3})(?:\*\*)?(?=[\s\u3000\u4e00-\u9fff]|$)"
)
EXPLANATION_RE = re.compile(r"^\s*(?:#{1,6}\s*)?条\s*文\s*说\s*明\s*$")
TOC_LINE_RE = re.compile(r"(?:…{2,}|·{3,}|\.{4,})\s*\(?\d*\)?\s*$")


def check_clauses(text: str) -> dict:
    """检查行首 x.y.z 条文编号是否在每节内从 1 起连续递增。

    "条文说明"之后编号会重新开始，分段检查；目录行（带引导线）和表格行不计。
    只是线索：规范本身偶尔也会跳号（如删除条文），断点需对照原页核实。
    """
    parts: list[list[tuple[int, tuple[int, int, int]]]] = [[]]
    for ln, line in enumerate(text.split("\n"), 1):
        if EXPLANATION_RE.match(line):
            parts.append([])
            continue
        if line.lstrip().startswith("|") or TOC_LINE_RE.search(line):
            continue
        m = CLAUSE_LINE_RE.match(line)
        if m:
            parts[-1].append((ln, (int(m.group(1)), int(m.group(2)), int(m.group(3)))))

    gaps: list[str] = []
    duplicates: list[str] = []
    out_of_order: list[str] = []
    total = 0
    first = last = None
    for part in parts:
        seen: dict[tuple[int, int], int] = {}
        used: set[tuple[int, int, int]] = set()
        for ln, num in part:
            total += 1
            first = first or num
            last = num
            key, z = num[:2], num[2]
            label = f"{num[0]}.{num[1]}.{z}"
            if num in used:
                duplicates.append(f"{label} (L{ln})")
                continue
            used.add(num)
            prev = seen.get(key, 0)
            if z < prev:
                out_of_order.append(f"{label} (L{ln})")
                continue
            for missing in range(prev + 1, z):
                gaps.append(f"{num[0]}.{num[1]}.{missing}")
            seen[key] = z

    def fmt(n: tuple[int, int, int] | None) -> str | None:
        return f"{n[0]}.{n[1]}.{n[2]}" if n else None

    return {"clauses": total, "first": fmt(first), "last": fmt(last), "gaps": gaps,
            "duplicates": duplicates, "out_of_order": out_of_order, "sections": len(parts)}


def compare_numbers(text: str, reference: str) -> dict:
    a, b = Counter(NUMBER_RE.findall(text)), Counter(NUMBER_RE.findall(reference))
    return {
        "missing_vs_reference": sorted((b - a).elements(), key=_num_key),
        "extra_vs_reference": sorted((a - b).elements(), key=_num_key),
        "numbers_in_text": sum(a.values()),
        "numbers_in_reference": sum(b.values()),
    }


def _num_key(s: str):
    try:
        return (0, float(s.rstrip("%").replace(",", "")), s)
    except ValueError:
        return (1, 0.0, s)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="转换结果质量抽检")
    ap.add_argument("input", help="待检查的 Markdown/文本（- 表示标准输入）")
    ap.add_argument("--against", metavar="REF", help="参照版本，用于数字比对")
    ap.add_argument("--dict", action="append", default=[], metavar="TSV", help="追加形近字词表（可重复）")
    ap.add_argument("--fix", action="store_true", help="自动替换 fix 级条目")
    ap.add_argument("-o", "--output", help="--fix 的输出文件（默认覆盖标准输出）")
    ap.add_argument("--forbid", action="append", default=[], metavar="TEXT",
                    help="不得出现的已知错误写法（字面匹配，可重复）")
    ap.add_argument("--clauses", action="store_true", help="检查 x.y.z 条文编号是否连续")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出报告")
    ap.add_argument("--strict", action="store_true", help="发现问题时以退出码 4 结束")
    args = ap.parse_args(argv)
    ensure_utf8_stdio()

    try:
        text = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
        rules = BUILTIN_RULES + [r for p in args.dict for r in load_dict(p)]
        ref = Path(args.against).read_text(encoding="utf-8") if args.against else None
    except (OSError, ValueError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    issues = scan(text, rules) + scan_forbidden(text, args.forbid)
    report: dict = {"stats": text_stats(text), "issues": sorted(issues, key=lambda h: h["line"])}
    if args.clauses:
        report["clauses"] = check_clauses(text)
    if ref is not None:
        report["numbers"] = compare_numbers(text, ref)

    if args.fix:
        fixed, n = apply_fixes(text, rules)
        report["fixed"] = n
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(fixed)
        else:
            sys.stdout.write(fixed)

    out = sys.stderr if args.fix and not args.output else sys.stdout
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2), file=out)
    else:
        st = report["stats"]
        print(f"字节 {st['utf8_bytes']:,} | 字符 {st['chars']:,} | 汉字 {st['chinese_chars']:,} | 行 {st['lines']:,}",
              file=out)
        for h in report["issues"]:
            tip = f" → {h['suggest']}" if h["suggest"] else ""
            print(f"  [{h['level']}] L{h['line']}: 「{h['match']}」{tip}  ({h['note']})  …{h['context']}…", file=out)
        if "numbers" in report:
            nb = report["numbers"]
            print(f"数字比对：本文 {nb['numbers_in_text']} 个 / 参照 {nb['numbers_in_reference']} 个", file=out)
            if nb["missing_vs_reference"]:
                print(f"  缺失：{nb['missing_vs_reference'][:50]}", file=out)
            if nb["extra_vs_reference"]:
                print(f"  多出：{nb['extra_vs_reference'][:50]}", file=out)
        if "clauses" in report:
            cl = report["clauses"]
            print(f"条文编号：{cl['clauses']} 条（{cl['first']} → {cl['last']}）", file=out)
            for key, label in (("gaps", "跳号"), ("duplicates", "重号"), ("out_of_order", "乱序")):
                if cl[key]:
                    print(f"  {label} {len(cl[key])} 处：{cl[key][:30]}", file=out)
        if "fixed" in report:
            print(f"已自动修正 {report['fixed']} 处", file=out)

    clause_problems = "clauses" in report and any(
        report["clauses"][k] for k in ("gaps", "duplicates", "out_of_order"))
    problems = bool(report["issues"]) or clause_problems or bool(
        ref is not None and (report["numbers"]["missing_vs_reference"] or report["numbers"]["extra_vs_reference"])
    )
    return 4 if args.strict and problems else 0


if __name__ == "__main__":
    sys.exit(main())
