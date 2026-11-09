#!/usr/bin/env python3
"""
mineru_cloud.py - 通过 MinerU 云 API 解析扫描件 / 带 OCR 层的扫描件，并做结果质检

只依赖标准库，Windows / macOS / Linux 通用。流程：

    白名单校验 → 读取 token → POST /file-urls/batch → PUT 上传原文件 → 轮询
    → 下载结果 zip（防路径穿越解压）→ 禁用串 / 条文连续性 / 逐页字数完整性检查 → run_summary.json

几条经过实测的规矩（详见 docs/pitfalls.md 坑 12、16~23）：
- 只上传白名单目录里的文件（配置 cloud_allowed_dirs）。未配置白名单时一律拒绝，
  确需上传白名单外的文件，必须显式加 --allow-outside-whitelist。
- 扫描件自带的隐形 OCR 层错误集中在条文编号、尺寸、标准号上；重新识别时必须强制 OCR
  （is_ocr=true），否则引擎可能沿用那层文字。--ocr auto 会根据 classify 结果自动打开。
- 整本一次提交，不要逐页提交：排队开销按次计，逐页提交慢两个数量级。
- token 从环境变量 MINERU_API_TOKEN 或系统钥匙串（macOS Keychain / Windows 凭据管理器 /
  Linux Secret Service，需要 pip install keyring）读取，从不打印、从不写入任何文件。

用法：
    python tools/mineru_cloud.py 规范.pdf -o out/ --dry-run
    python tools/mineru_cloud.py 规范.pdf -o out/ --classify-json c.json
    python tools/mineru_cloud.py 规范.pdf -o out/ --ocr on --forbid "3.2.n" --forbid "Zm内" --strict

API 字段以 MinerU 官方文档为准（https://mineru.net/apiManage/docs），可在配置中改 api_base。

退出码：0 成功；1 参数/配置错误；3 API 或网络错误；4 质检未通过（--strict）；5 白名单拒绝上传。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _compat import ensure_utf8_stdio, load_config

DEFAULTS: dict[str, Any] = {
    "api_base": "https://mineru.net/api/v4",
    "model_version": "vlm",
    "language": "ch",
    "enable_formula": True,
    "enable_table": True,
    "max_pages_per_file": 200,  # 以官方文档为准；超过时请按 classify 的 segments 拆分后分别提交
    "poll_interval": 10,
    "timeout": 1800,
    "keyring_service": "mineru-api",
    "keyring_user": "token",
}
TOKEN_ENV = "MINERU_API_TOKEN"
OCR_CLASSES = {"scan", "scan_ocr_layer", "garbled"}  # 这些页的现有文字层不可信或不存在
FORCE_OCR_CLASSES = {"scan_ocr_layer", "garbled"}  # 有一层"看似可用"的文字，必须强制 OCR 才不会被沿用
COMPLETENESS_LOW, COMPLETENESS_HIGH = 0.8, 1.2
COMPLETENESS_MIN_CJK = 50

EXIT_OK, EXIT_ARGS, EXIT_API, EXIT_QA, EXIT_REFUSED = 0, 1, 3, 4, 5


class ApiError(RuntimeError):
    pass


# ------------------------------------------------------------------ 白名单
def _norm(p: str | os.PathLike) -> str:
    return os.path.normcase(os.path.realpath(os.path.expanduser(str(p))))


def is_allowed(path: str | os.PathLike, allowed_dirs: list[str]) -> bool:
    """文件是否位于某个白名单目录内（解析符号链接与 .. ，Windows 下不区分大小写）。"""
    target = _norm(path)
    for d in allowed_dirs:
        base = _norm(d)
        try:
            if os.path.commonpath([target, base]) == base:
                return True
        except ValueError:  # Windows 不同盘符
            continue
    return False


# ------------------------------------------------------------------ token
def resolve_token(cfg: dict[str, Any], env: dict[str, str] | None = None) -> tuple[str | None, str]:
    """返回 (token, 来源描述)。来源描述里不含 token 本身。"""
    env = os.environ if env is None else env
    if env.get(TOKEN_ENV):
        return env[TOKEN_ENV].strip(), f"环境变量 {TOKEN_ENV}"
    try:
        import keyring  # type: ignore
    except ImportError:
        return None, "未设置环境变量，且未安装 keyring"
    service, user = cfg["keyring_service"], cfg["keyring_user"]
    try:
        tok = keyring.get_password(service, user)
    except Exception as e:  # noqa: BLE001 - 钥匙串后端报错时给出可读原因
        return None, f"钥匙串读取失败: {type(e).__name__}"
    return (tok.strip(), f"系统钥匙串 {service}/{user}") if tok else (None, f"钥匙串中无 {service}/{user}")


def mask(token: str | None) -> str:
    return f"已读取（{len(token)} 位）" if token else "未找到"


# ------------------------------------------------------------------ 提交参数
def ocr_decision(mode: str, classify: dict | None) -> tuple[bool, str]:
    if mode == "on":
        return True, "命令行指定 --ocr on"
    if mode == "off":
        return False, "命令行指定 --ocr off"
    classes = set((classify or {}).get("page_classes", {}))
    hit = sorted(classes & FORCE_OCR_CLASSES)
    if hit:
        return True, f"classify 发现 {', '.join(hit)} 页，强制 OCR 以免沿用旧文字层"
    if classify is None:
        return False, "未提供 classify 结果，由引擎自行判断（扫描件+OCR 层请用 --ocr on）"
    return False, "未发现可疑文字层，由引擎自行判断"


def page_ranges_from_classify(classify: dict | None) -> str | None:
    """mixed 文档只提交需要 OCR/版面识别的页段，例如 "163-186"；其余情况返回 None（整本）。"""
    if not classify or classify.get("document_type") != "mixed":
        return None
    ranges = [f"{s['start']}-{s['end']}" if s["start"] != s["end"] else str(s["start"])
              for s in classify.get("segments", []) if str(s.get("engine") or "").startswith("mineru")]
    return ",".join(ranges) or None


def count_range_pages(ranges: str | None, total: int | None) -> int | None:
    if not ranges:
        return total
    n = 0
    for part in ranges.split(","):
        a, _, b = part.partition("-")
        n += (int(b) if b else int(a)) - int(a) + 1
    return n


def build_payload(file_name: str, cfg: dict[str, Any], is_ocr: bool, page_ranges: str | None,
                  data_id: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": file_name, "is_ocr": is_ocr, "data_id": data_id}
    if page_ranges:
        entry["page_ranges"] = page_ranges
    return {
        "files": [entry],
        "model_version": cfg["model_version"],
        "language": cfg["language"],
        "enable_formula": bool(cfg["enable_formula"]),
        "enable_table": bool(cfg["enable_table"]),
        # 早期接口把 is_ocr 放在顶层；两处都写，兼容新旧版本
        "is_ocr": is_ocr,
    }


# ------------------------------------------------------------------ HTTP
def _http(method: str, url: str, *, token: str | None = None, body: bytes | None = None,
          json_body: Any = None, headers: dict[str, str] | None = None, timeout: float = 120,
          stream=None, length: int | None = None) -> bytes:
    hdrs = dict(headers or {})
    data: Any = body
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    if stream is not None:
        data = stream
        hdrs["Content-Length"] = str(length)
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode("utf-8", errors="replace")
        raise ApiError(f"{method} {_safe_url(url)} → HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ApiError(f"{method} {_safe_url(url)} 网络错误: {e}") from e


def _safe_url(url: str) -> str:
    return url.split("?", 1)[0]  # 预签名 URL 的查询串里带签名，不打印


urlopen: Callable[..., Any] = urllib.request.urlopen  # 测试时替换


def _api_json(raw: bytes, what: str) -> dict:
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ApiError(f"{what}: 返回的不是 JSON") from e
    if obj.get("code") not in (0, "0", None):
        raise ApiError(f"{what}: code={obj.get('code')} msg={obj.get('msg')}")
    return obj.get("data") or {}


def submit(pdf: Path, payload: dict, token: str, cfg: dict) -> str:
    data = _api_json(_http("POST", f"{cfg['api_base']}/file-urls/batch", token=token, json_body=payload),
                     "创建批次")
    batch_id, urls = data.get("batch_id"), data.get("file_urls") or []
    if not batch_id or not urls:
        raise ApiError("创建批次: 返回缺少 batch_id 或 file_urls")
    size = pdf.stat().st_size
    with open(pdf, "rb") as f:
        # 预签名上传地址按"无 Content-Type"签名；urllib 会给带数据的请求默认加表单类型，这里显式置空
        _http("PUT", urls[0], stream=f, length=size, headers={"Content-Type": ""}, timeout=max(120, size / 200_000))
    return batch_id


def poll(batch_id: str, token: str, cfg: dict, *, sleep: Callable[[float], None] = time.sleep,
         clock: Callable[[], float] = time.monotonic, log: Callable[[str], None] | None = None) -> dict:
    deadline = clock() + float(cfg["timeout"])
    last = None
    while True:
        data = _api_json(_http("GET", f"{cfg['api_base']}/extract-results/batch/{batch_id}", token=token),
                         "查询结果")
        results = data.get("extract_result") or []
        item = results[0] if results else {}
        state = item.get("state", "unknown")
        if log and state != last:
            prog = item.get("extract_progress") or {}
            extra = f" {prog.get('extracted_pages')}/{prog.get('total_pages')} 页" if prog else ""
            log(f"{datetime.now():%H:%M:%S} {state}{extra}")
            last = state
        if state == "done":
            if not item.get("full_zip_url"):
                raise ApiError("解析完成但没有返回结果下载地址")
            return item
        if state == "failed":
            raise ApiError(f"解析失败: {item.get('err_msg') or '未知原因'}")
        if clock() >= deadline:
            raise ApiError(f"等待超时（{cfg['timeout']} 秒），batch_id={batch_id}，可稍后用 --resume 继续")
        sleep(float(cfg["poll_interval"]))


def safe_extract(zip_bytes: bytes, dest: Path) -> list[Path]:
    """解压到 dest，拒绝绝对路径和 .. 穿越。"""
    import io

    dest = dest.resolve()
    out: list[Path] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            target = (dest / name).resolve()
            if name.startswith("/") or re.match(r"^[A-Za-z]:", name) or os.path.commonpath(
                    [str(target), str(dest)]) != str(dest):
                raise ApiError(f"结果压缩包含不安全路径: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as f:
                f.write(src.read())
            out.append(target)
    return out


def find_outputs(files: list[Path]) -> tuple[Path | None, Path | None]:
    md = next((p for p in files if p.name == "full.md"), None) or next((p for p in files if p.suffix == ".md"), None)
    cl = next((p for p in files if p.name.endswith("content_list.json")), None)
    return md, cl


# ------------------------------------------------------------------ 质检
_CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0002fa1f]")


def mineru_page_cjk(content_list: list[dict]) -> dict[int, int]:
    """content_list.json 中每个块带 page_idx（0-based），统计每页汉字数（1-based 页码）。"""
    counts: dict[int, int] = {}
    for block in content_list:
        if not isinstance(block, dict) or "page_idx" not in block:
            continue
        text = " ".join(str(block.get(k) or "") for k in ("text", "table_body", "table_caption", "image_caption"))
        page = int(block["page_idx"]) + 1
        counts[page] = counts.get(page, 0) + len(_CJK_RE.findall(text))
    return counts


def completeness(layer_cjk: dict[int, int], ocr_cjk: dict[int, int],
                 low: float = COMPLETENESS_LOW, high: float = COMPLETENESS_HIGH) -> list[dict]:
    """逐页比对：原 PDF 文字层（如旧 OCR 层，正文汉字通常准确）与新识别结果的汉字数。
    比值过低多为漏段，过高多为串页/重复。文字层太少的页不比对。"""
    flagged = []
    for page, ref in sorted(layer_cjk.items()):
        if ref < COMPLETENESS_MIN_CJK:
            continue
        got = ocr_cjk.get(page, 0)
        ratio = got / ref
        if ratio < low or ratio > high:
            flagged.append({"page": page, "layer_cjk": ref, "ocr_cjk": got, "ratio": round(ratio, 2)})
    return flagged


def layer_cjk_by_page(pdf: Path, pages: set[int] | None = None) -> dict[int, int]:
    try:
        from _pdfbackend import open_pdf
    except ImportError:
        return {}
    try:
        doc = open_pdf(str(pdf))
    except Exception:  # noqa: BLE001 - 无后端或打不开时跳过此项检查
        return {}
    try:
        out = {}
        for i in range(doc.page_count):
            if pages is None or (i + 1) in pages:
                out[i + 1] = len(_CJK_RE.findall(doc.page(i).text or ""))
        return out
    finally:
        doc.close()


def pdf_page_count(pdf: Path) -> int | None:
    try:
        from _pdfbackend import open_pdf

        doc = open_pdf(str(pdf))
    except Exception:  # noqa: BLE001
        return None
    try:
        return doc.page_count
    finally:
        doc.close()


def parse_ranges(ranges: str | None, total: int) -> set[int]:
    if not ranges:
        return set(range(1, total + 1))
    pages: set[int] = set()
    for part in ranges.split(","):
        a, _, b = part.partition("-")
        pages.update(range(int(a), int(b or a) + 1))
    return pages


# ------------------------------------------------------------------ CLI
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="MinerU 云 API 解析 + 质检（白名单、强制 OCR、完整性校验）")
    ap.add_argument("pdf", help="要解析的 PDF")
    ap.add_argument("-o", "--output", help="输出目录（默认 配置 output_dir 或 ./out），结果放在 <输出目录>/<文件名>/")
    ap.add_argument("--config", help="配置文件路径（默认按平台惯例，见 docs/cross-platform.md）")
    ap.add_argument("--classify-json", help="classify.py 的输出：决定是否强制 OCR、mixed 文档只提交 OCR 页段")
    ap.add_argument("--ocr", choices=["auto", "on", "off"], default="auto", help="是否强制 OCR，默认 auto")
    ap.add_argument("--page-ranges", help="手工指定页段，如 1-20,35（1-based），覆盖 classify 的结果")
    ap.add_argument("--forbid", action="append", default=[], metavar="TEXT", help="结果中不得出现的已知错误写法")
    ap.add_argument("--no-completeness", action="store_true", help="跳过逐页字数完整性比对")
    ap.add_argument("--allow-outside-whitelist", action="store_true",
                    help="明确允许上传白名单外的文件（自行确认不涉密后再用）")
    ap.add_argument("--resume", metavar="BATCH_ID", help="不重新提交，继续等待已有批次")
    ap.add_argument("--dry-run", action="store_true", help="只做校验并打印提交参数，不联网")
    ap.add_argument("--strict", action="store_true", help="质检发现问题时以退出码 4 结束")
    args = ap.parse_args(argv)
    ensure_utf8_stdio()
    log = lambda msg: print(msg, file=sys.stderr, flush=True)  # noqa: E731

    try:
        user_cfg = load_config(args.config)
        cfg = {**DEFAULTS, **(user_cfg.get("mineru") or {})}
        classify = json.loads(Path(args.classify_json).read_text(encoding="utf-8")) if args.classify_json else None
    except (OSError, ValueError) as e:
        log(f"错误: {e}")
        return EXIT_ARGS

    pdf = Path(args.pdf)
    if not pdf.is_file():
        log(f"错误: 未找到文件 {pdf}")
        return EXIT_ARGS

    allowed = [str(d) for d in user_cfg.get("cloud_allowed_dirs") or []]
    in_whitelist = is_allowed(pdf, allowed)
    if not in_whitelist and not args.allow_outside_whitelist:
        why = "未配置 cloud_allowed_dirs" if not allowed else "文件不在 cloud_allowed_dirs 内"
        log(f"拒绝上传：{why}。云 API 只用于公开资料（规范、出版物）；项目文件请走本地引擎。\n"
            f"确认文件可以外传后，可加 --allow-outside-whitelist。")
        return EXIT_REFUSED

    is_ocr, ocr_reason = ocr_decision(args.ocr, classify)
    ranges = args.page_ranges or page_ranges_from_classify(classify)
    total = pdf_page_count(pdf)
    submit_pages = count_range_pages(ranges, total)
    if submit_pages and submit_pages > int(cfg["max_pages_per_file"]):
        log(f"错误: 本次提交 {submit_pages} 页，超过单文件上限 {cfg['max_pages_per_file']} 页；"
            "请按页段拆分后分别提交（--page-ranges 或先用 qpdf 拆分）。")
        return EXIT_ARGS

    sha = _sha256(pdf)
    payload = build_payload(pdf.name, cfg, is_ocr, ranges, data_id=sha[:16])
    token, token_src = resolve_token(cfg)
    log(f"文件: {pdf.name}（{total if total is not None else '?'} 页，本次提交 {submit_pages or '?'} 页）")
    log(f"白名单: {'是' if in_whitelist else '否（已显式允许）'} | token: {mask(token)}，来源 {token_src}")
    log(f"强制 OCR: {is_ocr}（{ocr_reason}）")
    log("提交参数: " + json.dumps(payload, ensure_ascii=False))

    if args.dry_run:
        return EXIT_OK
    if not token:
        log(f"错误: 未找到 MinerU API token。设置环境变量 {TOKEN_ENV}，或 pip install keyring 后运行：\n"
            f"  python -c \"import keyring,getpass; keyring.set_password('{cfg['keyring_service']}',"
            f"'{cfg['keyring_user']}',getpass.getpass('MinerU token: '))\"")
        return EXIT_ARGS

    out_root = Path(args.output or user_cfg.get("output_dir") or "out").expanduser()
    dest = out_root / pdf.stem
    t0 = time.monotonic()
    try:
        batch_id = args.resume or submit(pdf, payload, token, cfg)
        log(f"batch_id={batch_id}")
        item = poll(batch_id, token, cfg, log=log)
        files = safe_extract(_http("GET", item["full_zip_url"], timeout=600), dest)
    except ApiError as e:
        log(f"错误: {e}")
        return EXIT_API
    elapsed = round(time.monotonic() - t0, 1)

    md_path, cl_path = find_outputs(files)
    summary: dict[str, Any] = {
        "source_file": pdf.name,
        "source_sha256": sha,
        "pages_total": total,
        "pages_submitted": submit_pages,
        "page_ranges": ranges,
        "batch_id": batch_id,
        "payload": payload,
        "ocr_reason": ocr_reason,
        "in_whitelist": in_whitelist,
        "elapsed_seconds": elapsed,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "markdown": str(md_path.relative_to(dest)) if md_path else None,
        "checks": {},
    }
    problems = md_path is None
    if md_path:
        text = md_path.read_text(encoding="utf-8")
        from qa_check import check_clauses, scan_forbidden

        forbidden = scan_forbidden(text, args.forbid)
        clauses = check_clauses(text)
        summary["checks"]["forbidden_found"] = sorted({h["match"] for h in forbidden})
        summary["checks"]["clauses"] = clauses
        problems |= bool(forbidden) or bool(clauses["gaps"] or clauses["duplicates"] or clauses["out_of_order"])
    if cl_path and not args.no_completeness and total:
        layer = layer_cjk_by_page(pdf, parse_ranges(ranges, total))
        if layer and any(v >= COMPLETENESS_MIN_CJK for v in layer.values()):
            content = json.loads(cl_path.read_text(encoding="utf-8"))
            flagged = completeness(layer, mineru_page_cjk(content))
            summary["checks"]["completeness_flagged_pages"] = flagged
            problems |= bool(flagged)
        else:
            summary["checks"]["completeness_flagged_pages"] = "跳过：原 PDF 无可比对的文字层"

    (dest / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"完成，用时 {elapsed} 秒 → {dest}")
    chk = summary["checks"]
    if chk.get("forbidden_found"):
        log(f"  禁用串出现: {chk['forbidden_found']}（可能沿用了旧文字层）")
    cl = chk.get("clauses")
    if cl:
        log(f"  条文编号 {cl['clauses']} 条，跳号 {len(cl['gaps'])} / 重号 {len(cl['duplicates'])} / "
            f"乱序 {len(cl['out_of_order'])}")
    if isinstance(chk.get("completeness_flagged_pages"), list) and chk["completeness_flagged_pages"]:
        log(f"  字数异常页: {[x['page'] for x in chk['completeness_flagged_pages']]}（渲染原页核对）")
    return EXIT_QA if args.strict and problems else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
