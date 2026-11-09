"""
_compat.py - Windows / macOS / Linux 共用的小工具（只依赖标准库）

- ensure_utf8_stdio()：输出被重定向到文件/管道时统一用 UTF-8。
  中文 Windows 上重定向输出默认按 cp936(GBK) 编码，下游按 UTF-8 读就是乱码；
  扩展区汉字、㎡ 之外的符号还可能直接抛 UnicodeEncodeError。交互式控制台不受影响。
- config_dir() / load_config()：按平台惯例定位配置文件，不写死任何个人路径。
- run_text()：调用外部命令并按正确编码解码输出（坑 19）。
"""

from __future__ import annotations

import json
import locale
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

APP_NAME = "engineering-pdf-pipeline"
CONFIG_ENV = "PDF_PIPELINE_CONFIG"


def ensure_utf8_stdio() -> None:
    for name in ("stdout", "stderr", "stdin"):
        stream = getattr(sys, name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            if stream.isatty():
                continue
        except (ValueError, OSError):
            continue
        if (getattr(stream, "encoding", "") or "").lower().replace("-", "") != "utf8":
            stream.reconfigure(encoding="utf-8")


def config_dir(platform: str | None = None, env: dict[str, str] | None = None) -> Path:
    """Windows: %APPDATA%\\engineering-pdf-pipeline；macOS: ~/Library/Application Support/...；
    其他: $XDG_CONFIG_HOME/... 或 ~/.config/...。"""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    home = Path(env.get("HOME") or env.get("USERPROFILE") or Path.home())
    if platform.startswith("win"):
        base = Path(env["APPDATA"]) if env.get("APPDATA") else home / "AppData" / "Roaming"
    elif platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else home / ".config"
    return base / APP_NAME


def config_path() -> Path:
    override = os.environ.get(CONFIG_ENV)
    return Path(override).expanduser() if override else config_dir() / "config.json"


def load_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    """读取 JSON 配置（Python 3.10 没有 tomllib，用 JSON 保持零依赖）。文件不存在时返回空配置。"""
    p = Path(path).expanduser() if path else config_path()
    if not p.exists():
        return {}
    with open(p, encoding="utf-8-sig") as f:  # 兼容 Windows 记事本写入的 BOM
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"配置文件顶层必须是对象: {p}")
    return data


def console_encoding() -> str:
    """外部命令输出最可能使用的编码。Windows 原生命令（where、java -version 等）按系统 ANSI 代码页输出，
    即使 Python 处于 UTF-8 模式（PYTHONUTF8=1）也是如此。"""
    if sys.platform.startswith("win"):
        return "mbcs"
    return locale.getpreferredencoding(False) or "utf-8"


def run_text(cmd: list[str], timeout: float = 30) -> tuple[int, str]:
    """运行外部命令，返回 (退出码, stdout+stderr 文本)。

    不用 text=True：它按 Python 的首选编码解码，UTF-8 模式下解 GBK 字节会抛异常，
    而且异常发生在读取线程里，主流程照常往下走，很容易被误判成"没装"。
    """
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)
    raw = (p.stdout or b"") + (p.stderr or b"")
    for enc in ("utf-8", console_encoding()):
        try:
            return p.returncode, raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return p.returncode, raw.decode("utf-8", errors="replace")
