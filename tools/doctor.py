#!/usr/bin/env python3
"""
doctor.py - 环境自检（Windows / macOS / Linux 通用）

换一台机器、换一个 Python 环境后先跑一遍，确认这条流水线在本机"实际"能用什么，
而不是照搬别的机器上的结论。只读检查，不安装、不联网、不打印任何密钥。

    python tools/doctor.py
    python tools/doctor.py --json
    python tools/doctor.py --strict     # 关键项不满足时退出码为 1，便于脚本/Agent 判断

检查项：Python 与编码模式、PDF 后端、OpenDataLoader（含 Java 版本）、MinerU CLI、qpdf、
keyring 与 MinerU token（只报告是否存在与长度）、配置文件与白名单目录是否存在。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import locale
import os
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


def _java_candidates(env: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """候选 java：JAVA_HOME > PATH > macOS 的 /usr/libexec/java_home。

    macOS 的 /usr/bin/java 只是占位程序：没装 JDK、或 JDK 未注册（如 Homebrew 的 keg-only openjdk）时
    它只会提示安装，解析不出版本。很多工具（包括 opendataloader-pdf）会因此静默退化。
    """
    env = os.environ if env is None else env
    exe = "java.exe" if sys.platform.startswith("win") else "java"
    out: list[tuple[str, str]] = []
    if env.get("JAVA_HOME"):
        out.append((os.path.join(env["JAVA_HOME"], "bin", exe), "JAVA_HOME"))
    found = shutil.which("java")
    if found:
        out.append((found, "PATH"))
    if sys.platform == "darwin" and os.path.exists("/usr/libexec/java_home"):
        code, home = run_text(["/usr/libexec/java_home"])
        if code == 0 and home.strip():
            out.append((os.path.join(home.strip().splitlines()[-1], "bin", "java"), "/usr/libexec/java_home"))
    return out


def find_java(env: dict[str, str] | None = None) -> dict[str, Any]:
    tried = []
    for path, source in _java_candidates(env):
        if not os.path.exists(path):
            tried.append(f"{source}: {path} 不存在")
            continue
        _, out = run_text([path, "-version"])
        major = java_major(out)
        if major:
            return {"path": path, "major": major, "source": source, "tried": tried}
        note = "（macOS 占位程序，未找到可用 JDK）" if path == "/usr/bin/java" else "（解析不出版本）"
        tried.append(f"{source}: {path}{note}")
    return {"path": None, "major": None, "source": None, "tried": tried}


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

    r["java"] = find_java()

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
    r["cloud_denied_dirs"] = [
        {"dir": d, "exists": Path(d).expanduser().is_dir()} for d in cfg.get("cloud_denied_dirs") or []
    ]
    r["cloud_denied_patterns"] = list(cfg.get("cloud_denied_patterns") or [])

    try:
        from mineru_cloud import DEFAULTS, resolve_token

        tok, src = resolve_token({**DEFAULTS, **(cfg.get("mineru") or {})})
        r["mineru_token"] = {"found": bool(tok), "length": len(tok) if tok else 0, "source": src}
    except Exception as e:  # noqa: BLE001
        r["mineru_token"] = {"found": False, "error": type(e).__name__}
    return r


def problems(r: dict[str, Any]) -> list[str]:
    """--strict 时视为失败的关键项。"""
    out = []
    if not any(r["pdf_backends"].values()):
        out.append("没有可用的 PDF 后端")
    if r["config"].get("error"):
        out.append(f"配置文件无法读取：{r['config']['error']}")
    if r["opendataloader_pdf"] and (r["java"]["major"] or 0) < 11:
        out.append("opendataloader-pdf 需要 Java 11+，当前未找到")
    out += [f"白名单目录不存在：{d['dir']}" for d in r["cloud_allowed_dirs"] if not d["exists"]]
    return out


def advice(r: dict[str, Any]) -> list[str]:
    tips = []
    if not any(r["pdf_backends"].values()):
        tips.append("没有 PDF 后端：pip install pymupdf（或许可证敏感时 pip install pypdfium2）")
    if r["opendataloader_pdf"] and (r["java"]["major"] or 0) < 11:
        tips.append("已装 opendataloader-pdf，但 Java 低于 11（或未找到），fast 模式跑不起来；"
                    "JDK 不在 PATH 上时请设置 JAVA_HOME")
    if not r["commands"]["mineru"] and not r["mineru_token"]["found"]:
        tips.append("既没有 MinerU CLI 也没有云 API token：扫描件只能用 MinerU 图形版手工处理")
    if r["mineru_token"]["found"] and not r["cloud_allowed_dirs"]:
        tips.append("有云 API token 但未配置 cloud_allowed_dirs：mineru_cloud.py 会拒绝上传")
    if r["cloud_allowed_dirs"] and not (r["cloud_denied_dirs"] or r["cloud_denied_patterns"]):
        tips.append("未配置排除名单：公开资料目录里若夹着企业标准、院内发文等内部文件，"
                    "请用 cloud_denied_patterns（如 \"*企业标准*\"）或 cloud_denied_dirs 挡住")
    for d in r["cloud_allowed_dirs"]:
        if not d["exists"]:
            tips.append(f"白名单目录不存在：{d['dir']}")
    if sys.platform.startswith("win") and r["utf8_mode"]:
        tips.append("当前处于 UTF-8 模式（PYTHONUTF8=1）：调用 Windows 原生命令时注意其输出仍是 GBK（坑 19）")
    return tips


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PDF 流水线环境自检")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="关键项不满足时以退出码 1 结束")
    args = ap.parse_args(argv)
    ensure_utf8_stdio()

    r = collect()
    r["advice"] = advice(r)
    r["problems"] = problems(r)
    code = 1 if args.strict and r["problems"] else 0
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return code

    ok = lambda v: "✓" if v else "✗"  # noqa: E731
    print(f"系统      {r['platform']}")
    print(f"Python    {r['python']}  ({r['python_executable']})")
    print(f"编码      utf8_mode={r['utf8_mode']}  preferred={r['preferred_encoding']}")
    for k, v in r["pdf_backends"].items():
        if k != "fitz":
            print(f"{k:<10}{ok(v)} {v or ''}")
    j = r["java"]
    print(f"ODL fast  {ok(r['opendataloader_pdf'])} {r['opendataloader_pdf'] or ''}  "
          f"java={j['major'] or '未找到'}" + (f"（{j['source']}: {j['path']}）" if j["path"] else ""))
    for t in j.get("tried", []):
        print(f"            跳过 {t}")
    for c, p in r["commands"].items():
        print(f"{c:<10}{ok(p)} {p or ''}")
    print(f"keyring   {ok(r['keyring'])} {r['keyring'] or ''}")
    t = r["mineru_token"]
    print(f"MinerU token {ok(t['found'])} {'长度 ' + str(t['length']) if t['found'] else ''}  {t.get('source', '')}")
    print(f"配置      {r['config']['path']}  {'存在' if r['config']['exists'] else '不存在'}")
    for d in r["cloud_allowed_dirs"]:
        print(f"  白名单  {ok(d['exists'])} {d['dir']}")
    for d in r["cloud_denied_dirs"]:
        print(f"  排除    {'·' if d['exists'] else '?'} {d['dir']}")
    for pat in r["cloud_denied_patterns"]:
        print(f"  排除    {pat}")
    if r["problems"]:
        print("\n问题：")
        for x in r["problems"]:
            print(f"  ✗ {x}")
    if r["advice"]:
        print("\n建议：")
        for tip in r["advice"]:
            print(f"  - {tip}")
    return code


if __name__ == "__main__":
    sys.exit(main())
