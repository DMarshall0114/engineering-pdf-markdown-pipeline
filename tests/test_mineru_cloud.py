import io
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

import mineru_cloud as mc
from _compat import config_dir, run_text
from doctor import advice, collect, java_major


# ------------------------------------------------------------ 白名单
def test_whitelist_inside_and_outside(tmp_path):
    pub = tmp_path / "公开规范"
    pub.mkdir()
    (pub / "a.pdf").write_bytes(b"x")
    other = tmp_path / "项目"
    other.mkdir()
    (other / "b.pdf").write_bytes(b"x")
    assert mc.is_allowed(pub / "a.pdf", [str(pub)])
    assert not mc.is_allowed(other / "b.pdf", [str(pub)])
    assert not mc.is_allowed(pub / "a.pdf", [])


def test_whitelist_rejects_dotdot_escape(tmp_path):
    pub = tmp_path / "pub"
    pub.mkdir()
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"x")
    assert not mc.is_allowed(pub / ".." / "secret.pdf", [str(pub)])


def test_whitelist_prefix_is_not_parent(tmp_path):
    """/data/pub 不能放行 /data/public-but-not-really/x.pdf。"""
    (tmp_path / "pub").mkdir()
    sneaky = tmp_path / "pub2"
    sneaky.mkdir()
    (sneaky / "x.pdf").write_bytes(b"x")
    assert not mc.is_allowed(sneaky / "x.pdf", [str(tmp_path / "pub")])


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="仅 Windows 不区分大小写")
def test_whitelist_case_insensitive_on_windows(tmp_path):
    d = tmp_path / "Books"
    d.mkdir()
    (d / "a.pdf").write_bytes(b"x")
    assert mc.is_allowed(str(d / "a.pdf").upper(), [str(d).lower()])


# ------------------------------------------------------------ token / 参数
def test_token_from_env_never_echoed():
    tok, src = mc.resolve_token(mc.DEFAULTS, env={mc.TOKEN_ENV: "  sk-secret  "})
    assert tok == "sk-secret"
    assert "sk-secret" not in src and "sk-secret" not in mc.mask(tok)
    assert "9" in mc.mask(tok)


def test_ocr_forced_for_ocr_layer_and_garbled():
    on, why = mc.ocr_decision("auto", {"page_classes": {"scan_ocr_layer": 99}})
    assert on and "scan_ocr_layer" in why
    assert mc.ocr_decision("auto", {"page_classes": {"garbled": 1, "text": 9}})[0]
    assert not mc.ocr_decision("auto", {"page_classes": {"scan": 10}})[0]
    assert mc.ocr_decision("on", None)[0] and not mc.ocr_decision("off", {"page_classes": {"garbled": 1}})[0]


def test_page_ranges_only_for_mixed():
    mixed = {"document_type": "mixed", "segments": [
        {"start": 1, "end": 162, "engine": "opendataloader-fast"},
        {"start": 163, "end": 186, "engine": "mineru-ocr"},
        {"start": 187, "end": 187, "engine": "mineru-layout-or-vision"},
    ]}
    assert mc.page_ranges_from_classify(mixed) == "163-186,187"
    assert mc.page_ranges_from_classify({"document_type": "scan_or_image"}) is None
    assert mc.count_range_pages("163-186,187", 200) == 25
    assert mc.count_range_pages(None, 99) == 99
    assert mc.parse_ranges("2-3,5", 9) == {2, 3, 5}


def test_payload_puts_is_ocr_on_file_entry():
    p = mc.build_payload("规范.pdf", mc.DEFAULTS, True, "1-5", data_id="abc")
    assert p["files"][0] == {"name": "规范.pdf", "is_ocr": True, "data_id": "abc", "page_ranges": "1-5"}
    assert p["model_version"] == "vlm" and p["is_ocr"] is True


