#!/usr/bin/env python3
"""
add_frontmatter.py - 为转换产出的 Markdown 写入溯源 Frontmatter（坑 11）

写入字段：title / source_file / source_sha256 / source_pages / engine /
document_type / converted_at / chinese_chars / pipeline。
已有 Frontmatter 时合并：本工具管理的字段被更新，其他字段（如你在 Obsidian 里加的 tags）原样保留。

用法：
    python tools/add_frontmatter.py out.md --source 原文.pdf --engine opendataloader-fast --in-place
    python tools/classify.py 原文.pdf > c.json
    python tools/add_frontmatter.py out.md --source 原文.pdf --classify-json c.json --in-place
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from _compat import ensure_utf8_stdio

CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0002fa1f]")
FM_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
MANAGED_KEYS = ["title", "source_file", "source_sha256", "source_pages", "engine",
                "document_type", "converted_at", "chinese_chars", "pipeline"]
PIPELINE = "engineering-pdf-markdown-pipeline"


def _yaml_scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def split_frontmatter(text: str) -> tuple[list[str], str]:
    """返回 (原 frontmatter 行列表, 正文)。只做行级处理，不依赖 PyYAML，保证未知字段原样保留。"""
    m = FM_RE.match(text.replace("\r\n", "\n"))
    if not m:
        return [], text
    return m.group(1).split("\n"), text.replace("\r\n", "\n")[m.end():]


def _strip_managed(lines: list[str]) -> list[str]:
    """移除本工具管理的顶层键（含其缩进的续行）。"""
    out, skipping = [], False
    for line in lines:
        key = re.match(r"^([A-Za-z_][\w-]*):", line)
        if key:
            skipping = key.group(1) in MANAGED_KEYS
        elif not line.startswith((" ", "\t", "-")):
            skipping = False
        if not skipping:
            out.append(line)
    return out


def guess_title(body: str, fallback: str) -> str:
    m = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    return m.group(1).strip() if m else fallback


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_frontmatter(text: str, source: str | None, engine: str | None, classify: dict | None,
                      now: datetime | None = None) -> str:
    existing, body = split_frontmatter(text)
    fields: dict = {}
    stem = os.path.splitext(os.path.basename(source))[0] if source else "untitled"
    fields["title"] = guess_title(body, stem)
    if source:
        fields["source_file"] = os.path.basename(source)
        if os.path.exists(source):
            fields["source_sha256"] = sha256_file(source)
    if classify:
        if classify.get("total_pages") is not None:
            fields["source_pages"] = classify["total_pages"]
        if classify.get("document_type"):
            fields["document_type"] = classify["document_type"]
        if not engine and classify.get("recommended_engine") not in (None, "split"):
            engine = classify["recommended_engine"]
    if engine:
        fields["engine"] = engine
    fields["converted_at"] = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    fields["chinese_chars"] = len(CJK_RE.findall(body))
    fields["pipeline"] = PIPELINE

    kept = [ln for ln in _strip_managed(existing) if ln.strip()]
    lines = [f"{k}: {_yaml_scalar(fields[k])}" for k in MANAGED_KEYS if k in fields] + kept
    return "---\n" + "\n".join(lines) + "\n---\n\n" + body.lstrip("\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="为 Markdown 写入溯源 Frontmatter")
    ap.add_argument("markdown")
    ap.add_argument("--source", help="原始 PDF 路径（用于文件名与 SHA-256）")
    ap.add_argument("--engine", help="实际使用的转换引擎")
    ap.add_argument("--classify-json", help="classify.py 的输出（取页数、文档类型、推荐引擎）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--in-place", action="store_true", help="直接改写输入文件")
    g.add_argument("-o", "--output", help="输出文件（默认标准输出）")
    args = ap.parse_args(argv)
    ensure_utf8_stdio()

    try:
        text = Path(args.markdown).read_text(encoding="utf-8")
        classify = json.loads(Path(args.classify_json).read_text(encoding="utf-8")) if args.classify_json else None
    except (OSError, json.JSONDecodeError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    result = build_frontmatter(text, args.source, args.engine, classify)
    target = args.markdown if args.in_place else args.output
    if target:
        with open(target, "w", encoding="utf-8") as f:
            f.write(result)
    else:
        sys.stdout.write(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
