#!/usr/bin/env python3
"""
doctor.py - 环境自检（Windows / macOS / Linux 通用）

换一台机器、换一个 Python 环境后先跑一遍，确认这条流水线在本机"实际"能用什么，
而不是照搬别的机器上的结论。只读检查，不安装、不联网、不打印任何密钥。

    python tools/doctor.py
    python tools/doctor.py --json

检查项：Python 与编码模式、PDF 后端、OpenDataLoader（含 Java 版本）、MinerU CLI、qpdf、
keyring 与 MinerU token（只报告是否存在与长度）、配置文件与白名单目录是否存在。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import locale
import platform
import re
import shutil
import sys
from typing import Any

from _compat import config_path, ensure_utf8_stdio, load_config, run_text


def _module_version(name: str) -> str | None:
    if importlib.util.find_spec(name) is None:
        return None
    try:
        from importlib.metadata import PackageNotFoundError, version

        dist = {"fitz": "pymupdf", "opendataloader_pdf": "opendataloader-pdf"}.get(name, name)
        return version(dist)
    except PackageNotFoundError:
        return "installed"
    except Exception:  # noqa: BLE001
        return "installed"


def java_major(text: str) -> int | None:
    """从 `java -version` 输出解析主版本：1.8.0_xxx → 8，21.0.2 → 21。"""
    m = re.search(r'version "(\d+)(?:\.(\d+))?', text)
    if not m:
        return None
    major = int(m.group(1))
    return int(m.group(2) or 0) if major == 1 else major


def collect() -> dict[str, Any]:
    r: dict[str, Any] = {
        "platform": f"{platform.system()} {platform.release()} ({sys.platform})",
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "utf8_mode": bool(sys.flags.utf8_mode),
        "preferred_encoding": locale.getpreferredencoding(False),
    }
    r["pdf_backends"] = {m: _module_version(m) for m in ("pymupdf", "fitz", "pypdfium2")}
    r["opendataloader_pdf"] = _module_version("opendataloader_pdf")

    java = shutil.which("java")
    r["java"] = {"path": java, "major": None}
    if java:
        _, out = run_text([java, "-version"])
        r["java"]["major"] = java_major(out)

    r["commands"] = {c: shutil.which(c) for c in ("mineru", "qpdf")}

    kr = _module_version("keyring")
    r["keyring"] = kr
    cfg_file = config_path()
    try:
        cfg = load_config()
        r["config"] = {"path": str(cfg_file), "exists": cfg_file.exists()}
    except (OSError, ValueError) as e:
        cfg = {}
        r["config"] = {"path": str(cfg_file), "exists": cfg_file.exists(), "error": str(e)}

    from pathlib import Path

    r["cloud_allowed_dirs"] = [
        {"dir": d, "exists": Path(d).expanduser().is_dir()} for d in cfg.get("cloud_allowed_dirs") or []
    ]

    try:
        from mineru_cloud import DEFAULTS, resolve_token

        tok, src = resolve_token({**DEFAULTS, **(cfg.get("mineru") or {})})
        r["mineru_token"] = {"found": bool(tok), "length": len(tok) if tok else 0, "source": src}
    except Exception as e:  # noqa: BLE001
        r["mineru_token"] = {"found": False, "error": type(e).__name__}
    return r


def advice(r: dict[str, Any]) -> list[str]:
    tips = []
    if not any(r["pdf_backends"].values()):
        tips.append("没有 PDF 后端：pip install pymupdf（或许可证敏感时 pip install pypdfium2）")
    if r["opendataloader_pdf"] and (r["java"]["major"] or 0) < 11:
        tips.append("已装 opendataloader-pdf，但 Java 低于 11（或未找到），fast 模式跑不起来")
    if not r["commands"]["mineru"] and not r["mineru_token"]["found"]:
        tips.append("既没有 MinerU CLI 也没有云 API token：扫描件只能用 MinerU 图形版手工处理")
    if r["mineru_token"]["found"] and not r["cloud_allowed_dirs"]:
        tips.append("有云 API token 但未配置 cloud_allowed_dirs：mineru_cloud.py 会拒绝上传")
    for d in r["cloud_allowed_dirs"]:
        if not d["exists"]:
            tips.append(f"白名单目录不存在：{d['dir']}")
    if sys.platform.startswith("win") and r["utf8_mode"]:
        tips.append("当前处于 UTF-8 模式（PYTHONUTF8=1）：调用 Windows 原生命令时注意其输出仍是 GBK（坑 19）")
    return tips


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PDF 流水线环境自检")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    ensure_utf8_stdio()

    r = collect()
    r["advice"] = advice(r)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0

    ok = lambda v: "✓" if v else "✗"  # noqa: E731
    print(f"系统      {r['platform']}")
    print(f"Python    {r['python']}  ({r['python_executable']})")
    print(f"编码      utf8_mode={r['utf8_mode']}  preferred={r['preferred_encoding']}")
    for k, v in r["pdf_backends"].items():
        if k != "fitz":
            print(f"{k:<10}{ok(v)} {v or ''}")
    print(f"ODL fast  {ok(r['opendataloader_pdf'])} {r['opendataloader_pdf'] or ''}  "
          f"java={r['java']['major'] or '未找到'}")
    for c, p in r["commands"].items():
        print(f"{c:<10}{ok(p)} {p or ''}")
    print(f"keyring   {ok(r['keyring'])} {r['keyring'] or ''}")
    t = r["mineru_token"]
    print(f"MinerU token {ok(t['found'])} {'长度 ' + str(t['length']) if t['found'] else ''}  {t.get('source', '')}")
    print(f"配置      {r['config']['path']}  {'存在' if r['config']['exists'] else '不存在'}")
    for d in r["cloud_allowed_dirs"]:
        print(f"  白名单  {ok(d['exists'])} {d['dir']}")
    if r["advice"]:
        print("\n建议：")
        for tip in r["advice"]:
            print(f"  - {tip}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