# ------------------------------------------------------------ 解压与质检
def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_safe_extract_blocks_traversal(tmp_path):
    with pytest.raises(mc.ApiError):
        mc.safe_extract(_zip({"../evil.txt": b"x"}), tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()


def test_safe_extract_and_find_outputs(tmp_path):
    files = mc.safe_extract(_zip({"full.md": "# 标题".encode(), "abc_content_list.json": b"[]",
                                  "images/1.jpg": b"\xff"}), tmp_path / "out")
    md, cl = mc.find_outputs(files)
    assert md.name == "full.md" and cl.name == "abc_content_list.json"


def test_completeness_flags_short_and_long_pages():
    layer = {1: 500, 2: 500, 3: 500, 4: 20}
    ocr = {1: 480, 2: 200, 3: 900}
    flagged = mc.completeness(layer, ocr)
    assert [f["page"] for f in flagged] == [2, 3]  # 第 4 页文字层太少不比对


def test_mineru_page_cjk_uses_page_idx():
    blocks = [{"type": "text", "text": "第一页正文", "page_idx": 0},
              {"type": "table", "table_body": "<td>表格</td>", "page_idx": 1},
              {"type": "image", "img_path": "x.jpg"}]
    assert mc.mineru_page_cjk(blocks) == {1: 5, 2: 2}


# ------------------------------------------------------------ CLI（不联网）
@pytest.fixture
def pdf_in(tmp_path):
    d = tmp_path / "pub"
    d.mkdir()
    pdf = d / "规范.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"cloud_allowed_dirs": [str(d)]}), encoding="utf-8")
    return pdf, cfg


def test_cli_refuses_without_whitelist(tmp_path, capsys):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF")
    cfg = tmp_path / "c.json"
    cfg.write_text("{}", encoding="utf-8")
    assert mc.main([str(pdf), "--config", str(cfg), "--dry-run"]) == mc.EXIT_REFUSED
    assert "拒绝上传" in capsys.readouterr().err


def test_cli_dry_run_prints_payload_without_token(pdf_in, capsys, monkeypatch):
    pdf, cfg = pdf_in
    monkeypatch.setenv(mc.TOKEN_ENV, "sk-should-not-appear")
    assert mc.main([str(pdf), "--config", str(cfg), "--dry-run", "--ocr", "on"]) == 0
    err = capsys.readouterr().err
    assert '"is_ocr": true' in err and "sk-should-not-appear" not in err


def test_cli_full_flow_with_fake_api(pdf_in, tmp_path, monkeypatch, capsys):
    pdf, cfg = pdf_in
    monkeypatch.setenv(mc.TOKEN_ENV, "sk-test")
    md = "## 3.2 节\n3.2.1 正文。\n3.2.2 正文。\n3.2.n 错误编号\n"
    zip_bytes = _zip({"full.md": md.encode(), "规范_content_list.json": b"[]"})
    calls = []
    states = iter(["waiting-file", "running", "done"])

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        calls.append((req.get_method(), req.full_url, dict(req.header_items())))
        if req.full_url.endswith("/file-urls/batch"):
            body = json.loads(req.data.decode())
            assert body["files"][0]["is_ocr"] is True
            return Resp(json.dumps({"code": 0, "data": {"batch_id": "b1", "file_urls": ["https://up/x?sig=1"]}})
                        .encode())
        if req.full_url.startswith("https://up/"):
            return Resp(b"")
        if "/extract-results/batch/b1" in req.full_url:
            st = next(states)
            item = {"state": st, "full_zip_url": "https://dl/r.zip" if st == "done" else None}
            return Resp(json.dumps({"code": 0, "data": {"extract_result": [item]}}).encode())
        if req.full_url == "https://dl/r.zip":
            return Resp(zip_bytes)
        raise AssertionError(req.full_url)

    monkeypatch.setattr(mc, "urlopen", fake_urlopen)
    monkeypatch.setattr(mc.time, "sleep", lambda s: None)
    out = tmp_path / "out"
    code = mc.main([str(pdf), "--config", str(cfg), "-o", str(out), "--ocr", "on",
                    "--forbid", "3.2.n", "--strict"])
    assert code == mc.EXIT_QA  # 禁用串命中 → 质检不通过

    put = next(c for c in calls if c[0] == "PUT")
    assert put[2].get("Content-type") == ""  # 预签名上传不能带默认表单类型
    assert all("Authorization" not in c[2] for c in calls if c[1].startswith("https://up/"))
    summary = json.loads((out / "规范" / "run_summary.json").read_text(encoding="utf-8"))
    assert summary["checks"]["forbidden_found"] == ["3.2.n"]
    assert summary["batch_id"] == "b1" and summary["payload"]["files"][0]["is_ocr"] is True
    assert "sk-test" not in json.dumps(summary)
    assert "sk-test" not in capsys.readouterr().err


def test_cli_api_error_exit_code(pdf_in, monkeypatch):
    pdf, cfg = pdf_in
    monkeypatch.setenv(mc.TOKEN_ENV, "sk-test")

    def boom(req, timeout=0):
        raise mc.urllib.error.URLError("unreachable")

    monkeypatch.setattr(mc, "urlopen", boom)
    assert mc.main([str(pdf), "--config", str(cfg), "-o", os.fspath(pdf.parent / "o")]) == mc.EXIT_API


# ------------------------------------------------------------ _compat
def test_config_dir_per_platform():
    app = "engineering-pdf-pipeline"
    assert config_dir("win32", {"APPDATA": "C:/Users/u/AppData/Roaming"}) == Path("C:/Users/u/AppData/Roaming") / app
    assert config_dir("darwin", {"HOME": "/Users/u"}) == Path("/Users/u/Library/Application Support") / app
    assert config_dir("linux", {"HOME": "/home/u"}) == Path("/home/u/.config") / app
    assert config_dir("linux", {"HOME": "/home/u", "XDG_CONFIG_HOME": "/cfg"}) == Path("/cfg") / app


def test_run_text_decodes_and_reports_missing_command():
    code, out = run_text([sys.executable, "-c", "import sys; sys.stdout.buffer.write('中文'.encode('utf-8'))"])
    assert code == 0 and "中文" in out
    code, _ = run_text(["definitely-not-a-real-command-xyz"])
    assert code == -1


def test_java_major_parsing():
    assert java_major('java version "1.8.0_491"') == 8
    assert java_major('openjdk version "21.0.2" 2024-01-16') == 21
    assert java_major("garbage") is None


def test_doctor_collect_runs_and_hides_token(monkeypatch):
    monkeypatch.setenv(mc.TOKEN_ENV, "sk-hidden-123")
    r = collect()
    assert r["mineru_token"]["found"] and r["mineru_token"]["length"] == len("sk-hidden-123")
    assert "sk-hidden-123" not in json.dumps(r, ensure_ascii=False)
    assert isinstance(advice(r), list)
